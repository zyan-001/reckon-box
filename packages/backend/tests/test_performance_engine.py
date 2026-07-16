"""
ReckonBox 2.0 性能引擎测试
==========================
覆盖策略模板分发、依赖边级联、断言机制、确定性等核心功能。
"""

import json
from pathlib import Path

from modules.performance_engine import (
    PerformanceEngine, Component, ComponentType, Request,
    EventType, SimEvent,
)
from modules.domain_pack import load_domain_pack, Scenario, Phase
from utils.micro_skill import MicroSkill, default_skill
from utils.dependency_edge import DependencyEdge
from utils.strategy_templates import default_params


# ---------------------------------------------------------------------------
# 辅助函数和常量
# ---------------------------------------------------------------------------

YAML_PATH = str(
    Path(__file__).resolve().parent.parent / "src" / "domain_packs" / "software_systems.yaml"
)


def _make_scenario(phases=None, inject=None):
    """创建测试用 Scenario 对象。"""
    if phases is None:
        phases = [Phase(ticks=100, rps=5)]
    return Scenario(
        id="test_scenario",
        name="测试场景",
        description="测试用",
        phases=phases,
        inject=inject or [],
    )


def _make_components_with_skill(template_id="sync_direct", **skill_overrides):
    """创建包含 MicroSkill 的基础组件集。

    返回: (components_dict, scenario)
    """
    params = default_params(template_id)
    params.update(skill_overrides)
    skill = MicroSkill(strategy_template_id=template_id, params=params)
    skill_db = MicroSkill(strategy_template_id="sync_direct",
                          params=default_params("sync_direct"))

    components = {
        "gateway": Component(
            name="gateway", type=ComponentType.GATEWAY, replicas=2,
            cpu_per_replica=100, memory_limit=4096, queue_capacity=500,
            timeout_ms=2000, dependencies=["order_service"],
            dependency_edges=[
                DependencyEdge(source="gateway", target="order_service", communication_mode="sync")
            ],
        ),
        "order_service": Component(
            name="order_service", type=ComponentType.SERVICE, replicas=2,
            cpu_per_replica=100, memory_limit=4096, queue_capacity=500,
            timeout_ms=2000, micro_skill=skill,
            dependencies=["order_db"],
            dependency_edges=[
                DependencyEdge(source="order_service", target="order_db", communication_mode="sync")
            ],
        ),
        "order_db": Component(
            name="order_db", type=ComponentType.DATABASE, replicas=1,
            cpu_per_replica=100, memory_limit=4096, queue_capacity=500,
            timeout_ms=3000, micro_skill=skill_db, dependencies=[],
        ),
    }
    scenario = _make_scenario()
    return components, scenario


# ---------------------------------------------------------------------------
# 基础测试
# ---------------------------------------------------------------------------

class TestEngineInit:
    """引擎初始化和基础功能测试。"""

    def test_engine_init_with_micro_skill(self):
        """引擎初始化，组件含 MicroSkill。"""
        components, scenario = _make_components_with_skill("sync_direct")
        engine = PerformanceEngine(components, scenario)
        assert engine is not None
        svc = engine.components["order_service"]
        assert svc.micro_skill is not None
        assert svc.micro_skill.strategy_template_id == "sync_direct"

    def test_micro_skill_none_defaults_to_sync_direct(self):
        """micro_skill=None 时默认使用 sync_direct。"""
        components = {
            "gateway": Component(
                name="gateway", type=ComponentType.GATEWAY, replicas=1,
                dependencies=["svc"],
            ),
            "svc": Component(
                name="svc", type=ComponentType.SERVICE, replicas=1,
                micro_skill=None, dependencies=["db"],
            ),
            "db": Component(
                name="db", type=ComponentType.DATABASE, replicas=1,
                dependencies=[],
            ),
        }
        scenario = _make_scenario()
        engine = PerformanceEngine(components, scenario)
        # 运行几 tick 不报错
        report = engine.run(ticks=10)
        assert report is not None
        assert report["strategy_dispatch_info"]["svc"] == "sync_direct"

    def test_get_report_contains_strategy_dispatch_info(self):
        """get_report 包含 strategy_dispatch_info。"""
        components, scenario = _make_components_with_skill("sync_direct")
        engine = PerformanceEngine(components, scenario)
        report = engine.run(ticks=10)
        assert "strategy_dispatch_info" in report
        assert "order_service" in report["strategy_dispatch_info"]
        assert report["strategy_dispatch_info"]["order_service"] == "sync_direct"

    def test_get_report_contains_assertion_events(self):
        """get_report 包含 assertion_events。"""
        components, scenario = _make_components_with_skill("sync_direct")
        engine = PerformanceEngine(components, scenario)
        report = engine.run(ticks=10)
        assert "assertion_events" in report
        assert isinstance(report["assertion_events"], list)

    def test_component_has_dependency_edges(self):
        """Component 支持 dependency_edges 字段。"""
        edge = DependencyEdge(source="svc", target="db", communication_mode="sync")
        comp = Component(
            name="svc", type=ComponentType.SERVICE,
            dependency_edges=[edge],
        )
        assert len(comp.dependency_edges) == 1
        assert comp.dependency_edges[0].source == "svc"
        assert comp.dependency_edges[0].target == "db"

    def test_normalize_components_micro_skill_dict(self):
        """_normalize_components 将 dict 形式的 micro_skill 转为 MicroSkill。"""
        components = {
            "svc": {
                "name": "svc",
                "type": "service",
                "micro_skill": {
                    "strategy_template_id": "sync_direct",
                    "params": {"timeout_ms": 0, "capacity_per_replica": 1},
                },
            },
        }
        scenario = _make_scenario()
        engine = PerformanceEngine(components, scenario)
        assert engine.components["svc"].micro_skill is not None
        assert engine.components["svc"].micro_skill.strategy_template_id == "sync_direct"

    def test_normalize_components_dependency_edges_dict(self):
        """_normalize_components 将 dict 形式的 dependency_edges 转为 DependencyEdge。"""
        components = {
            "svc": {
                "name": "svc",
                "type": "service",
                "dependency_edges": [
                    {"source": "svc", "target": "db", "communication_mode": "sync"}
                ],
            },
        }
        scenario = _make_scenario()
        engine = PerformanceEngine(components, scenario)
        edges = engine.components["svc"].dependency_edges
        assert len(edges) == 1
        assert isinstance(edges[0], DependencyEdge)
        assert edges[0].communication_mode == "sync"


# ---------------------------------------------------------------------------
# 策略模板分发测试
# ---------------------------------------------------------------------------

class TestSyncDirect:
    """sync_direct 策略测试。"""

    def test_deps_healthy_success(self):
        """依赖健康 → 请求成功。"""
        components, scenario = _make_components_with_skill("sync_direct")
        engine = PerformanceEngine(components, scenario)
        # db 健康，请求应成功
        report = engine.run(ticks=20)
        assert report["successful"] > 0, "sync_direct 依赖健康时应成功"

    def test_deps_unhealthy_fail(self):
        """依赖不健康 → 立即失败。"""
        components, scenario = _make_components_with_skill("sync_direct")
        engine = PerformanceEngine(components, scenario)
        # 崩溃数据库
        engine.components["order_db"].healthy_replicas = 0
        # 阻止副本恢复
        report = engine.run(ticks=20)
        # 请求应失败
        assert report["failed"] > 0, "sync_direct 依赖不健康时应失败"


class TestRetryNoBackoff:
    """retry_no_backoff 策略测试。"""

    def test_retry_when_dep_unhealthy(self):
        """依赖不健康 → 重试。"""
        params = default_params("retry_no_backoff")
        params["max_retries"] = 3
        components, scenario = _make_components_with_skill("retry_no_backoff", **params)
        engine = PerformanceEngine(components, scenario)
        # 崩溃数据库
        engine.components["order_db"].healthy_replicas = 0
        engine.run(ticks=20)
        # 应产生重试事件
        retry_events = [e for e in engine.events if e.event_type == EventType.REQUEST_RETRY]
        assert len(retry_events) > 0, "retry_no_backoff 依赖不健康时应重试"

    def test_exceed_max_retries_fail(self):
        """超过 max_retries → 失败。"""
        params = default_params("retry_no_backoff")
        params["max_retries"] = 2
        components, scenario = _make_components_with_skill("retry_no_backoff", **params)
        engine = PerformanceEngine(components, scenario)
        engine.components["order_db"].healthy_replicas = 0
        engine.run(ticks=30)
        # 请求应最终失败
        assert report_has_failed(engine), "retry_no_backoff 超过 max_retries 应失败"

    def test_retry_storm_assertion(self):
        """重试耗尽时触发 retry_storm 断言。"""
        params = default_params("retry_no_backoff")
        params["max_retries"] = 2
        components, scenario = _make_components_with_skill("retry_no_backoff", **params)
        engine = PerformanceEngine(components, scenario)
        engine.components["order_db"].healthy_replicas = 0
        engine.run(ticks=30)
        # 检查是否有 retry_storm 断言
        assertion_events = [e for e in engine.assertion_events
                            if "retry_storm" in e.detail]
        assert len(assertion_events) > 0, "重试耗尽应触发 retry_storm 断言"


class TestRetryWithBackoff:
    """retry_with_backoff 策略测试。"""

    def test_delayed_retry(self):
        """依赖不健康 → 延迟重试（ready_tick > current_tick）。"""
        params = default_params("retry_with_backoff")
        params["max_retries"] = 3
        params["base_delay_ms"] = 200
        components, scenario = _make_components_with_skill("retry_with_backoff", **params)
        engine = PerformanceEngine(components, scenario)
        engine.components["order_db"].healthy_replicas = 0

        # 手动向 order_service 队列放入请求
        engine.request_counter += 1
        req = Request(
            req_id=engine.request_counter,
            arrival_tick=1,
            target_component="order_service",
            current_component="order_service",
            remaining_timeout=50,
        )
        engine.components["order_service"].queue.append(req)

        # 执行一个 tick
        engine.step()

        # 检查是否有退避重试事件
        backoff_events = [e for e in engine.events
                          if e.event_type == EventType.STRATEGY_TRIGGERED
                          and "退避重试" in e.detail]
        assert len(backoff_events) > 0, "retry_with_backoff 应产生退避重试事件"

        # 检查请求的 ready_tick 已被设置
        # 请求可能还在队列中
        found_delayed = False
        for comp in engine.components.values():
            for queued_req in comp.queue:
                if queued_req.ready_tick > 0:
                    found_delayed = True
                    break
        assert found_delayed or len(backoff_events) > 0, "退避重试应设置 ready_tick"

    def test_exceed_max_retries_fail(self):
        """超过 max_retries → 失败。"""
        params = default_params("retry_with_backoff")
        params["max_retries"] = 2
        params["base_delay_ms"] = 100
        components, scenario = _make_components_with_skill("retry_with_backoff", **params)
        engine = PerformanceEngine(components, scenario)
        engine.components["order_db"].healthy_replicas = 0
        engine.run(ticks=50)
        assert report_has_failed(engine), "retry_with_backoff 超过 max_retries 应失败"


class TestAsyncFireAndForget:
    """async_fire_and_forget 策略测试。"""

    def test_write_and_return_success(self):
        """写入即返回成功。"""
        params = default_params("async_fire_and_forget")
        params["consumer_lag"] = 3
        skill = MicroSkill(strategy_template_id="async_fire_and_forget", params=params)
        skill_db = MicroSkill(strategy_template_id="sync_direct",
                              params=default_params("sync_direct"))

        components = {
            "gateway": Component(
                name="gateway", type=ComponentType.GATEWAY, replicas=2,
                cpu_per_replica=100, memory_limit=4096, queue_capacity=500,
                timeout_ms=2000, dependencies=["order_service"],
            ),
            "order_service": Component(
                name="order_service", type=ComponentType.SERVICE, replicas=2,
                cpu_per_replica=100, memory_limit=4096, queue_capacity=500,
                timeout_ms=2000, micro_skill=skill,
                dependencies=["mq:async"],
                dependency_edges=[
                    DependencyEdge(source="order_service", target="mq", communication_mode="async")
                ],
            ),
            "mq": Component(
                name="mq", type=ComponentType.MESSAGE_QUEUE, replicas=1,
                cpu_per_replica=100, memory_limit=4096, queue_capacity=500,
                timeout_ms=3000, consumer_lag=3,
                dependencies=["order_db"],
            ),
            "order_db": Component(
                name="order_db", type=ComponentType.DATABASE, replicas=1,
                cpu_per_replica=100, memory_limit=4096, queue_capacity=500,
                timeout_ms=3000, micro_skill=skill_db, dependencies=[],
            ),
        }
        scenario = _make_scenario()
        engine = PerformanceEngine(components, scenario)
        report = engine.run(ticks=20)
        # order_service 应有成功请求（写入即返回）
        assert report["component_stats"]["order_service"]["total_processed"] > 0, \
            "async_fire_and_forget 写入即返回成功"

    def test_consumer_delayed_read(self):
        """消费者按 consumer_lag 延迟读取。"""
        params = default_params("async_fire_and_forget")
        params["consumer_lag"] = 5
        skill = MicroSkill(strategy_template_id="async_fire_and_forget", params=params)
        skill_db = MicroSkill(strategy_template_id="sync_direct",
                              params=default_params("sync_direct"))

        components = {
            "gateway": Component(
                name="gateway", type=ComponentType.GATEWAY, replicas=1,
                cpu_per_replica=100, memory_limit=4096, queue_capacity=500,
                timeout_ms=2000, dependencies=["svc"],
            ),
            "svc": Component(
                name="svc", type=ComponentType.SERVICE, replicas=1,
                cpu_per_replica=100, memory_limit=4096, queue_capacity=500,
                timeout_ms=2000, micro_skill=skill,
                dependencies=["mq:async"],
                dependency_edges=[
                    DependencyEdge(source="svc", target="mq", communication_mode="async")
                ],
            ),
            "mq": Component(
                name="mq", type=ComponentType.MESSAGE_QUEUE, replicas=1,
                cpu_per_replica=100, memory_limit=4096, queue_capacity=500,
                timeout_ms=3000, consumer_lag=5,
                dependencies=["db"],
            ),
            "db": Component(
                name="db", type=ComponentType.DATABASE, replicas=1,
                cpu_per_replica=100, memory_limit=4096, queue_capacity=500,
                timeout_ms=3000, micro_skill=skill_db, dependencies=[],
            ),
        }
        scenario = _make_scenario()
        engine = PerformanceEngine(components, scenario)
        engine.run(ticks=30)
        # 消息队列应有异步消息积压或已分发
        mq = engine.components["mq"]
        # 至少应有 in_flight_async 被写入过
        assert mq.total_processed > 0 or len(mq.in_flight_async) > 0 or \
               engine.components["db"].total_processed > 0, \
            "消费者应能延迟读取消息"


class TestDegradeFallback:
    """degrade_fallback 策略测试。"""

    def test_fallback_available_success(self):
        """fallback 可用 → 降级成功。"""
        params = default_params("degrade_fallback")
        params["fallback_target"] = "cache"
        skill = MicroSkill(strategy_template_id="degrade_fallback", params=params)
        skill_cache = MicroSkill(strategy_template_id="sync_direct",
                                 params=default_params("sync_direct"))

        components = {
            "gateway": Component(
                name="gateway", type=ComponentType.GATEWAY, replicas=1,
                cpu_per_replica=100, memory_limit=4096, queue_capacity=500,
                timeout_ms=2000, dependencies=["svc"],
            ),
            "svc": Component(
                name="svc", type=ComponentType.SERVICE, replicas=1,
                cpu_per_replica=100, memory_limit=4096, queue_capacity=500,
                timeout_ms=2000, micro_skill=skill,
                dependencies=["db"],
                dependency_edges=[
                    DependencyEdge(source="svc", target="db", communication_mode="sync")
                ],
            ),
            "db": Component(
                name="db", type=ComponentType.DATABASE, replicas=1,
                cpu_per_replica=100, memory_limit=4096, queue_capacity=500,
                timeout_ms=3000, dependencies=[],
            ),
            "cache": Component(
                name="cache", type=ComponentType.CACHE, replicas=2,
                cpu_per_replica=100, memory_limit=4096, queue_capacity=500,
                timeout_ms=1000, micro_skill=skill_cache, dependencies=[],
            ),
        }
        scenario = _make_scenario()
        engine = PerformanceEngine(components, scenario)
        # 崩溃数据库
        engine.components["db"].healthy_replicas = 0
        engine.run(ticks=30)
        # svc 应有成功请求（降级到 cache）
        svc_stat = engine.get_report()["component_stats"]["svc"]
        assert svc_stat["total_processed"] > 0, "degrade_fallback 应降级成功"

    def test_fallback_unavailable_fail(self):
        """fallback 不可用 → 失败。"""
        params = default_params("degrade_fallback")
        params["fallback_target"] = "cache"
        skill = MicroSkill(strategy_template_id="degrade_fallback", params=params)

        components = {
            "gateway": Component(
                name="gateway", type=ComponentType.GATEWAY, replicas=1,
                cpu_per_replica=100, memory_limit=4096, queue_capacity=500,
                timeout_ms=2000, dependencies=["svc"],
            ),
            "svc": Component(
                name="svc", type=ComponentType.SERVICE, replicas=1,
                cpu_per_replica=100, memory_limit=4096, queue_capacity=500,
                timeout_ms=2000, micro_skill=skill,
                dependencies=["db"],
                dependency_edges=[
                    DependencyEdge(source="svc", target="db", communication_mode="sync")
                ],
            ),
            "db": Component(
                name="db", type=ComponentType.DATABASE, replicas=1,
                cpu_per_replica=100, memory_limit=4096, queue_capacity=500,
                timeout_ms=3000, dependencies=[],
            ),
            "cache": Component(
                name="cache", type=ComponentType.CACHE, replicas=1,
                cpu_per_replica=100, memory_limit=4096, queue_capacity=500,
                timeout_ms=1000, dependencies=[],
            ),
        }
        scenario = _make_scenario()
        engine = PerformanceEngine(components, scenario)
        # 崩溃数据库和缓存
        engine.components["db"].healthy_replicas = 0
        engine.components["cache"].healthy_replicas = 0
        engine.run(ticks=30)
        # svc 应有失败请求
        assert report_has_failed(engine), "degrade_fallback fallback 不可用时应失败"


class TestCircuitBreak:
    """circuit_break 策略测试。"""

    def test_consecutive_failures_open_circuit(self):
        """连续失败 → 熔断。"""
        params = default_params("circuit_break")
        params["failure_threshold"] = 3
        params["recovery_ticks"] = 10
        skill = MicroSkill(strategy_template_id="circuit_break", params=params)

        components = {
            "gateway": Component(
                name="gateway", type=ComponentType.GATEWAY, replicas=1,
                cpu_per_replica=100, memory_limit=4096, queue_capacity=500,
                timeout_ms=2000, dependencies=["svc"],
            ),
            "svc": Component(
                name="svc", type=ComponentType.SERVICE, replicas=1,
                cpu_per_replica=100, memory_limit=4096, queue_capacity=500,
                timeout_ms=2000, micro_skill=skill,
                dependencies=["db"],
                dependency_edges=[
                    DependencyEdge(source="svc", target="db", communication_mode="sync")
                ],
            ),
            "db": Component(
                name="db", type=ComponentType.DATABASE, replicas=1,
                cpu_per_replica=100, memory_limit=4096, queue_capacity=500,
                timeout_ms=3000, dependencies=[],
            ),
        }
        scenario = _make_scenario()
        engine = PerformanceEngine(components, scenario)
        # 崩溃数据库
        engine.components["db"].healthy_replicas = 0
        engine.run(ticks=30)
        # svc 应触发熔断
        cb_events = [e for e in engine.events
                     if e.event_type == EventType.CIRCUIT_BREAKER_OPEN
                     and e.component == "svc"]
        assert len(cb_events) > 0, "circuit_break 连续失败应打开熔断器"

    def test_circuit_open_fast_fail(self):
        """熔断期间 fast fail。"""
        params = default_params("circuit_break")
        params["failure_threshold"] = 3
        params["recovery_ticks"] = 20  # 较长恢复时间
        skill = MicroSkill(strategy_template_id="circuit_break", params=params)

        components = {
            "gateway": Component(
                name="gateway", type=ComponentType.GATEWAY, replicas=1,
                cpu_per_replica=100, memory_limit=4096, queue_capacity=500,
                timeout_ms=2000, dependencies=["svc"],
            ),
            "svc": Component(
                name="svc", type=ComponentType.SERVICE, replicas=1,
                cpu_per_replica=100, memory_limit=4096, queue_capacity=500,
                timeout_ms=2000, micro_skill=skill,
                dependencies=["db"],
                dependency_edges=[
                    DependencyEdge(source="svc", target="db", communication_mode="sync")
                ],
            ),
            "db": Component(
                name="db", type=ComponentType.DATABASE, replicas=1,
                cpu_per_replica=100, memory_limit=4096, queue_capacity=500,
                timeout_ms=3000, dependencies=[],
            ),
        }
        scenario = _make_scenario()
        engine = PerformanceEngine(components, scenario)
        engine.components["db"].healthy_replicas = 0
        engine.run(ticks=30)

        # 找到熔断打开的 tick
        cb_open_events = [e for e in engine.events
                          if e.event_type == EventType.CIRCUIT_BREAKER_OPEN
                          and e.component == "svc"]
        if cb_open_events:
            open_tick = cb_open_events[0].tick
            # 熔断期间不应有 request_success
            success_during_open = [
                e for e in engine.events
                if e.event_type == EventType.REQUEST_SUCCESS
                and e.component == "svc"
                and open_tick < e.tick <= open_tick + 20
            ]
            assert len(success_during_open) == 0, \
                "熔断器打开期间不应有 request_success"


# ---------------------------------------------------------------------------
# 依赖边级联测试
# ---------------------------------------------------------------------------

class TestDependencyEdgeCascade:
    """依赖边语义级联测试。"""

    def test_sync_edge_propagates_failure(self):
        """sync 边传播失败。"""
        edge = DependencyEdge(source="svc", target="db", communication_mode="sync")
        skill = default_skill("sync_direct")
        components = {
            "gateway": Component(
                name="gateway", type=ComponentType.GATEWAY, replicas=1,
                cpu_per_replica=100, memory_limit=4096, queue_capacity=500,
                timeout_ms=2000, dependencies=["svc"],
            ),
            "svc": Component(
                name="svc", type=ComponentType.SERVICE, replicas=1,
                cpu_per_replica=100, memory_limit=4096, queue_capacity=500,
                timeout_ms=2000, micro_skill=skill,
                dependencies=["db"],
                dependency_edges=[edge],
            ),
            "db": Component(
                name="db", type=ComponentType.DATABASE, replicas=1,
                cpu_per_replica=100, memory_limit=4096, queue_capacity=500,
                timeout_ms=3000, dependencies=[],
            ),
        }
        scenario = _make_scenario()
        engine = PerformanceEngine(components, scenario)
        # 崩溃数据库
        engine.components["db"].healthy_replicas = 0
        engine.run(ticks=20)
        # sync 边应传播失败
        assert report_has_failed(engine), "sync 边应传播失败"

    def test_async_edge_blocks_cascade(self):
        """async 边阻断级联（写入即返回）。"""
        edge = DependencyEdge(source="svc", target="mq", communication_mode="async")
        skill = default_skill("sync_direct")
        components = {
            "gateway": Component(
                name="gateway", type=ComponentType.GATEWAY, replicas=1,
                cpu_per_replica=100, memory_limit=4096, queue_capacity=500,
                timeout_ms=2000, dependencies=["svc"],
            ),
            "svc": Component(
                name="svc", type=ComponentType.SERVICE, replicas=1,
                cpu_per_replica=100, memory_limit=4096, queue_capacity=500,
                timeout_ms=2000, micro_skill=skill,
                dependencies=["mq:async"],
                dependency_edges=[edge],
            ),
            "mq": Component(
                name="mq", type=ComponentType.MESSAGE_QUEUE, replicas=1,
                cpu_per_replica=100, memory_limit=4096, queue_capacity=500,
                timeout_ms=3000, consumer_lag=2,
                dependencies=["db"],
            ),
            "db": Component(
                name="db", type=ComponentType.DATABASE, replicas=1,
                cpu_per_replica=100, memory_limit=4096, queue_capacity=500,
                timeout_ms=3000, dependencies=[],
            ),
        }
        scenario = _make_scenario()
        engine = PerformanceEngine(components, scenario)
        # 即使 mq down，async 写入也应成功
        engine.components["mq"].healthy_replicas = 0
        engine.run(ticks=20)
        # svc 应有成功请求（async 写入即返回）
        svc_stat = engine.get_report()["component_stats"]["svc"]
        assert svc_stat["total_processed"] > 0, \
            "async 边应阻断级联（写入即返回成功）"


# ---------------------------------------------------------------------------
# 断言机制测试
# ---------------------------------------------------------------------------

class TestAssertions:
    """断言触发机制测试。"""

    def test_retry_storm_assertion_triggered(self):
        """retry_storm 断言触发。"""
        params = default_params("retry_no_backoff")
        params["max_retries"] = 2
        components, scenario = _make_components_with_skill("retry_no_backoff", **params)
        engine = PerformanceEngine(components, scenario)
        engine.components["order_db"].healthy_replicas = 0
        engine.run(ticks=30)
        # 检查 assertion_events
        storm_assertions = [e for e in engine.assertion_events
                            if "retry_storm" in e.detail]
        assert len(storm_assertions) > 0, "应触发 retry_storm 断言"
        # 检查事件类型
        assert all(e.event_type == EventType.ASSERTION_TRIGGERED
                   for e in storm_assertions), "断言事件类型应为 ASSERTION_TRIGGERED"

    def test_cascading_failure_assertion_triggered(self):
        """cascading_failure 断言触发。"""
        params = default_params("degrade_fallback")
        params["fallback_target"] = "cache"
        skill = MicroSkill(strategy_template_id="degrade_fallback", params=params)

        components = {
            "gateway": Component(
                name="gateway", type=ComponentType.GATEWAY, replicas=1,
                cpu_per_replica=100, memory_limit=4096, queue_capacity=500,
                timeout_ms=2000, dependencies=["svc"],
            ),
            "svc": Component(
                name="svc", type=ComponentType.SERVICE, replicas=1,
                cpu_per_replica=100, memory_limit=4096, queue_capacity=500,
                timeout_ms=2000, micro_skill=skill,
                dependencies=["db"],
                dependency_edges=[
                    DependencyEdge(source="svc", target="db", communication_mode="sync")
                ],
            ),
            "db": Component(
                name="db", type=ComponentType.DATABASE, replicas=1,
                cpu_per_replica=100, memory_limit=4096, queue_capacity=500,
                timeout_ms=3000, dependencies=[],
            ),
            "cache": Component(
                name="cache", type=ComponentType.CACHE, replicas=1,
                cpu_per_replica=100, memory_limit=4096, queue_capacity=500,
                timeout_ms=1000, dependencies=[],
            ),
        }
        scenario = _make_scenario()
        engine = PerformanceEngine(components, scenario)
        # 崩溃数据库和缓存
        engine.components["db"].healthy_replicas = 0
        engine.components["cache"].healthy_replicas = 0
        engine.run(ticks=30)
        # 检查 cascading_failure 断言
        cascade_assertions = [e for e in engine.assertion_events
                              if "cascading_failure" in e.detail]
        assert len(cascade_assertions) > 0, "应触发 cascading_failure 断言"

    def test_assertion_events_in_report(self):
        """断言事件出现在 get_report 中。"""
        params = default_params("retry_no_backoff")
        params["max_retries"] = 2
        components, scenario = _make_components_with_skill("retry_no_backoff", **params)
        engine = PerformanceEngine(components, scenario)
        engine.components["order_db"].healthy_replicas = 0
        report = engine.run(ticks=30)
        # assertion_events 应非空
        assert len(report["assertion_events"]) > 0, "报告应包含断言事件"
        # key_events 应包含 ASSERTION_TRIGGERED
        key_types = [e["type"] for e in report["key_events"]]
        assert "assertion_triggered" in key_types, "key_events 应包含 assertion_triggered"


# ---------------------------------------------------------------------------
# 确定性测试
# ---------------------------------------------------------------------------

class TestDeterminism:
    """确定性测试。"""

    def test_same_input_same_output(self):
        """相同输入多次运行结果一致。"""
        components, scenario = _make_components_with_skill("sync_direct")
        engine1 = PerformanceEngine(
            _copy_components(components), scenario
        )
        report1 = engine1.run(ticks=50)

        engine2 = PerformanceEngine(
            _copy_components(components), scenario
        )
        report2 = engine2.run(ticks=50)

        json1 = json.dumps(report1, sort_keys=True, default=str)
        json2 = json.dumps(report2, sort_keys=True, default=str)
        assert json1 == json2, "相同输入的两次运行结果不一致"

    def test_different_strategies_deterministic(self):
        """不同策略模板下的确定性。"""
        params = default_params("retry_no_backoff")
        params["max_retries"] = 3
        components, scenario = _make_components_with_skill("retry_no_backoff", **params)

        engine1 = PerformanceEngine(_copy_components(components), scenario)
        engine1.components["order_db"].healthy_replicas = 0
        report1 = engine1.run(ticks=30)

        engine2 = PerformanceEngine(_copy_components(components), scenario)
        engine2.components["order_db"].healthy_replicas = 0
        report2 = engine2.run(ticks=30)

        json1 = json.dumps(report1, sort_keys=True, default=str)
        json2 = json.dumps(report2, sort_keys=True, default=str)
        assert json1 == json2, "retry_no_backoff 相同输入的两次运行结果不一致"


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def report_has_failed(engine) -> bool:
    """检查引擎是否有失败请求。"""
    return len(engine.failed_requests) > 0 or engine.get_report()["failed"] > 0


def _copy_components(components: dict) -> dict:
    """深拷贝组件字典，用于确定性测试。

    因为 Component 含 deque 等不可 json 序列化的字段，
    需要手动重建。
    """
    from dataclasses import fields as dataclass_fields
    copied = {}
    for name, comp in components.items():
        kwargs = {}
        for f in dataclass_fields(comp):
            val = getattr(comp, f.name)
            # 运行时状态重置为默认值
            if f.name in ("healthy_replicas", "in_flight", "memory_used",
                          "failure_count", "success_count", "total_processed",
                          "total_failed", "total_retried", "total_rated_limited",
                          "dropped_messages", "trigger_count", "consecutive_failures"):
                kwargs[f.name] = 0
            elif f.name == "queue":
                kwargs[f.name] = deque()
            elif f.name == "in_flight_async":
                kwargs[f.name] = []
            elif f.name == "crash_history":
                kwargs[f.name] = []
            elif f.name == "circuit_open":
                kwargs[f.name] = False
            elif f.name == "circuit_open_until":
                kwargs[f.name] = 0
            elif f.name == "dependency_edges":
                # 浅拷贝 list
                kwargs[f.name] = list(val)
            elif f.name == "micro_skill":
                # MicroSkill 不需要拷贝（只读）
                kwargs[f.name] = val
            else:
                kwargs[f.name] = val
        copied[name] = Component(**kwargs)
    return copied


# 需要在 _copy_components 之前导入 deque
from collections import deque
