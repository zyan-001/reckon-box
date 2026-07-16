"""
ScenarioGenerator - 动态场景生成
================================
基于 BlackBoxSpec 和用户选择的变更点，动态生成引擎可用的 Scenario 对象列表。

纯规则引擎，无 LLM 调用。相同输入产生相同输出（确定性）。

生成 3 类场景：
- 正常流量场景：稳定 rps = max_qps // 10
- 洪峰场景：4 阶段（预热/洪峰/回落/恢复），峰值 = max_qps
- 异常场景：每个 exception_scenario 生成一个，含故障注入

性能边界缺失时使用默认 max_qps=30。
"""

from typing import Optional, Union

from utils.blackbox_spec import BlackBoxSpec, apply_variant_mappings
from modules.domain_pack import Scenario, Phase, Inject


DEFAULT_MAX_QPS = 30


def generate_scenarios(
    blackbox_spec: Union[BlackBoxSpec, dict],
    selected_option_ids: dict = None,
    components: list = None,
) -> list:
    """基于黑盒规格动态生成压力测试场景。

    Args:
        blackbox_spec: BlackBoxSpec 对象或 dict
        selected_option_ids: {variant_id: option_id} 用户选择的变更点选项
        components: DesignSpec 的 components 列表（用于 variant 参数映射应用）

    Returns:
        list[Scenario]，每个为 domain_pack.Scenario 对象

    副作用:
        若 components 和 selected_option_ids 都提供，会就地应用 variant 参数映射，
        修改 components 内组件的参数（如 timeout_ms）。修改后的 components 会在
        引擎运行时使用，不影响 Scenario 本身。
    """
    # 规格归一化：统一从 BlackBoxSpec 或 dict 提取字段
    if isinstance(blackbox_spec, BlackBoxSpec):
        scenario_description = blackbox_spec.scenario_description
        performance_bounds = blackbox_spec.performance_bounds or {}
        exception_scenarios = blackbox_spec.exception_scenarios or []
        design_variants = blackbox_spec.design_variants or []
    elif isinstance(blackbox_spec, dict):
        scenario_description = blackbox_spec.get("scenario_description", "")
        performance_bounds = blackbox_spec.get("performance_bounds", {}) or {}
        exception_scenarios = blackbox_spec.get("exception_scenarios", []) or []
        design_variants = blackbox_spec.get("design_variants", []) or []
    else:
        raise TypeError(f"不支持的 blackbox_spec 类型: {type(blackbox_spec)}")

    # 提取 max_qps，缺失或非法时使用默认值
    max_qps = _extract_max_qps(performance_bounds)

    # 应用 variant 参数映射（若提供 components 和 selected_option_ids）
    # 修改后的 components 在引擎运行时使用，不写入 Scenario
    if components is not None and selected_option_ids:
        _apply_variant_mappings_inplace(
            components, design_variants, selected_option_ids
        )

    scenarios: list = []

    # 1. 正常流量场景
    scenarios.append(_build_normal_scenario(scenario_description, max_qps))

    # 2. 洪峰场景
    scenarios.append(_build_flash_scenario(scenario_description, max_qps))

    # 3. 异常场景（每个 exception_scenario 生成一个）
    for idx, exc in enumerate(exception_scenarios):
        scenarios.append(
            _build_exception_scenario(
                scenario_description, max_qps, exc, idx, components
            )
        )

    return scenarios


# ---------------------------------------------------------------------------
# 内部辅助函数
# ---------------------------------------------------------------------------

def _extract_max_qps(performance_bounds: dict) -> int:
    """从 performance_bounds 提取 max_qps，缺失或非法时返回默认值 30。"""
    if not performance_bounds:
        return DEFAULT_MAX_QPS
    max_qps = performance_bounds.get("max_qps")
    if max_qps is None or not isinstance(max_qps, (int, float)) or max_qps <= 0:
        return DEFAULT_MAX_QPS
    return int(max_qps)


def _apply_variant_mappings_inplace(
    components: list, variants: list, selected_option_ids: dict
) -> None:
    """应用 variant 参数映射，就地更新 components 内每个组件的参数。

    apply_variant_mappings 返回深拷贝后的新列表，这里把变换后的字段写回原
    dict 对象，保留 list 与 dict 的引用身份，便于调用方观察变更。
    """
    modified = apply_variant_mappings(components, variants, selected_option_ids)
    for orig, mod in zip(components, modified):
        if isinstance(orig, dict) and isinstance(mod, dict):
            orig.update(mod)


def _build_normal_scenario(scenario_description: str, max_qps: int) -> Scenario:
    """正常流量场景：rps = max_qps // 10。"""
    rps = max_qps // 10
    return Scenario(
        id="dynamic_normal",
        name="正常流量",
        description=f"基于场景「{scenario_description}」生成的正常流量，约 {rps} 请求/tick",
        phases=[Phase(ticks=100, rps=rps)],
        inject=[],
    )


def _build_flash_scenario(scenario_description: str, max_qps: int) -> Scenario:
    """洪峰场景：4 阶段（预热/洪峰/回落/恢复）。"""
    return Scenario(
        id="dynamic_flash",
        name="流量洪峰",
        description=f"基于场景「{scenario_description}」生成的洪峰流量，峰值 {max_qps} 请求/tick",
        phases=[
            Phase(ticks=20, rps=max_qps // 10),     # 预热
            Phase(ticks=30, rps=max_qps),           # 洪峰
            Phase(ticks=30, rps=max_qps * 2 // 3),  # 回落
            Phase(ticks=20, rps=max_qps // 5),      # 恢复
        ],
        inject=[],
    )


def _build_exception_scenario(
    scenario_description: str,
    max_qps: int,
    exception_scenario: dict,
    idx: int,
    components: Optional[list],
) -> Scenario:
    """异常场景：rps = max_qps // 5，含根据描述推断的故障注入。"""
    exc_name = exception_scenario.get("name", f"异常场景 {idx}")
    exc_desc = exception_scenario.get("description", "")

    injects = _build_injects_for_exception(exception_scenario, components)

    return Scenario(
        id=f"dynamic_exception_{idx}",
        name=exc_name,
        description=f"基于场景「{scenario_description}」异常「{exc_desc}」生成的故障注入场景",
        phases=[Phase(ticks=100, rps=max_qps // 5)],
        inject=injects,
    )


def _build_injects_for_exception(
    exception_scenario: dict, components: Optional[list]
) -> list:
    """根据异常场景的 name/description 推断 inject 类型。

    规则（按优先级，首条匹配生效）：
    1. 含「数据库/慢/延迟」→ degrade_cpu (target=database, start_tick=10, interval=5, amount=2)
    2. 含「崩溃/宕机/挂掉」→ crash_all (target 推断或默认第一个 service, at_tick=30)
    3. 含「内存/OOM」      → degrade_cpu (target 推断, start_tick=20, interval=10, amount=5)
    4. 其它               → degrade_cpu (target=database, start_tick=20, interval=10, amount=3)
    """
    name = exception_scenario.get("name", "") or ""
    desc = exception_scenario.get("description", "") or ""
    text = f"{name} {desc}"

    # 1. 数据库/慢/延迟 → degrade_cpu (database)
    if any(kw in text for kw in ("数据库", "慢", "延迟")):
        return [Inject(
            target="database",
            action="degrade_cpu",
            start_tick=10,
            interval=5,
            amount=2,
        )]

    # 2. 崩溃/宕机/挂掉 → crash_all
    if any(kw in text for kw in ("崩溃", "宕机", "挂掉")):
        target = _infer_target(components, prefer_type="service", fallback="database")
        return [Inject(
            target=target,
            action="crash_all",
            at_tick=30,
        )]

    # 3. 内存/OOM → degrade_cpu (推断 target)
    if any(kw in text for kw in ("内存", "OOM")):
        target = _infer_target(components, prefer_type="service", fallback="database")
        return [Inject(
            target=target,
            action="degrade_cpu",
            start_tick=20,
            interval=10,
            amount=5,
        )]

    # 4. 默认 → degrade_cpu (database)
    return [Inject(
        target="database",
        action="degrade_cpu",
        start_tick=20,
        interval=10,
        amount=3,
    )]


def _infer_target(
    components: Optional[list],
    prefer_type: str = "service",
    fallback: str = "database",
) -> str:
    """从 components 列表推断 inject target。

    优先返回 prefer_type 类型组件的 name；找不到则返回第一个组件的 name；
    都没有则返回 fallback。
    """
    if not components:
        return fallback
    # 先找 prefer_type 类型
    for comp in components:
        if isinstance(comp, dict) and comp.get("type") == prefer_type:
            name = comp.get("name")
            if name:
                return name
    # 找不到则取第一个组件的 name
    if isinstance(components[0], dict):
        name = components[0].get("name")
        if name:
            return name
    return fallback
