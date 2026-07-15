"""端到端测试：BlackBoxSpec → ScenarioGenerator → SandboxEngine → RemediationGuide 完整链路。

验证 ReckonBox 核心管线的端到端行为，覆盖 Task 9 的四个子任务：
  1. 完整链路：从黑盒规格到修复引导报告的全流程贯通
  2. variant parameter_mapping 对引擎行为的实际影响（timeout_ms 翻倍）
  3. LLM 全链路降级（所有 LLM 模块返回 None 时流程不崩溃）
  4. E2E 确定性（相同输入产生相同输出）

所有 LLM 相关测试走降级路径或显式 mock，不依赖真实 LLM 调用。

注意：conftest.py 的 autouse fixture 仅 patch modules.ai_tutor.get_llm_config，
不影响 modules.remediation_guide / teaching_cards 等模块的本地 get_llm_config 引用
（from modules.ai_tutor import get_llm_config 已绑定到各自命名空间）。
因此本文件中调用 generate_guidance 的测试自行 patch 这些本地引用。
"""

import copy
from contextlib import contextmanager
from unittest.mock import patch

from utils.blackbox_spec import BlackBoxSpec, DesignVariant, to_dict as blackbox_to_dict
from utils.design_spec import to_dict as spec_to_dict
from domain_packs.software_systems_presets import design_novice
from modules.scenario_generator import generate_scenarios
from modules.simulation_engine import SandboxEngine
from modules.remediation_guide import (
    FALLBACK_GUIDANCE,
    RemediationReport,
    generate_guidance,
)


# ---------------------------------------------------------------------------
# 辅助构造
# ---------------------------------------------------------------------------

def _build_blackbox_spec(
    max_qps=300,
    exception_scenarios=None,
    design_variants=None,
) -> BlackBoxSpec:
    """构造测试用 BlackBoxSpec，含功能点/性能边界/异常场景/设计变更点。"""
    return BlackBoxSpec(
        scenario_description="电商订单系统",
        functional_points=["创建订单", "查询订单"],
        performance_bounds={"max_qps": max_qps, "target_latency_ms": 200},
        exception_scenarios=exception_scenarios or [
            {"id": "db_slow", "name": "数据库变慢", "description": "数据库处理能力下降"},
        ],
        design_variants=design_variants or [],
    )


def _build_lock_contention_variant() -> DesignVariant:
    """构造一个 parameter_mapping variant：选中后 database 的 timeout_ms 翻倍。"""
    return DesignVariant(
        id="lock_contention",
        name="锁竞争场景",
        description="是否存在数据库锁竞争",
        options=[
            {"id": "yes", "label": "存在", "description": "存在锁竞争"},
            {"id": "no", "label": "不存在", "description": "无锁竞争"},
        ],
        impact_type="parameter_mapping",
        parameter_mappings=[
            {
                "option_id": "yes",
                "component_type": "database",
                "param": "timeout_ms",
                "multiplier": 2,
                "offset": 0,
            },
        ],
    )


def _get_novice_components() -> list:
    """从 design_novice 预设获取组件列表（list[dict] 格式，深拷贝避免污染）。"""
    spec_dict = spec_to_dict(design_novice())
    return copy.deepcopy(spec_dict["components"])


def _components_list_to_dict(components_list: list) -> dict:
    """将 list[dict] 转为 {name: dict} 格式，供 SandboxEngine 使用。"""
    return {c["name"]: c for c in components_list}


def _pick_flash(scenarios):
    """从场景列表中取出洪峰场景。"""
    return next(s for s in scenarios if s.id == "dynamic_flash")


@contextmanager
def _all_llm_degraded():
    """patch 所有持有 get_llm_config 本地引用的模块，模拟全链路 LLM 降级。

    覆盖：remediation_guide / teaching_cards / parameter_advisor /
          scenario_parser / variant_identifier。
    scenario_generator 与 simulation_engine 是纯规则引擎，不调用 LLM。
    """
    with patch("modules.remediation_guide.get_llm_config", return_value=None), \
         patch("modules.teaching_cards.get_llm_config", return_value=None), \
         patch("modules.parameter_advisor.get_llm_config", return_value=None), \
         patch("modules.scenario_parser.get_llm_config", return_value=None), \
         patch("modules.variant_identifier.get_llm_config", return_value=None):
        yield


# ---------------------------------------------------------------------------
# 1. 完整链路
# ---------------------------------------------------------------------------

def test_full_chain_blackbox_to_remediation():
    """完整链路：BlackBoxSpec → generate_scenarios → SandboxEngine → generate_guidance。

    构造含 max_qps/异常场景/parameter_mapping variant 的 BlackBoxSpec，
    用 design_novice 组件列表应用 variant 后取洪峰场景运行引擎，
    再将 report 传给 generate_guidance 生成修复引导报告。
    """
    spec = _build_blackbox_spec(
        max_qps=300,
        design_variants=[_build_lock_contention_variant()],
    )
    components = _get_novice_components()
    blackbox_dict = blackbox_to_dict(spec)
    # design_spec 使用应用 variant 后的实际组件，便于教学卡片引用真实参数
    design_dict = {"domain": "software_systems", "components": components}

    # 应用 variant（选中锁竞争 → database.timeout_ms 翻倍）并生成场景
    scenarios = generate_scenarios(
        spec, selected_option_ids={"lock_contention": "yes"}, components=components
    )

    # 取洪峰场景运行引擎
    flash = _pick_flash(scenarios)
    report = SandboxEngine(_components_list_to_dict(components), flash).run(ticks=100)

    # 生成修复引导（LLM 不可用 → 走 FALLBACK_GUIDANCE）
    with _all_llm_degraded():
        result = generate_guidance(
            report, design_spec=design_dict, blackbox_spec=blackbox_dict
        )

    # 断言三部分结构合理
    assert isinstance(result, RemediationReport)
    assert isinstance(result.crash_analysis, list)
    assert isinstance(result.teaching_cards, list)
    assert isinstance(result.design_guidance, list)

    # crash_analysis 每项有 failure_mode/component/severity/rank 字段
    for item in result.crash_analysis:
        assert "failure_mode" in item
        assert "component" in item
        assert "severity" in item
        assert "rank" in item

    # design_guidance 在 LLM 不可用时使用 FALLBACK_GUIDANCE（非空）
    assert len(result.design_guidance) > 0
    for item in result.design_guidance:
        assert item["question"] == FALLBACK_GUIDANCE[item["failure_mode"]]


# ---------------------------------------------------------------------------
# 2. variant parameter_mapping 对引擎行为的实际影响
# ---------------------------------------------------------------------------

def test_variant_parameter_mapping_impacts_engine():
    """variant parameter_mapping 对引擎行为的实际影响：timeout_ms 翻倍。

    两次运行：一次不选 variant，一次选中 variant。
    断言选中后 database 的 timeout_ms 是原来的 2 倍，且两次引擎运行都不崩溃。
    """
    variant = _build_lock_contention_variant()
    spec = _build_blackbox_spec(max_qps=300, design_variants=[variant])

    # ---- 不选 variant ----
    components_no = _get_novice_components()
    scenarios_no = generate_scenarios(
        spec, selected_option_ids=None, components=components_no
    )
    db_no = next(c for c in components_no if c["type"] == "database")
    timeout_no = db_no["timeout_ms"]
    flash_no = _pick_flash(scenarios_no)
    report_no = SandboxEngine(
        _components_list_to_dict(components_no), flash_no
    ).run(ticks=100)

    # ---- 选中 variant ----
    components_yes = _get_novice_components()
    scenarios_yes = generate_scenarios(
        spec, selected_option_ids={"lock_contention": "yes"}, components=components_yes
    )
    db_yes = next(c for c in components_yes if c["type"] == "database")
    timeout_yes = db_yes["timeout_ms"]
    flash_yes = _pick_flash(scenarios_yes)
    report_yes = SandboxEngine(
        _components_list_to_dict(components_yes), flash_yes
    ).run(ticks=100)

    # 断言：选中 variant 后 database 的 timeout_ms 翻倍
    assert timeout_yes == timeout_no * 2

    # 断言：两次运行的 report 都能正常生成（不崩溃）
    assert isinstance(report_no, dict)
    assert isinstance(report_yes, dict)
    assert "failure_modes" in report_no
    assert "failure_modes" in report_yes
    assert "component_stats" in report_no
    assert "component_stats" in report_yes

    # 可选观察：超时事件差异。timeout_ms 翻倍后请求等待更久才超时，
    # 理论上 REQUEST_TIMEOUT 事件不应增加。这里仅做非强约束观察。
    timeouts_no = [
        e for e in report_no.get("key_events", [])
        if e.get("type") == "request_timeout"
    ]
    timeouts_yes = [
        e for e in report_yes.get("key_events", [])
        if e.get("type") == "request_timeout"
    ]
    # 更长的超时阈值不应导致超时事件显著增加
    assert len(timeouts_yes) <= len(timeouts_no) + 50


# ---------------------------------------------------------------------------
# 3. LLM 全链路降级
# ---------------------------------------------------------------------------

def test_llm_full_chain_degradation():
    """LLM 全链路降级：所有 LLM 模块返回 None 时流程不崩溃。

    patch 所有相关模块的 get_llm_config 返回 None，
    generate_scenarios（纯规则引擎）→ SandboxEngine.run → generate_guidance 全程不抛异常，
    RemediationReport.design_guidance 使用 FALLBACK_GUIDANCE（非空），
    teaching_cards 在 LLM 不可用时返回原模板（personalize_card 降级）。
    """
    with _all_llm_degraded():
        spec = _build_blackbox_spec(max_qps=300)
        components = _get_novice_components()
        blackbox_dict = blackbox_to_dict(spec)
        design_dict = {"domain": "software_systems", "components": components}

        # generate_scenarios 纯规则引擎，不受 LLM 影响
        scenarios = generate_scenarios(spec, components=components)
        flash = _pick_flash(scenarios)
        report = SandboxEngine(
            _components_list_to_dict(components), flash
        ).run(ticks=100)

        # generate_guidance 内部调用 remediation_guide/teaching_cards 的 get_llm_config
        result = generate_guidance(
            report, design_spec=design_dict, blackbox_spec=blackbox_dict
        )

    # 断言：整个流程不抛异常，返回合法 RemediationReport
    assert isinstance(result, RemediationReport)
    assert isinstance(result.crash_analysis, list)
    assert isinstance(result.teaching_cards, list)
    assert isinstance(result.design_guidance, list)

    # 断言：design_guidance 使用 FALLBACK_GUIDANCE（非空，包含已知失败模式的引导问题）
    assert len(result.design_guidance) > 0
    for item in result.design_guidance:
        assert item["failure_mode"] in FALLBACK_GUIDANCE
        assert item["question"] == FALLBACK_GUIDANCE[item["failure_mode"]]

    # 断言：teaching_cards 在 LLM 不可用时返回模板
    # personalize_card 降级返回原模板 dict（含 title/explanation 等字段）
    assert len(result.teaching_cards) > 0
    for item in result.teaching_cards:
        assert "failure_mode" in item
        assert "card" in item
        card = item["card"]
        assert isinstance(card, dict)
        assert "title" in card
        assert "explanation" in card


# ---------------------------------------------------------------------------
# 4. E2E 确定性
# ---------------------------------------------------------------------------

def test_scenario_generator_determinism_in_e2e():
    """E2E 确定性：相同 BlackBoxSpec + 相同 selected_option_ids + 相同 components，
    两次调用 generate_scenarios 生成的 scenarios 完全一致。

    比较 phases 的 ticks/rps 与 inject 的 target/action 等字段。
    """
    spec = _build_blackbox_spec(
        max_qps=300,
        exception_scenarios=[
            {"id": "db_slow", "name": "数据库变慢", "description": "数据库查询变慢"},
            {"id": "svc_crash", "name": "服务崩溃", "description": "支付服务突然崩溃"},
        ],
        design_variants=[_build_lock_contention_variant()],
    )
    selected = {"lock_contention": "yes"}

    # 两次独立调用，各自使用深拷贝的 components 避免相互污染
    components_a = _get_novice_components()
    components_b = _get_novice_components()

    first = generate_scenarios(
        spec, selected_option_ids=selected, components=components_a
    )
    second = generate_scenarios(
        spec, selected_option_ids=selected, components=components_b
    )

    # 场景数量一致
    assert len(first) == len(second)

    for s1, s2 in zip(first, second):
        assert s1.id == s2.id
        assert s1.name == s2.name

        # phases 的 ticks/rps 完全一致
        assert len(s1.phases) == len(s2.phases)
        for p1, p2 in zip(s1.phases, s2.phases):
            assert p1.ticks == p2.ticks
            assert p1.rps == p2.rps

        # inject 的 target/action 等字段完全一致
        assert len(s1.inject) == len(s2.inject)
        for i1, i2 in zip(s1.inject, s2.inject):
            assert i1.target == i2.target
            assert i1.action == i2.action
            assert i1.start_tick == i2.start_tick
            assert i1.interval == i2.interval
            assert i1.at_tick == i2.at_tick
            assert i1.amount == i2.amount

    # 额外断言：两次 variant 参数映射结果也一致（database.timeout_ms 相同）
    db_a = next(c for c in components_a if c["type"] == "database")
    db_b = next(c for c in components_b if c["type"] == "database")
    assert db_a["timeout_ms"] == db_b["timeout_ms"]
