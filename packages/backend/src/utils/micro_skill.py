"""
MicroSkill 数据结构模块
======================
ReckonBox 2.0 的组件行为单元，将策略模板与实例化参数、断言、步骤树绑定。

核心概念：
- StepNode: 步骤树节点，描述操作步骤的层次结构
- MicroSkill: 组件的行为单元 = 策略模板 + 实例化参数 + 断言 + 步骤树

纯数据结构模块，不依赖任何 LLM 或 Streamlit。
"""

import json
from dataclasses import dataclass, field

from utils.strategy_templates import (
    StrategyTemplate,
    default_params,
    get_template,
    validate_params,
)


# ---------------------------------------------------------------------------
# StepNode 步骤树节点
# ---------------------------------------------------------------------------

@dataclass
class StepNode:
    """步骤树节点。

    字段：
    - id: 节点唯一标识
    - label: 步骤名称
    - description: 自然语言描述
    - children: 子步骤列表（默认空列表）
    """
    id: str
    label: str
    description: str
    children: list = field(default_factory=list)  # list[StepNode]


def step_node_to_dict(node: StepNode) -> dict:
    """递归序列化 StepNode 为字典。

    Args:
        node: 待序列化的 StepNode

    Returns:
        包含所有层级子节点的字典
    """
    return {
        "id": node.id,
        "label": node.label,
        "description": node.description,
        "children": [step_node_to_dict(child) for child in node.children],
    }


def step_node_from_dict(data: dict) -> StepNode:
    """从字典递归反序列化为 StepNode。

    Args:
        data: 待反序列化的字典

    Returns:
        包含所有层级子节点的 StepNode
    """
    return StepNode(
        id=data["id"],
        label=data["label"],
        description=data["description"],
        children=[step_node_from_dict(child) for child in data.get("children", [])],
    )


# ---------------------------------------------------------------------------
# MicroSkill 行为单元
# ---------------------------------------------------------------------------

@dataclass
class MicroSkill:
    """组件的行为单元 = 策略模板 + 实例化参数 + 断言 + 步骤树。

    字段：
    - strategy_template_id: 策略模板 ID（引用 StrategyTemplate）
    - params: 实例化参数（需符合模板 params_schema）
    - assertions: 断言列表（模板内置 + 用户自定义）
    - step_tree: 步骤树（可选，描述操作步骤的层次结构）
    """
    strategy_template_id: str
    params: dict = field(default_factory=dict)
    assertions: list = field(default_factory=list)
    step_tree: StepNode | None = None


# ---------------------------------------------------------------------------
# 序列化 / 反序列化
# ---------------------------------------------------------------------------

def to_dict(skill: MicroSkill) -> dict:
    """序列化 MicroSkill 为字典。

    Args:
        skill: 待序列化的 MicroSkill

    Returns:
        包含所有字段的字典，step_tree 递归序列化
    """
    result = {
        "strategy_template_id": skill.strategy_template_id,
        "params": skill.params,
        "assertions": skill.assertions,
    }
    if skill.step_tree is not None:
        result["step_tree"] = step_node_to_dict(skill.step_tree)
    else:
        result["step_tree"] = None
    return result


def from_dict(data: dict) -> MicroSkill:
    """从字典反序列化为 MicroSkill。

    Args:
        data: 待反序列化的字典

    Returns:
        完整的 MicroSkill 对象
    """
    step_tree = None
    if data.get("step_tree") is not None:
        step_tree = step_node_from_dict(data["step_tree"])
    return MicroSkill(
        strategy_template_id=data["strategy_template_id"],
        params=data.get("params", {}),
        assertions=data.get("assertions", []),
        step_tree=step_tree,
    )


def to_json(skill: MicroSkill) -> str:
    """JSON 序列化 MicroSkill。

    Args:
        skill: 待序列化的 MicroSkill

    Returns:
        JSON 字符串
    """
    return json.dumps(to_dict(skill), ensure_ascii=False)


def from_json(json_str: str) -> MicroSkill:
    """JSON 反序列化为 MicroSkill。

    Args:
        json_str: JSON 字符串

    Returns:
        完整的 MicroSkill 对象
    """
    return from_dict(json.loads(json_str))


# ---------------------------------------------------------------------------
# 模板绑定与默认创建
# ---------------------------------------------------------------------------

def resolve_template(skill: MicroSkill) -> StrategyTemplate:
    """绑定策略模板并校验 params。

    流程：
    1. 根据 strategy_template_id 查找模板，不存在则抛 ValueError
    2. 调用 validate_params 校验 params
    3. 校验失败（有错误）则抛 ValueError 包含错误列表
    4. 成功则返回 StrategyTemplate 对象

    Args:
        skill: 待绑定的 MicroSkill

    Returns:
        绑定成功的 StrategyTemplate 对象

    Raises:
        ValueError: 模板不存在或参数校验失败
    """
    template = get_template(skill.strategy_template_id)
    if template is None:
        raise ValueError(f"策略模板不存在: '{skill.strategy_template_id}'")

    errors = validate_params(skill.strategy_template_id, skill.params)
    if errors:
        raise ValueError(f"参数校验失败: {errors}")

    return template


def default_skill(template_id: str = "sync_direct") -> MicroSkill:
    """创建默认 MicroSkill。

    使用 default_params(template_id) 填充 params，
    从模板中复制 assertions。

    Args:
        template_id: 策略模板 ID，默认 "sync_direct"

    Returns:
        填充了默认参数和模板断言的 MicroSkill

    Raises:
        ValueError: 模板不存在（由 default_params 抛出）
    """
    template = get_template(template_id)
    if template is None:
        raise ValueError(f"策略模板不存在: '{template_id}'")

    return MicroSkill(
        strategy_template_id=template_id,
        params=default_params(template_id),
        assertions=list(template.assertions),  # 浅拷贝模板断言
    )
