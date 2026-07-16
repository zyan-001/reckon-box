"""模拟引擎确定性测试（验收标准 3.1-3.9）"""

import json
import random
import re
import time
from pathlib import Path

from modules.simulation_engine import SandboxEngine, Component, ComponentType, Request
from modules.domain_pack import load_domain_pack
from domain_packs.software_systems_presets import design_novice, design_expert
from utils.design_spec import spec_to_engine_components

YAML_PATH = str(
    Path(__file__).resolve().parent.parent / "src" / "domain_packs" / "software_systems.yaml"
)


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _get_novice_components():
    """加载初学者设计并转为引擎可用的 dict。"""
    return spec_to_engine_components(design_novice())


def _get_expert_components():
    """加载专家级设计并转为引擎可用的 dict。"""
    return spec_to_engine_components(design_expert())


def _run_engine(components, scenario_id, ticks=100):
    """创建引擎并运行，返回 report。"""
    pack = load_domain_pack(YAML_PATH)
    scenario = pack.get_scenario(scenario_id)
    engine = SandboxEngine(components, scenario)
    return engine.run(ticks=ticks)


# ---------------------------------------------------------------------------
# 3.1 性能：100 ticks × 6组件 < 2秒
# ---------------------------------------------------------------------------

def test_performance_100_ticks():
    """验收 3.1：100 ticks 模拟在 2 秒内完成。"""
    components = _get_novice_components()
    start = time.time()
    report = _run_engine(components, "flash_sale", ticks=100)
    elapsed = time.time() - start
    assert elapsed < 2.0, f"100 ticks 模拟耗时 {elapsed:.2f}s，超过 2s 限制"
    assert report["total_ticks"] == 100


# ---------------------------------------------------------------------------
# 3.2 确定性：相同输入产生相同输出
# ---------------------------------------------------------------------------

def test_determinism_same_output():
    """验收 3.2：相同输入连续运行2次，输出完全一致。"""
    report1 = _run_engine(_get_novice_components(), "flash_sale", ticks=100)
    report2 = _run_engine(_get_novice_components(), "flash_sale", ticks=100)
    json1 = json.dumps(report1, sort_keys=True, default=str)
    json2 = json.dumps(report2, sort_keys=True, default=str)
    assert json1 == json2, "相同输入的两次运行结果不一致"


# ---------------------------------------------------------------------------
# 3.3 引擎不依赖外部 API
# ---------------------------------------------------------------------------

def test_no_external_api():
    """验收 3.3：引擎不依赖任何外部 API 或网络调用。"""
    # 验证引擎源码不导入网络模块
    se_path = Path(__file__).resolve().parent.parent / "src" / "modules" / "simulation_engine.py"
    source = se_path.read_text(encoding="utf-8")
    for mod in ["requests", "urllib", "httpx", "socket", "aiohttp"]:
        assert f"import {mod}" not in source, f"引擎不应依赖网络模块: {mod}"

    # 验证引擎能正常运行
    report = _run_engine(_get_novice_components(), "flash_sale", ticks=50)
    assert report is not None
    assert report["total_requests"] > 0, "引擎应能正常运行"


# ---------------------------------------------------------------------------
# 3.4 空依赖组件正常处理请求
# ---------------------------------------------------------------------------

def test_empty_dependency_component():
    """验收 3.4：空依赖的组件（database/cache）正常处理请求。"""
    components = {
        "gateway": Component(
            name="gateway", type=ComponentType.GATEWAY, replicas=2,
            cpu_per_replica=100, memory_limit=4096, queue_capacity=500,
            timeout_ms=2000, dependencies=["database"],
        ),
        "database": Component(
            name="database", type=ComponentType.DATABASE, replicas=1,
            cpu_per_replica=100, memory_limit=4096, queue_capacity=500,
            timeout_ms=3000, dependencies=[],
        ),
    }
    pack = load_domain_pack(YAML_PATH)
    scenario = pack.get_scenario("normal")
    engine = SandboxEngine(components, scenario)
    report = engine.run(ticks=20)
    assert report["successful"] > 0, "空依赖组件应能成功处理请求"


# ---------------------------------------------------------------------------
# 3.5 所有副本崩溃 → 队列请求标记为失败
# ---------------------------------------------------------------------------

def test_all_replicas_crash():
    """验收 3.5：所有副本崩溃的组件，队列请求被标记为失败。"""
    components = {
        "gateway": Component(
            name="gateway", type=ComponentType.GATEWAY, replicas=1,
            cpu_per_replica=100, memory_limit=4096, queue_capacity=500,
            timeout_ms=2000, dependencies=["svc"],
        ),
        "svc": Component(
            name="svc", type=ComponentType.SERVICE, replicas=2,
            cpu_per_replica=100, memory_limit=4096, queue_capacity=500,
            timeout_ms=2000, retry_count=0, dependencies=[],
        ),
    }
    pack = load_domain_pack(YAML_PATH)
    scenario = pack.get_scenario("normal")
    engine = SandboxEngine(components, scenario)

    # 手动向 svc 队列放入请求
    test_req_ids = []
    for i in range(10):
        engine.request_counter += 1
        req = Request(
            req_id=engine.request_counter,
            arrival_tick=1,
            target_component="svc",
            current_component="svc",
            remaining_timeout=20,
        )
        engine.components["svc"].queue.append(req)
        test_req_ids.append(req.req_id)

    # 手动崩溃所有副本
    engine.components["svc"].healthy_replicas = 0

    # 运行一个 tick 处理
    engine.step()

    failed_ids = {req.req_id for req in engine.failed_requests}
    for rid in test_req_ids:
        assert rid in failed_ids, f"请求 #{rid} 应在 failed_requests 中"


# ---------------------------------------------------------------------------
# 3.6 熔断器打开后拒绝所有请求
# ---------------------------------------------------------------------------

def test_circuit_breaker_open_rejects():
    """验收 3.6：熔断器打开后拒绝所有请求。"""
    components = {
        "gateway": Component(
            name="gateway", type=ComponentType.GATEWAY, replicas=1,
            cpu_per_replica=100, memory_limit=4096, queue_capacity=500,
            timeout_ms=2000, dependencies=["svc"],
        ),
        "svc": Component(
            name="svc", type=ComponentType.SERVICE, replicas=1,
            cpu_per_replica=5, memory_limit=4096, queue_capacity=10,
            timeout_ms=1000, retry_count=0, has_circuit_breaker=True,
            dependencies=["db"],
        ),
        "db": Component(
            name="db", type=ComponentType.DATABASE, replicas=1,
            cpu_per_replica=100, memory_limit=4096, queue_capacity=500,
            timeout_ms=3000, dependencies=[],
        ),
    }
    pack = load_domain_pack(YAML_PATH)
    scenario = pack.get_scenario("flash_sale")
    engine = SandboxEngine(components, scenario)
    engine.run(ticks=100)

    # 找到第一个 circuit_breaker_open 事件
    cb_open_ticks = [
        e.tick for e in engine.events
        if e.event_type.value == "circuit_breaker_open" and e.component == "svc"
    ]
    assert len(cb_open_ticks) > 0, "svc 应触发熔断器"

    first_open_tick = cb_open_ticks[0]

    # 验证熔断器打开期间（open_tick+1 到 open_tick+10）无 request_success
    successes_during_open = [
        e for e in engine.events
        if e.event_type.value == "request_success"
        and e.component == "svc"
        and first_open_tick < e.tick <= first_open_tick + 10
    ]
    assert len(successes_during_open) == 0, (
        f"熔断器打开期间(tick {first_open_tick + 1}-{first_open_tick + 10}) "
        f"不应有 request_success，找到 {len(successes_during_open)} 个"
    )


# ---------------------------------------------------------------------------
# 3.7 重试请求不会无限循环
# ---------------------------------------------------------------------------

def test_retry_not_infinite():
    """验收 3.7：重试请求不会无限循环，retry_attempt 不超过 retry_count。"""
    components = {
        "gateway": Component(
            name="gateway", type=ComponentType.GATEWAY, replicas=1,
            cpu_per_replica=100, memory_limit=4096, queue_capacity=500,
            timeout_ms=2000, dependencies=["svc"],
        ),
        "svc": Component(
            name="svc", type=ComponentType.SERVICE, replicas=1,
            cpu_per_replica=100, memory_limit=4096, queue_capacity=500,
            timeout_ms=1000, retry_count=2, has_circuit_breaker=False,
            dependencies=["db"],
        ),
        "db": Component(
            name="db", type=ComponentType.DATABASE, replicas=1,
            cpu_per_replica=100, memory_limit=4096, queue_capacity=500,
            timeout_ms=3000, dependencies=[],
        ),
    }
    pack = load_domain_pack(YAML_PATH)
    scenario = pack.get_scenario("normal")
    engine = SandboxEngine(components, scenario)

    # 崩溃数据库，使 svc 的依赖始终不可用
    engine.components["db"].healthy_replicas = 0
    engine.run(ticks=20)

    # 验证所有重试事件的 retry_attempt 不超过 retry_count
    retry_events = [
        e for e in engine.events if e.event_type.value == "request_retry"
    ]
    assert len(retry_events) > 0, "应产生重试事件"

    for e in retry_events:
        match = re.search(r"第 (\d+)/(\d+) 次重试", e.detail)
        assert match, f"无法解析重试次数: {e.detail}"
        attempt = int(match.group(1))
        max_retry = int(match.group(2))
        assert attempt <= max_retry, (
            f"重试次数 {attempt} 超过配置上限 {max_retry}"
        )

    # 验证重试耗尽后请求进入 failed_requests
    assert len(engine.failed_requests) > 0, "重试耗尽后请求应进入 failed_requests"


# ---------------------------------------------------------------------------
# 3.8 事件日志包含必要字段
# ---------------------------------------------------------------------------

def test_event_log_fields():
    """验收 3.8：事件日志包含 tick、类型、组件、详情字段。"""
    components = _get_novice_components()
    pack = load_domain_pack(YAML_PATH)
    scenario = pack.get_scenario("flash_sale")
    engine = SandboxEngine(components, scenario)
    engine.run(ticks=100)

    all_events = engine.events
    assert len(all_events) >= 10, f"事件数不足: {len(all_events)}"

    rng = random.Random(42)
    sample = rng.sample(all_events, 10)
    for e in sample:
        assert hasattr(e, "tick"), f"事件缺少 tick 字段: {e}"
        assert hasattr(e, "event_type"), f"事件缺少 event_type 字段: {e}"
        assert hasattr(e, "component"), f"事件缺少 component 字段: {e}"
        assert hasattr(e, "detail"), f"事件缺少 detail 字段: {e}"


# ---------------------------------------------------------------------------
# 3.9 组件时间线每 tick 记录状态字段
# ---------------------------------------------------------------------------

def test_timeline_fields():
    """验收 3.9：组件时间线每条记录包含 5 个状态字段。"""
    components = _get_novice_components()
    pack = load_domain_pack(YAML_PATH)
    scenario = pack.get_scenario("flash_sale")
    engine = SandboxEngine(components, scenario)
    engine.run(ticks=100)

    rng = random.Random(42)
    comp_name = rng.choice(list(engine.component_timeline.keys()))
    timeline = engine.component_timeline[comp_name]

    assert len(timeline) > 0, f"组件 {comp_name} 的时间线为空"

    for entry in timeline:
        assert "healthy_replicas" in entry, f"时间线记录缺少 healthy_replicas: {entry}"
        assert "queue_size" in entry, f"时间线记录缺少 queue_size: {entry}"
        assert "memory_used" in entry, f"时间线记录缺少 memory_used: {entry}"
        assert "circuit_open" in entry, f"时间线记录缺少 circuit_open: {entry}"
        assert "total_processed" in entry, f"时间线记录缺少 total_processed: {entry}"
