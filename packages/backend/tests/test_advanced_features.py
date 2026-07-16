"""端到端测试与验证（Task 10）

验证新增功能：
  - message_queue + async 依赖的级联故障阻断
  - scheduled_task 的 cron 触发频率与 jitter 确定性
  - DecisionTreeAnalyzer 的性能预估（吞吐量 / 延迟 / SPOF）
  - DesignReviewer 无 LLM 时的降级行为
  - 新失败模式不影响纯 sync 设计
"""

from pathlib import Path
from unittest.mock import patch

from modules.simulation_engine import SandboxEngine, Component, ComponentType
from modules.decision_tree_analyzer import DecisionTreeAnalyzer
from modules.design_reviewer import review_design
from modules.domain_pack import load_domain_pack
from domain_packs.software_systems_presets import design_novice
from utils.design_spec import to_dict, spec_to_engine_components

YAML_PATH = str(
    Path(__file__).resolve().parent.parent / "src" / "domain_packs" / "software_systems.yaml"
)


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _get_scenario(scenario_id="normal"):
    """加载指定场景。"""
    pack = load_domain_pack(YAML_PATH)
    return pack.get_scenario(scenario_id)


def _novice_spec_dict():
    """返回 novice 预设的 dict 形式（供 DecisionTreeAnalyzer.analyze 使用）。"""
    return to_dict(design_novice())


# ---------------------------------------------------------------------------
# 1. message_queue + async 依赖阻断级联故障
# ---------------------------------------------------------------------------

def test_message_queue_async_decoupling():
    """验证 message_queue + async 依赖能阻断级联故障。

    拓扑: gateway → service → "message_queue:async" → consumer_service
    consumer_service 崩溃时，async 依赖使 service 立即返回，
    gateway / service 不受影响。
    """
    components = {
        "gateway": Component(
            name="gateway", type=ComponentType.GATEWAY, replicas=2,
            cpu_per_replica=100, memory_limit=4096, queue_capacity=500,
            timeout_ms=2000, dependencies=["service"],
        ),
        "service": Component(
            name="service", type=ComponentType.SERVICE, replicas=2,
            cpu_per_replica=100, memory_limit=4096, queue_capacity=500,
            timeout_ms=2000, retry_count=0,
            dependencies=["message_queue:async"],
        ),
        "message_queue": Component(
            name="message_queue", type=ComponentType.MESSAGE_QUEUE, replicas=2,
            cpu_per_replica=100, memory_limit=4096, queue_capacity=500,
            timeout_ms=2000, consumer_lag=2,
            dependencies=["consumer_service"],
        ),
        "consumer_service": Component(
            name="consumer_service", type=ComponentType.SERVICE, replicas=1,
            cpu_per_replica=100, memory_limit=4096, queue_capacity=500,
            timeout_ms=2000, retry_count=0, dependencies=[],
        ),
    }
    scenario = _get_scenario("normal")
    engine = SandboxEngine(components, scenario)

    # 崩溃 consumer_service，使消息无法被消费（积压在 message_queue）
    engine.components["consumer_service"].healthy_replicas = 0

    engine.run(ticks=30)

    gateway = engine.components["gateway"]
    service = engine.components["service"]
    assert gateway.success_count > 0, (
        "consumer 崩溃后 gateway 应仍有成功请求（async 解耦阻断级联故障）"
    )
    assert service.success_count > 0, (
        "consumer 崩溃后 service 应仍有成功请求（async 依赖立即返回）"
    )


# ---------------------------------------------------------------------------
# 2. scheduled_task 按 cron_interval 触发
# ---------------------------------------------------------------------------

def test_scheduled_task_trigger_frequency():
    """验证 scheduled_task 按 cron_interval 触发。

    cron_interval=10，运行 100 ticks，trigger_count 应在 9-11 之间
    （允许 jitter 偏差；此处 jitter=0，理论值为 10）。
    """
    components = {
        "scheduled_task": Component(
            name="scheduled_task", type=ComponentType.SCHEDULED_TASK, replicas=1,
            cpu_per_replica=50, memory_limit=1024, queue_capacity=100,
            timeout_ms=1000, cron_interval=10, jitter=0,
            dependencies=["database"],
        ),
        "database": Component(
            name="database", type=ComponentType.DATABASE, replicas=1,
            cpu_per_replica=100, memory_limit=4096, queue_capacity=500,
            timeout_ms=3000, dependencies=[],
        ),
    }
    scenario = _get_scenario("normal")
    engine = SandboxEngine(components, scenario)
    engine.run(ticks=100)

    trigger_count = engine.components["scheduled_task"].trigger_count
    assert 9 <= trigger_count <= 11, (
        f"cron_interval=10 运行 100 ticks，trigger_count 应在 9-11 之间，"
        f"实际 {trigger_count}"
    )


# ---------------------------------------------------------------------------
# 3. scheduled_task jitter 确定性
# ---------------------------------------------------------------------------

def test_scheduled_task_jitter_deterministic():
    """验证 jitter 的确定性——相同输入产生相同输出。"""

    def build_and_run():
        components = {
            "scheduled_task": Component(
                name="scheduled_task", type=ComponentType.SCHEDULED_TASK,
                replicas=1, cpu_per_replica=50, memory_limit=1024,
                queue_capacity=100, timeout_ms=1000,
                cron_interval=10, jitter=3,
                dependencies=["database"],
            ),
            "database": Component(
                name="database", type=ComponentType.DATABASE, replicas=1,
                cpu_per_replica=100, memory_limit=4096, queue_capacity=500,
                timeout_ms=3000, dependencies=[],
            ),
        }
        scenario = _get_scenario("normal")
        engine = SandboxEngine(components, scenario)
        engine.run(ticks=100)
        return engine.components["scheduled_task"].trigger_count

    count1 = build_and_run()
    count2 = build_and_run()
    assert count1 == count2, (
        f"相同输入两次运行 trigger_count 不一致: {count1} vs {count2}"
    )


# ---------------------------------------------------------------------------
# 4. 性能预估 - 吞吐量
# ---------------------------------------------------------------------------

def test_performance_estimates_throughput():
    """验证性能预估包含吞吐量信息。"""
    spec = _novice_spec_dict()
    report = DecisionTreeAnalyzer().analyze(spec)
    throughput = report.performance_estimates["throughput"]

    assert isinstance(throughput, list) and len(throughput) > 0, (
        "throughput 应为非空 list"
    )
    bottlenecks = [t for t in throughput if t.get("is_bottleneck")]
    assert len(bottlenecks) >= 1, "至少应有一个组件 is_bottleneck=True"


# ---------------------------------------------------------------------------
# 5. 性能预估 - 延迟
# ---------------------------------------------------------------------------

def test_performance_estimates_latency():
    """验证性能预估包含延迟信息。"""
    spec = _novice_spec_dict()
    report = DecisionTreeAnalyzer().analyze(spec)
    latency = report.performance_estimates["latency"]

    assert latency["estimated_ms"] > 0, (
        f"estimated_ms 应 > 0，实际 {latency['estimated_ms']}"
    )
    assert isinstance(latency["critical_path"], list) and len(latency["critical_path"]) > 0, (
        "critical_path 应为非空 list"
    )


# ---------------------------------------------------------------------------
# 6. 性能预估 - SPOF 风险
# ---------------------------------------------------------------------------

def test_performance_estimates_spof():
    """验证性能预估包含 SPOF 风险。"""
    spec = _novice_spec_dict()
    report = DecisionTreeAnalyzer().analyze(spec)
    spof = report.performance_estimates["spof_risk"]

    assert isinstance(spof, list), "spof_risk 应为 list"
    spof_names = [s["component"] for s in spof]
    assert "database" in spof_names, (
        f"database（replicas=1 且被依赖）应出现在 spof_risk 中，实际 {spof_names}"
    )


# ---------------------------------------------------------------------------
# 7. DesignReviewer 无 LLM 时返回空列表
# ---------------------------------------------------------------------------

def test_design_reviewer_no_llm_returns_empty():
    """验证 DesignReviewer 在无 LLM 时返回空列表。"""
    spec = _novice_spec_dict()
    with patch("modules.design_reviewer.get_llm_config", return_value=None):
        issues, message = review_design(spec)

    assert issues == [], "无 LLM 配置时 issues 应为空列表"
    assert "未配置" in message, f"状态消息应包含'未配置'，实际: {message}"


# ---------------------------------------------------------------------------
# 8. 新失败模式不影响纯 sync 设计
# ---------------------------------------------------------------------------

def test_new_failure_modes_only_trigger_with_new_components():
    """验证新失败模式不影响纯 sync 设计。

    novice 预设无 message_queue / scheduled_task，失败模式列表中
    不应包含 consumer_lag_accumulation 和 scheduled_task_thundering_herd。
    """
    components = spec_to_engine_components(design_novice())
    scenario = _get_scenario("flash_sale")
    engine = SandboxEngine(components, scenario)
    report = engine.run(ticks=100)

    mode_names = [m["mode"] for m in report["failure_modes"]]
    assert "consumer_lag_accumulation" not in mode_names, (
        "纯 sync 设计不应触发 consumer_lag_accumulation"
    )
    assert "scheduled_task_thundering_herd" not in mode_names, (
        "纯 sync 设计不应触发 scheduled_task_thundering_herd"
    )
