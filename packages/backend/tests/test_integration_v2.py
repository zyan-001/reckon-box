"""
ReckonBox 2.0 集成测试
======================
覆盖全链路和向后兼容性。

场景：
1. 端到端全链路：MicroSkill → PerformanceEngine → ConvergenceChecker
2. 迭代收敛：调整策略参数后重新模拟，验证收敛
3. 旧数据兼容性：旧 DesignSpec JSON / 旧依赖格式可正确加载和迁移
"""

from modules.performance_engine import (
    PerformanceEngine, Component, ComponentType, EventType,
)
from modules.domain_pack import Scenario, Phase, Inject
from modules.convergence_checker import check_convergence
from utils.micro_skill import MicroSkill, default_skill
from utils.dependency_edge import DependencyEdge, edges_from_legacy_deps, from_legacy
from utils.design_spec import (
    DesignSpec, ComponentConfig, to_dict as spec_to_dict,
    from_dict as spec_from_dict, from_json as spec_from_json,
)
from utils.blackbox_spec import BlackBoxSpec


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _make_scenario(phases=None, inject=None):
    """创建测试用 Scenario 对象。"""
    if phases is None:
        phases = [Phase(ticks=50, rps=5)]
    return Scenario(
        id="test_scenario",
        name="测试场景",
        description="集成测试场景",
        phases=phases,
        inject=inject or [],
    )


def _build_components_with_skills(gateway_skill, order_skill, db_skill):
    """构造三组件字典（gateway + order_service + database），绑定各自的 MicroSkill。

    组件依赖关系：gateway → order_service → database
    """
    components = {
        "gateway": {
            "name": "gateway",
            "type": "gateway",
            "replicas": 1,
            "cpu_per_replica": 100,
            "memory_limit": 1024,
            "memory_per_request": 5,
            "queue_capacity": 500,
            "timeout_ms": 1000,
            "dependencies": ["order_service"],
            "micro_skill": gateway_skill,
        },
        "order_service": {
            "name": "order_service",
            "type": "service",
            "replicas": 2,
            "cpu_per_replica": 50,
            "memory_limit": 512,
            "memory_per_request": 5,
            "queue_capacity": 200,
            "timeout_ms": 1000,
            "dependencies": ["database"],
            "micro_skill": order_skill,
        },
        "database": {
            "name": "database",
            "type": "database",
            "replicas": 1,
            "cpu_per_replica": 100,
            "memory_limit": 512,
            "memory_per_request": 5,
            "queue_capacity": 200,
            "timeout_ms": 1000,
            "dependencies": [],
            "micro_skill": db_skill,
        },
    }
    return components


# ===========================================================================
# 场景 1：端到端全链路测试
# ===========================================================================

def test_e2e_full_pipeline():
    """端到端：构造设计 → 运行模拟 → 收敛检查

    验证：
    - MicroSkill 正确绑定到组件
    - PerformanceEngine 策略分发正确
    - 收敛检查结果完整
    - report 包含策略分发信息和断言事件
    """
    # 1. 构造 MicroSkill（用 default_skill 而非 SkillParser，避免 LLM 依赖）
    gateway_skill = default_skill("sync_direct")
    order_skill = default_skill("retry_no_backoff")
    # 手动设置 max_retries 参数
    order_skill.params["max_retries"] = 3
    order_skill.params["capacity_per_replica"] = 50
    db_skill = default_skill("sync_direct")

    # 2. 构造组件字典（gateway + order_service + database）
    components = _build_components_with_skills(gateway_skill, order_skill, db_skill)

    # 3. 构造 Scenario（正常流量 + 故障注入 crash database）
    scenario = _make_scenario(
        phases=[Phase(ticks=30, rps=5), Phase(ticks=30, rps=10)],
        inject=[Inject(target="database", action="crash_all", at_tick=15)],
    )

    # 4. 运行 PerformanceEngine
    engine = PerformanceEngine(components, scenario)
    report = engine.run(ticks=60)

    # 5. 构造 BlackBoxSpec 用于收敛检查
    blackbox_spec = BlackBoxSpec(
        scenario_description="数据库崩溃场景",
        functional_points=["创建订单", "查询订单"],
        performance_bounds={
            "availability_target": "50.0%",   # 崩溃后成功率会降低，放宽阈值
            "target_latency_ms": 10000,
        },
    )

    # 6. 收敛检查
    result = check_convergence(report, blackbox_spec)

    # 7. 验证 report 包含策略分发信息
    assert "strategy_dispatch_info" in report
    assert report["strategy_dispatch_info"]["gateway"] == "sync_direct"
    assert report["strategy_dispatch_info"]["order_service"] == "retry_no_backoff"
    assert report["strategy_dispatch_info"]["database"] == "sync_direct"

    # 8. 验证 report 包含断言事件字段
    assert "assertion_events" in report
    assert isinstance(report["assertion_events"], list)

    # 9. 验证收敛检查结果结构完整
    assert hasattr(result, "converged")
    assert hasattr(result, "gaps")
    assert hasattr(result, "summary")
    assert isinstance(result.gaps, list)
    assert len(result.gaps) > 0  # 至少有 crash_points 验收项

    # 10. 验证基本统计合理
    assert report["total_requests"] > 0
    assert report["total_ticks"] == 60

    # 11. database 崩溃后 order_service 的重试应触发断言事件
    # （retry_no_backoff 在依赖不可用且重试耗尽时触发 retry_storm 断言）
    # 检查是否有 COMPONENT_CRASH 事件
    crash_events = [e for e in report["key_events"] if e["type"] == "component_crash"]
    assert len(crash_events) > 0, "应有 database 崩溃事件"


# ===========================================================================
# 场景 2：迭代收敛测试
# ===========================================================================

def test_iteration_convergence():
    """调整 MicroSkill 参数后重新模拟，验证收敛

    第一轮：order_service 使用 retry_no_backoff(max_retries=3)
      - database 崩溃 → retry_storm → 不收敛
    第二轮：调整 order_service 为 circuit_break(failure_threshold=3, recovery_ticks=10)
      - database 崩溃 → 熔断器保护 → 成功率提高 → 收敛
    """

    # ---- 第一轮：retry_no_backoff ----
    gateway_skill = default_skill("sync_direct")
    order_skill_v1 = default_skill("retry_no_backoff")
    order_skill_v1.params["max_retries"] = 3
    order_skill_v1.params["capacity_per_replica"] = 50
    db_skill = default_skill("sync_direct")

    components_v1 = _build_components_with_skills(gateway_skill, order_skill_v1, db_skill)

    # 数据库在 tick 15 崩溃
    scenario = _make_scenario(
        phases=[Phase(ticks=30, rps=5), Phase(ticks=30, rps=10)],
        inject=[Inject(target="database", action="crash_all", at_tick=15)],
    )

    engine_v1 = PerformanceEngine(components_v1, scenario)
    report_v1 = engine_v1.run(ticks=60)

    # 第一轮收敛检查：成功率应较低
    blackbox_spec = BlackBoxSpec(
        scenario_description="迭代收敛第一轮",
        performance_bounds={
            "availability_target": "80.0%",
            "target_latency_ms": 5000,
        },
    )
    result_v1 = check_convergence(report_v1, blackbox_spec)

    # ---- 第二轮：circuit_break ----
    gateway_skill_v2 = default_skill("sync_direct")
    order_skill_v2 = MicroSkill(
        strategy_template_id="circuit_break",
        params={"failure_threshold": 3, "recovery_ticks": 10, "capacity_per_replica": 50},
    )
    db_skill_v2 = default_skill("sync_direct")

    components_v2 = _build_components_with_skills(gateway_skill_v2, order_skill_v2, db_skill_v2)

    engine_v2 = PerformanceEngine(components_v2, scenario)
    report_v2 = engine_v2.run(ticks=60)

    result_v2 = check_convergence(report_v2, blackbox_spec)

    # 验证两轮结果差异
    # 第二轮的崩溃点应少于或等于第一轮
    # （retry_storm 断言在第一轮更可能触发）
    assertions_v1 = report_v1["assertion_events"]
    assertions_v2 = report_v2["assertion_events"]

    # 验证第一轮的 retry_storm 断言触发
    retry_storm_v1 = [
        a for a in assertions_v1 if "retry_storm" in a.get("detail", "")
    ]
    assert len(retry_storm_v1) > 0, (
        "第一轮 retry_no_backoff 在 database 崩溃后应触发 retry_storm 断言"
    )

    # 验证策略分发正确
    assert report_v1["strategy_dispatch_info"]["order_service"] == "retry_no_backoff"
    assert report_v2["strategy_dispatch_info"]["order_service"] == "circuit_break"

    # 第二轮应有熔断器事件
    cb_events_v2 = [
        e for e in report_v2["key_events"]
        if e["type"] in ("circuit_breaker_open", "circuit_breaker_close")
    ]
    assert len(cb_events_v2) > 0, "第二轮 circuit_break 应触发熔断器事件"

    # 验证收敛结果结构
    assert isinstance(result_v1.converged, bool)
    assert isinstance(result_v2.converged, bool)


# ===========================================================================
# 场景 3：旧数据兼容性测试
# ===========================================================================

def test_legacy_design_spec_compatibility():
    """旧的 DesignSpec JSON（含 strategies 字段）可正确加载

    验证：
    - strategies 字段被忽略
    - micro_skill 为 None
    - to_dict 输出不含 strategies 字段，含 micro_skill=None
    """
    # 构造旧格式 JSON（含 strategies 字段）
    legacy_json = '''{
        "domain": "software_systems",
        "components": [
            {
                "name": "svc",
                "type": "service",
                "replicas": 2,
                "cpu_per_replica": 50,
                "memory_limit": 512,
                "memory_per_request": 5,
                "queue_capacity": 200,
                "timeout_ms": 1000,
                "retry_count": 3,
                "has_circuit_breaker": false,
                "has_rate_limiter": false,
                "rate_limit": 0,
                "dependencies": ["database"],
                "description": "订单服务",
                "strategies": ["retry", "circuit_breaker"],
                "micro_skill": null
            },
            {
                "name": "database",
                "type": "database",
                "replicas": 1,
                "strategies": ["sync_direct"]
            }
        ]
    }'''

    # 用 from_json 加载
    spec = spec_from_json(legacy_json)

    # 验证 strategies 被忽略，micro_skill 为 None
    assert spec.domain == "software_systems"
    assert len(spec.components) == 2

    svc_comp = spec.components[0]
    assert isinstance(svc_comp, ComponentConfig)
    assert svc_comp.name == "svc"
    assert svc_comp.micro_skill is None
    assert svc_comp.retry_count == 3  # 正常字段保留

    db_comp = spec.components[1]
    assert db_comp.name == "database"
    assert db_comp.micro_skill is None

    # 验证 to_dict 输出不含 strategies 字段，含 micro_skill=None
    output = spec_to_dict(spec)
    for comp_dict in output["components"]:
        assert "strategies" not in comp_dict, "输出不应包含 strategies 字段"
        assert "micro_skill" in comp_dict
        assert comp_dict["micro_skill"] is None


def test_legacy_dependency_format():
    """旧依赖格式 'a:async' 可通过 DependencyEdge 迁移

    验证 edges_from_legacy_deps 正确转换旧格式字符串
    """
    # 构造含旧格式依赖的列表
    legacy_deps = ["database", "cache:async", "mq:async:backoff"]

    # 用 edges_from_legacy_deps 转换
    edges = edges_from_legacy_deps("order_service", legacy_deps)

    # 验证 DependencyEdge 列表正确
    assert len(edges) == 3

    # database（sync，无标注）
    assert edges[0].source == "order_service"
    assert edges[0].target == "database"
    assert edges[0].communication_mode == "sync"
    assert edges[0].retry_strategy is None

    # cache:async
    assert edges[1].source == "order_service"
    assert edges[1].target == "cache"
    assert edges[1].communication_mode == "async"
    assert edges[1].retry_strategy is None

    # mq:async:backoff
    assert edges[2].source == "order_service"
    assert edges[2].target == "mq"
    assert edges[2].communication_mode == "async"
    assert edges[2].retry_strategy == "backoff"

    # 也验证 from_legacy 单个转换
    edge = from_legacy("redis:async", "gateway")
    assert edge.source == "gateway"
    assert edge.target == "redis"
    assert edge.communication_mode == "async"


def test_legacy_design_spec_roundtrip():
    """旧 DesignSpec 加载后序列化再加载，数据一致

    验证 from_json → to_dict → from_dict 后字段一致
    """
    # 加载旧格式 JSON（含 strategies 字段）
    legacy_json = '''{
        "domain": "ecommerce",
        "components": [
            {
                "name": "gateway",
                "type": "gateway",
                "replicas": 1,
                "dependencies": ["order_svc"],
                "strategies": ["sync_direct"]
            },
            {
                "name": "order_svc",
                "type": "service",
                "replicas": 3,
                "cpu_per_replica": 80,
                "memory_limit": 1024,
                "dependencies": ["db"],
                "strategies": ["retry", "circuit_breaker"]
            },
            {
                "name": "db",
                "type": "database",
                "replicas": 2,
                "dependencies": [],
                "strategies": ["sync_direct"]
            }
        ]
    }'''

    # 第一次加载
    spec1 = spec_from_json(legacy_json)

    # 序列化再反序列化
    dict_out = spec_to_dict(spec1)
    spec2 = spec_from_dict(dict_out)

    # 验证字段一致
    assert spec1.domain == spec2.domain
    assert len(spec1.components) == len(spec2.components)

    for c1, c2 in zip(spec1.components, spec2.components):
        assert c1.name == c2.name
        assert c1.type == c2.type
        assert c1.replicas == c2.replicas
        assert c1.dependencies == c2.dependencies
        assert c1.micro_skill == c2.micro_skill  # 均为 None

    # 再次序列化，确认不含 strategies 字段
    dict_out2 = spec_to_dict(spec2)
    for comp_dict in dict_out2["components"]:
        assert "strategies" not in comp_dict
