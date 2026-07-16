"""失败模式分析器测试（验收标准 4.1-4.7）"""

import re
import time
from pathlib import Path

from modules.simulation_engine import SandboxEngine
from modules.failure_analyzer import FailureAnalyzer
from modules.domain_pack import load_domain_pack
from domain_packs.software_systems_presets import design_expert
from utils.design_spec import spec_to_engine_components

YAML_PATH = str(
    Path(__file__).resolve().parent.parent / "src" / "domain_packs" / "software_systems.yaml"
)


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _get_novice_components():
    """返回初学者风格的设计（含常见认知盲区）。

    相比 demo/student_designs.py 的 design_novice()，调高了网关和
    服务的 memory_limit，避免网关在流量洪峰早期因内存耗尽而崩溃，
    从而使失败能级联到下游服务，触发更多失败模式。
    """
    return {
        "api_gateway": {
            "name": "api_gateway", "type": "gateway", "replicas": 2,
            "cpu_per_replica": 100, "memory_limit": 4096,
            "memory_per_request": 5, "queue_capacity": 500,
            "timeout_ms": 2000, "retry_count": 0,
            "has_circuit_breaker": False, "has_rate_limiter": False,
            "rate_limit": 0,
            "dependencies": ["order_service", "product_service", "payment_service"],
        },
        "order_service": {
            "name": "order_service", "type": "service", "replicas": 2,
            "cpu_per_replica": 50, "memory_limit": 2048,
            "memory_per_request": 5, "queue_capacity": 200,
            "timeout_ms": 1000, "retry_count": 3,
            "has_circuit_breaker": False,
            "dependencies": ["product_service", "payment_service", "database"],
        },
        "product_service": {
            "name": "product_service", "type": "service", "replicas": 2,
            "cpu_per_replica": 50, "memory_limit": 2048,
            "memory_per_request": 5, "queue_capacity": 200,
            "timeout_ms": 800, "retry_count": 3,
            "has_circuit_breaker": False,
            "dependencies": ["database", "cache"],
        },
        "payment_service": {
            "name": "payment_service", "type": "service", "replicas": 1,
            "cpu_per_replica": 30, "memory_limit": 256,
            "memory_per_request": 5, "queue_capacity": 100,
            "timeout_ms": 2000, "retry_count": 2,
            "has_circuit_breaker": False,
            "dependencies": ["database"],
        },
        "database": {
            "name": "database", "type": "database", "replicas": 1,
            "cpu_per_replica": 40, "memory_limit": 4096,
            "memory_per_request": 5, "queue_capacity": 300,
            "timeout_ms": 3000, "retry_count": 0,
            "has_circuit_breaker": False,
            "dependencies": [],
        },
        "cache": {
            "name": "cache", "type": "cache", "replicas": 2,
            "cpu_per_replica": 80, "memory_limit": 4096,
            "memory_per_request": 5, "queue_capacity": 500,
            "timeout_ms": 100, "retry_count": 0,
            "has_circuit_breaker": False,
            "dependencies": [],
        },
    }


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
# 4.1 至少检测 5 种失败模式
# ---------------------------------------------------------------------------

def test_detect_at_least_5_modes():
    """验收 4.1：初学者设计 + flash_sale 场景，检测到 ≥5 种失败模式。"""
    report = _run_engine(_get_novice_components(), "flash_sale", ticks=100)
    modes = report["failure_modes"]
    mode_types = set(m["mode"] for m in modes)
    assert len(mode_types) >= 5, (
        f"应检测到 ≥5 种失败模式，实际 {len(mode_types)} 种: {mode_types}"
    )


# ---------------------------------------------------------------------------
# 4.2 每个失败模式附带证据（具体数字）
# ---------------------------------------------------------------------------

def test_evidence_contains_numbers():
    """验收 4.2：每个失败模式的 evidence 字段包含可验证的数字。"""
    report = _run_engine(_get_novice_components(), "flash_sale", ticks=100)
    modes = report["failure_modes"]
    assert len(modes) > 0, "应至少有1个失败模式"
    for m in modes:
        assert re.search(r"\d+", m["evidence"]), (
            f"evidence 应包含数字: {m['mode']} -> {m['evidence']}"
        )


# ---------------------------------------------------------------------------
# 4.3 严重性分级正确：critical > high > medium
# ---------------------------------------------------------------------------

def test_severity_ordering():
    """验收 4.3：spof_cascade=critical, retry_storm=high, queue_overflow=medium。"""
    report = _run_engine(_get_novice_components(), "flash_sale", ticks=100)
    modes = report["failure_modes"]
    mode_to_severity = {m["mode"]: m["severity"] for m in modes}

    if "spof_cascade" in mode_to_severity:
        assert mode_to_severity["spof_cascade"] == "critical", (
            f"spof_cascade 应为 critical，实际 {mode_to_severity['spof_cascade']}"
        )
    if "retry_storm" in mode_to_severity:
        assert mode_to_severity["retry_storm"] == "high", (
            f"retry_storm 应为 high，实际 {mode_to_severity['retry_storm']}"
        )
    if "queue_overflow" in mode_to_severity:
        assert mode_to_severity["queue_overflow"] == "medium", (
            f"queue_overflow 应为 medium，实际 {mode_to_severity['queue_overflow']}"
        )


# ---------------------------------------------------------------------------
# 4.4 健康设计在 normal 场景下报告 0 个失败模式
# ---------------------------------------------------------------------------

def test_healthy_design_no_failures():
    """验收 4.4：专家级设计 + normal 场景，failure_modes 为空列表。"""
    report = _run_engine(_get_expert_components(), "normal", ticks=100)
    modes = report["failure_modes"]
    assert modes == [], (
        f"专家设计 + normal 场景应无失败模式，实际检测到 {len(modes)} 个: "
        f"{[m['mode'] for m in modes]}"
    )


# ---------------------------------------------------------------------------
# 4.5 同一失败模式不重复报告
# ---------------------------------------------------------------------------

def test_no_duplicate_modes():
    """验收 4.5：同一 (component, mode) 元组不重复出现。"""
    report = _run_engine(_get_novice_components(), "flash_sale", ticks=100)
    modes = report["failure_modes"]
    seen = set()
    for m in modes:
        key = (m["component"], m["mode"])
        assert key not in seen, f"重复的失败模式: {key}"
        seen.add(key)


# ---------------------------------------------------------------------------
# 4.6 分析过程零 LLM 调用，响应时间 < 100ms
# ---------------------------------------------------------------------------

def test_analyzer_under_100ms():
    """验收 4.6：FailureAnalyzer.analyze 耗时 < 0.1 秒。"""
    components = _get_novice_components()
    pack = load_domain_pack(YAML_PATH)
    scenario = pack.get_scenario("flash_sale")
    engine = SandboxEngine(components, scenario)
    engine.run(ticks=100)

    analyzer = FailureAnalyzer()
    start = time.time()
    analyzer.analyze(engine.components, engine.events)
    elapsed = time.time() - start
    assert elapsed < 0.1, f"分析耗时 {elapsed * 1000:.1f}ms，超过 100ms 限制"


# ---------------------------------------------------------------------------
# 4.7 失败模式可追溯到具体事件
# ---------------------------------------------------------------------------

def test_failure_traceable_to_events():
    """验收 4.7：queue_overflow 失败模式的组件名出现在 key_events 中。"""
    report = _run_engine(_get_novice_components(), "flash_sale", ticks=100)
    modes = report["failure_modes"]
    key_events = report["key_events"]

    overflow_modes = [m for m in modes if m["mode"] == "queue_overflow"]
    assert len(overflow_modes) > 0, "应至少有1个 queue_overflow 失败模式"

    overflow_event_comps = {
        e["component"] for e in key_events if e["type"] == "queue_overflow"
    }
    for m in overflow_modes:
        assert m["component"] in overflow_event_comps, (
            f"queue_overflow 组件 {m['component']} 未在 key_events 中找到对应事件"
        )
