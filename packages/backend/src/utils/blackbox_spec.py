"""
BlackBox Spec 数据结构
======================
场景驱动的黑盒规格，描述用户场景的功能点、性能边界、异常场景和设计变更点。

BlackBoxSpec 是 Module 1 (Scenario Builder) 的核心产物，连接用户 NLP 输入与
Module 2 (Design Builder) 的设计变更点选择。

主要结构：
- DesignVariant: 设计变更点，分两种影响类型
  * parameter_mapping: 通过 multiplier/offset 直接修改组件参数
  * llm_context: 注入 LLM 上下文提示，影响生成建议
- BlackBoxSpec: 完整黑盒规格

纯数据结构模块，不依赖任何 LLM 或 Streamlit。
"""

import copy
import json
from dataclasses import dataclass, field, asdict
from typing import Union


@dataclass
class DesignVariant:
    """单个设计变更点。

    表示学生可选择的设计方案。impact_type 决定变更点的应用方式：
    - parameter_mapping: 选中某 option 后，按 parameter_mappings 修改组件参数
    - llm_context: 仅作为 LLM 上下文提示，不直接改参数

    options 形如 [{"id", "label", "description"}]。
    parameter_mappings（impact_type=parameter_mapping 时）形如：
        [{"option_id", "component_type", "param", "multiplier", "offset"}]
    """
    id: str
    name: str
    description: str
    options: list = field(default_factory=list)
    impact_type: str = "llm_context"
    parameter_mappings: list = field(default_factory=list)


@dataclass
class BlackBoxSpec:
    """完整黑盒规格。

    字段：
    - scenario_description: 用户原始 NLP 输入
    - functional_points: 功能点列表，如 ["创建订单", "查询订单状态"]
    - performance_bounds: 性能边界，如 {max_qps, target_latency_ms, availability_target}
    - exception_scenarios: 异常场景，如 [{id, name, description, trigger_condition}]
    - design_variants: 设计变更点列表，list[DesignVariant]
    """
    scenario_description: str
    functional_points: list = field(default_factory=list)
    performance_bounds: dict = field(default_factory=dict)
    exception_scenarios: list = field(default_factory=list)
    design_variants: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# 内部辅助函数
# ---------------------------------------------------------------------------

def _variant_to_dict(variant: Union[DesignVariant, dict]) -> dict:
    """将 DesignVariant 或 dict 统一转为可序列化的 dict。

    DesignVariant 经 asdict 转换（深拷贝）；已是 dict 则原样返回。
    """
    if isinstance(variant, DesignVariant):
        return asdict(variant)
    if isinstance(variant, dict):
        return variant
    raise TypeError(f"不支持的 variant 类型: {type(variant)}")


def _variant_from_dict(data: Union[DesignVariant, dict]) -> DesignVariant:
    """将 dict 或 DesignVariant 统一转为 DesignVariant。

    dict 中缺失的字段使用 DesignVariant 默认值，保证空 spec 不报错。
    """
    if isinstance(data, DesignVariant):
        return data
    if isinstance(data, dict):
        return DesignVariant(
            id=data.get("id", ""),
            name=data.get("name", ""),
            description=data.get("description", ""),
            options=data.get("options", []),
            impact_type=data.get("impact_type", "llm_context"),
            parameter_mappings=data.get("parameter_mappings", []),
        )
    raise TypeError(f"不支持的 variant 类型: {type(data)}")


# ---------------------------------------------------------------------------
# 序列化 / 反序列化
# ---------------------------------------------------------------------------

def to_dict(spec: BlackBoxSpec) -> dict:
    """将 BlackBoxSpec 转为可 JSON 序列化的 dict。

    design_variants 中的每个 DesignVariant 转为 dict；
    已经是 dict 的 variant 保持不变。
    """
    return {
        "scenario_description": spec.scenario_description,
        "functional_points": list(spec.functional_points),
        "performance_bounds": dict(spec.performance_bounds),
        "exception_scenarios": copy.deepcopy(spec.exception_scenarios),
        "design_variants": [_variant_to_dict(v) for v in spec.design_variants],
    }


def to_json(spec: BlackBoxSpec) -> str:
    """将 BlackBoxSpec 序列化为 JSON 字符串。

    ensure_ascii=False 保留中文等非 ASCII 字符，indent=2 便于阅读。
    """
    return json.dumps(to_dict(spec), ensure_ascii=False, indent=2)


def from_dict(data: dict) -> BlackBoxSpec:
    """从 dict 构建 BlackBoxSpec。

    design_variants 中的每个 dict 转为 DesignVariant；
    已经是 DesignVariant 的保持不变。缺失字段使用默认值，空 spec 不报错。
    """
    variants = [_variant_from_dict(v) for v in data.get("design_variants", [])]
    return BlackBoxSpec(
        scenario_description=data.get("scenario_description", ""),
        functional_points=data.get("functional_points", []),
        performance_bounds=data.get("performance_bounds", {}),
        exception_scenarios=data.get("exception_scenarios", []),
        design_variants=variants,
    )


def from_json(json_str: str) -> BlackBoxSpec:
    """从 JSON 字符串构建 BlackBoxSpec。"""
    return from_dict(json.loads(json_str))


# ---------------------------------------------------------------------------
# 变更点参数映射
# ---------------------------------------------------------------------------

def apply_variant_mappings(
    components: list,
    variants: list,
    selected_option_ids: dict,
) -> list:
    """将参数映射型变更点应用到组件列表。

    Args:
        components: DesignSpec 的 components 列表（dict 格式）
        variants: list[DesignVariant] 或 list[dict]
        selected_option_ids: {variant_id: option_id} 用户选择的选项

    Returns:
        修改后的 components 列表（深拷贝，不修改原列表）

    逻辑：
    - 遍历 variants，找 impact_type="parameter_mapping" 的
    - 对每个 variant，找 selected_option_ids 中对应的 option_id
    - 在 parameter_mappings 中找匹配的 option_id
    - 对 components 中 type 匹配的组件，param *= multiplier，param += offset
    """
    # 深拷贝，不修改原列表
    result = copy.deepcopy(components)

    for variant in variants:
        # 统一获取 variant 属性
        if isinstance(variant, DesignVariant):
            v_id = variant.id
            impact_type = variant.impact_type
            parameter_mappings = variant.parameter_mappings
        elif isinstance(variant, dict):
            v_id = variant.get("id", "")
            impact_type = variant.get("impact_type", "llm_context")
            parameter_mappings = variant.get("parameter_mappings", [])
        else:
            continue

        # 只处理 parameter_mapping 类型，llm_context 跳过
        if impact_type != "parameter_mapping":
            continue

        # 获取该 variant 用户选择的 option_id
        selected_option_id = selected_option_ids.get(v_id)
        if not selected_option_id:
            continue

        # 在 parameter_mappings 中找匹配 option_id 的规则并应用
        for mapping in parameter_mappings:
            if mapping.get("option_id") != selected_option_id:
                continue

            comp_type = mapping.get("component_type")
            param = mapping.get("param")
            # multiplier 默认 1，offset 默认 0，保证只给其一时也能正常工作
            multiplier = mapping.get("multiplier", 1)
            offset = mapping.get("offset", 0)

            if not param:
                continue

            # 对所有 type 匹配的组件应用变换：param = param * multiplier + offset
            for comp in result:
                if isinstance(comp, dict) and comp.get("type") == comp_type and param in comp:
                    comp[param] = comp[param] * multiplier + offset

    return result
