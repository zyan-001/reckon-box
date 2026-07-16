"""
策略模板注册表
==============
ReckonBox 2.0 的封闭策略模板注册表，包含 6 个内置模板。

每个模板定义了一种容错/调用策略的参数模式、容量计算、断言和负载放大系数，
供 Design Builder 和 Simulation Engine 引用。

模板列表：
- sync_direct: 同步直连调用，无容错
- retry_no_backoff: 立即重试，无退避间隔
- retry_with_backoff: 指数退避重试
- async_fire_and_forget: 异步写入即返回
- degrade_fallback: 降级到备用组件
- circuit_break: 熔断器模式

纯数据结构模块，不依赖任何 LLM 或 Streamlit。
"""

from dataclasses import dataclass, field
from typing import Callable, Optional


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class StrategyTemplate:
    """策略模板定义。

    字段：
    - id: 模板唯一标识
    - description: 模板描述
    - params_schema: 参数名 → 类型，用于校验
    - calculate_capacity: callable(params, replicas) → int，计算有效容量
    - assertions: 内置断言/崩溃条件列表
    - effective_load_multiplier: 可选 callable(params) → int，故障时负载放大系数
    """
    id: str
    description: str
    params_schema: dict  # {参数名: 类型}
    calculate_capacity: Callable[[dict, int], int]
    assertions: list = field(default_factory=list)
    effective_load_multiplier: Optional[Callable[[dict], int]] = None


# ---------------------------------------------------------------------------
# 内置模板定义
# ---------------------------------------------------------------------------

_TEMPLATES: dict[str, StrategyTemplate] = {}


def _register(template: StrategyTemplate) -> StrategyTemplate:
    """注册模板到内部注册表，返回模板本身。"""
    _TEMPLATES[template.id] = template
    return template


# 1. 同步直连调用，无容错
_register(StrategyTemplate(
    id="sync_direct",
    description="同步直连调用，无容错",
    params_schema={"timeout_ms": int, "capacity_per_replica": int},
    calculate_capacity=lambda params, replicas: replicas * params["capacity_per_replica"],
    assertions=[{"metric": "dependency_failed", "action": "fail_immediately"}],
))

# 2. 立即重试，无退避间隔
_register(StrategyTemplate(
    id="retry_no_backoff",
    description="立即重试，无退避间隔",
    params_schema={"max_retries": int, "capacity_per_replica": int},
    calculate_capacity=lambda params, replicas: replicas * params["capacity_per_replica"],
    effective_load_multiplier=lambda params: params["max_retries"] + 1,
    assertions=[
        {"metric": "dependency_failed", "action": "retry_immediately"},
        {"condition": "retries_exhausted", "crash_risk": "retry_storm"},
    ],
))

# 3. 指数退避重试
_register(StrategyTemplate(
    id="retry_with_backoff",
    description="指数退避重试",
    params_schema={"max_retries": int, "base_delay_ms": int, "capacity_per_replica": int},
    calculate_capacity=lambda params, replicas: replicas * params["capacity_per_replica"],
    assertions=[{"metric": "dependency_failed", "action": "retry_with_delay"}],
))

# 4. 异步写入即返回
_register(StrategyTemplate(
    id="async_fire_and_forget",
    description="异步写入即返回",
    params_schema={"consumer_lag": int, "capacity_per_replica": int},
    calculate_capacity=lambda params, replicas: replicas * params["capacity_per_replica"],
    assertions=[{"metric": "queue_full", "action": "drop_or_backlog"}],
))

# 5. 降级到备用组件
_register(StrategyTemplate(
    id="degrade_fallback",
    description="降级到备用组件",
    params_schema={"fallback_target": str, "capacity_per_replica": int},
    calculate_capacity=lambda params, replicas: replicas * params["capacity_per_replica"],
    assertions=[
        {"metric": "dependency_failed", "action": "check_fallback"},
        {"condition": "fallback_unavailable", "crash_risk": "cascading_failure"},
    ],
))

# 6. 熔断器模式
_register(StrategyTemplate(
    id="circuit_break",
    description="熔断器模式",
    params_schema={"failure_threshold": int, "recovery_ticks": int, "capacity_per_replica": int},
    calculate_capacity=lambda params, replicas: replicas * params["capacity_per_replica"],
    assertions=[
        {"metric": "consecutive_failures", "action": "open_circuit"},
        {"condition": "circuit_open", "effective_capacity": 0},
    ],
))


# ---------------------------------------------------------------------------
# 接口函数
# ---------------------------------------------------------------------------

def get_template(template_id: str) -> Optional[StrategyTemplate]:
    """根据模板 ID 获取模板，不存在则返回 None。"""
    return _TEMPLATES.get(template_id)


def get_all_templates() -> dict[str, StrategyTemplate]:
    """返回所有已注册模板的字典（浅拷贝，防止外部修改注册表）。"""
    return dict(_TEMPLATES)


def validate_params(template_id: str, params: dict) -> list[str]:
    """校验参数是否符合模板的 params_schema。

    Args:
        template_id: 模板 ID
        params: 待校验的参数字典

    Returns:
        错误信息列表，空列表表示校验通过
    """
    template = get_template(template_id)
    if template is None:
        return [f"未知模板 ID: '{template_id}'"]

    errors = []

    # 检查缺少的必需参数
    for param_name, expected_type in template.params_schema.items():
        if param_name not in params:
            errors.append(f"缺少必需参数: '{param_name}'")
            continue

        value = params[param_name]

        # 类型校验：bool 是 int 的子类，需特殊排除
        if expected_type is int and isinstance(value, bool):
            errors.append(f"参数 '{param_name}' 应为 int，实际为 bool")
            continue

        if not isinstance(value, expected_type):
            errors.append(
                f"参数 '{param_name}' 应为 {expected_type.__name__}，"
                f"实际为 {type(value).__name__}"
            )
            continue

        # 数值范围校验：int 类型不允许负值
        if expected_type is int and value < 0:
            errors.append(f"参数 '{param_name}' 不能为负数，当前值: {value}")

    # 检查多余的参数（非 schema 定义的）
    for param_name in params:
        if param_name not in template.params_schema:
            errors.append(f"未知参数: '{param_name}'")

    return errors


def default_params(template_id: str) -> dict:
    """返回模板的默认参数（最低配置）。

    默认值规则：
    - int 类型：0（capacity_per_replica 默认 1，consumer_lag 默认 0）
    - str 类型：空字符串
    """
    template = get_template(template_id)
    if template is None:
        raise ValueError(f"未知模板 ID: '{template_id}'")

    result = {}
    for param_name, expected_type in template.params_schema.items():
        if expected_type is int:
            # capacity_per_replica 最低为 1，其余 int 默认 0
            if param_name == "capacity_per_replica":
                result[param_name] = 1
            else:
                result[param_name] = 0
        elif expected_type is str:
            result[param_name] = ""
        else:
            # 其他类型默认 None
            result[param_name] = None
    return result
