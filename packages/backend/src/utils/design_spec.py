"""
Design Spec 数据结构
====================
连接 Module 2 (Design Builder UI) 和 Module 3 (Simulation Engine) 的结构化表示。

Design Spec 是学生设计系统的 JSON 可序列化表示，包含：
- domain: 领域标识（如 software_systems）
- components: 组件配置列表（gateway/service/database/cache）

接口契约参考 reckonbox_mvp_design.md 第 210-233 行。
"""

import json
from dataclasses import dataclass, field, fields, asdict
from typing import Union

from utils.micro_skill import MicroSkill
from utils.micro_skill import to_dict as micro_skill_to_dict
from utils.micro_skill import from_dict as micro_skill_from_dict


@dataclass
class ComponentConfig:
    """单个组件的配置参数。

    字段与 demo/reckon_sandbox.py 中 Component 的配置字段对应
    （不含运行时状态，仅设计时配置）。
    """
    name: str
    type: str  # gateway/service/database/cache
    replicas: int = 1
    cpu_per_replica: int = 50
    memory_limit: int = 512
    memory_per_request: int = 5
    queue_capacity: int = 200
    timeout_ms: int = 1000
    retry_count: int = 0
    has_circuit_breaker: bool = False
    has_rate_limiter: bool = False
    rate_limit: int = 0
    dependencies: list = field(default_factory=list)
    description: str = ""
    micro_skill: MicroSkill | None = None


@dataclass
class DesignSpec:
    """完整的设计规格，包含领域标识和组件列表。

    components 可以为 list[ComponentConfig] 或 list[dict]，
    转换函数（to_dict/from_dict）会自动处理两种形式。
    """
    domain: str
    components: list  # list[ComponentConfig] 或 list[dict]


# ---------------------------------------------------------------------------
# 内部辅助函数
# ---------------------------------------------------------------------------

def _ensure_component_config(comp: Union[ComponentConfig, dict]) -> ComponentConfig:
    """将 dict 或 ComponentConfig 统一转为 ComponentConfig。

    dict 中不在 ComponentConfig 字段集合内的键会被忽略，
    保证不会因多余字段导致构造失败。
    旧 strategies 字段会被忽略（向后兼容）。
    micro_skill 字段若为 dict 则自动反序列化为 MicroSkill。
    """
    if isinstance(comp, ComponentConfig):
        return comp
    if isinstance(comp, dict):
        valid_keys = {f.name for f in fields(ComponentConfig)}
        # 忽略旧 strategies 字段（向后兼容）
        filtered = {k: v for k, v in comp.items()
                    if k in valid_keys and k != "strategies"}
        # micro_skill 从 dict 反序列化为 MicroSkill
        if "micro_skill" in filtered and isinstance(filtered["micro_skill"], dict):
            filtered["micro_skill"] = micro_skill_from_dict(filtered["micro_skill"])
        return ComponentConfig(**filtered)
    raise TypeError(f"不支持的组件类型: {type(comp)}")


def _get_component_name(comp: Union[ComponentConfig, dict]) -> str:
    """从 ComponentConfig 或 dict 中提取组件名。"""
    if isinstance(comp, ComponentConfig):
        return comp.name
    if isinstance(comp, dict):
        return comp.get("name", "")
    return ""


def _get_component_type(comp: Union[ComponentConfig, dict]) -> str:
    """从 ComponentConfig 或 dict 中提取组件类型。"""
    if isinstance(comp, ComponentConfig):
        return comp.type
    if isinstance(comp, dict):
        return comp.get("type", "")
    return ""


def _get_component_dependencies(comp: Union[ComponentConfig, dict]) -> list:
    """从 ComponentConfig 或 dict 中提取依赖列表。"""
    if isinstance(comp, ComponentConfig):
        return comp.dependencies
    if isinstance(comp, dict):
        return comp.get("dependencies", [])
    return []


# ---------------------------------------------------------------------------
# 依赖标注解析
# ---------------------------------------------------------------------------

def parse_dep(dep: str) -> tuple[str, str]:
    """解析依赖标注字符串为 (名称, 模式) 元组。

    "a:async" -> ("a", "async")
    "a"       -> ("a", "sync")   # 无标注时默认 sync
    """
    if ":" in dep:
        name, _, mode = dep.partition(":")
        return (name, mode)
    return (dep, "sync")


def format_dep(name: str, mode: str = "sync") -> str:
    """将 (名称, 模式) 格式化为依赖标注字符串。

    ("a", "async") -> "a:async"
    ("a", "sync")  -> "a"        # sync 模式省略标注
    """
    if mode and mode != "sync":
        return f"{name}:{mode}"
    return name


# ---------------------------------------------------------------------------
# 序列化 / 反序列化
# ---------------------------------------------------------------------------

def _component_config_to_dict(comp: ComponentConfig) -> dict:
    """将 ComponentConfig 转为 dict，micro_skill 单独序列化。

    asdict 无法处理 MicroSkill 对象，因此手动构建 dict。
    """
    return {
        "name": comp.name,
        "type": comp.type,
        "replicas": comp.replicas,
        "cpu_per_replica": comp.cpu_per_replica,
        "memory_limit": comp.memory_limit,
        "memory_per_request": comp.memory_per_request,
        "queue_capacity": comp.queue_capacity,
        "timeout_ms": comp.timeout_ms,
        "retry_count": comp.retry_count,
        "has_circuit_breaker": comp.has_circuit_breaker,
        "has_rate_limiter": comp.has_rate_limiter,
        "rate_limit": comp.rate_limit,
        "dependencies": list(comp.dependencies),
        "description": comp.description,
        "micro_skill": micro_skill_to_dict(comp.micro_skill) if comp.micro_skill is not None else None,
    }


def to_dict(spec: DesignSpec) -> dict:
    """将 DesignSpec 转为可 JSON 序列化的 dict。

    components 中的每个 ComponentConfig 转为 dict；
    已经是 dict 的组件保持不变。
    """
    components = []
    for comp in spec.components:
        if isinstance(comp, ComponentConfig):
            components.append(_component_config_to_dict(comp))
        elif isinstance(comp, dict):
            components.append(comp)
        else:
            raise TypeError(f"不支持的组件类型: {type(comp)}")
    return {"domain": spec.domain, "components": components}


def to_json(spec: DesignSpec) -> str:
    """将 DesignSpec 序列化为 JSON 字符串。

    ensure_ascii=False 保留中文等非 ASCII 字符，indent=2 便于阅读。
    """
    return json.dumps(to_dict(spec), ensure_ascii=False, indent=2)


def from_dict(data: dict) -> DesignSpec:
    """从 dict 构建 DesignSpec。

    components 中的每个 dict 转为 ComponentConfig；
    已经是 ComponentConfig 的组件保持不变。
    """
    components = []
    for comp in data.get("components", []):
        if isinstance(comp, ComponentConfig):
            components.append(comp)
        elif isinstance(comp, dict):
            components.append(_ensure_component_config(comp))
        else:
            raise TypeError(f"不支持的组件类型: {type(comp)}")
    return DesignSpec(domain=data.get("domain", ""), components=components)


def from_json(json_str: str) -> DesignSpec:
    """从 JSON 字符串构建 DesignSpec。"""
    return from_dict(json.loads(json_str))


# ---------------------------------------------------------------------------
# 校验函数
# ---------------------------------------------------------------------------

def validate_cycles(spec: DesignSpec) -> tuple[bool, list]:
    """检测组件依赖是否构成循环。

    算法：对每个组件做 DFS，跟踪当前访问路径，
    遇到已在当前路径中的节点则发现循环。

    注意：dependencies 中的名称必须指向 spec 中存在的组件，
    不存在的依赖名跳过（不视为循环）。

    返回:
        (has_cycle, cycle_path)
        - has_cycle 为 True 时 cycle_path 形如 ["A", "B", "A"]
        - has_cycle 为 False 时 cycle_path 为空列表
    """
    # 构建名称 -> 组件 映射
    comp_map = {}
    for comp in spec.components:
        name = _get_component_name(comp)
        if name:
            comp_map[name] = comp

    visited = set()  # 已完成 DFS 且确认无循环的节点

    def dfs(node: str, path: list, path_set: set):
        """从 node 开始 DFS，返回循环路径或 None。

        path: 当前访问路径（按顺序）
        path_set: 当前路径节点集合（O(1) 查询）
        """
        if node in path_set:
            # 发现循环：截取从首次出现到当前的路径，并闭合
            idx = path.index(node)
            return path[idx:] + [node]
        if node in visited:
            return None

        path.append(node)
        path_set.add(node)

        comp = comp_map.get(node)
        if comp:
            for dep in _get_component_dependencies(comp):
                # 解析依赖标注，只取目标节点名
                target, _ = parse_dep(dep)
                # 不存在的依赖名跳过，不视为循环
                if target not in comp_map:
                    continue
                result = dfs(target, path, path_set)
                if result:
                    return result

        path.pop()
        path_set.remove(node)
        visited.add(node)
        return None

    for comp in spec.components:
        name = _get_component_name(comp)
        if not name or name in visited:
            continue
        cycle = dfs(name, [], set())
        if cycle:
            return (True, cycle)

    return (False, [])


def validate_dependencies(spec: DesignSpec, allowed_map: dict = None) -> list[str]:
    """检查组件依赖是否合法。

    - 如果 allowed_map 为 None，跳过类型检查，只检查依赖名是否存在。
    - 如果 allowed_map 提供（格式 {component_type: [allowed_dep_types]}），
      则同时检查依赖组件的类型是否被允许。

    返回错误信息列表，每个错误包含组件名和非法依赖名。
    """
    errors = []

    # 构建名称 -> 类型 映射
    name_to_type = {}
    for comp in spec.components:
        name = _get_component_name(comp)
        if name:
            name_to_type[name] = _get_component_type(comp)

    for comp in spec.components:
        comp_name = _get_component_name(comp)
        comp_type = _get_component_type(comp)
        deps = _get_component_dependencies(comp)

        for dep in deps:
            target, _ = parse_dep(dep)
            # 检查依赖名是否存在
            if target not in name_to_type:
                errors.append(
                    f"组件 '{comp_name}' 依赖不存在的组件 '{target}'"
                )
                continue

            # 检查依赖类型是否被允许（仅当 allowed_map 提供时）
            if allowed_map is not None:
                allowed_types = allowed_map.get(comp_type, [])
                dep_type = name_to_type[target]
                if dep_type not in allowed_types:
                    errors.append(
                        f"组件 '{comp_name}' (类型: {comp_type}) 不允许依赖"
                        f"类型 '{dep_type}' 的组件 '{target}'"
                    )

    return errors


# ---------------------------------------------------------------------------
# 引擎转换辅助函数
# ---------------------------------------------------------------------------

def component_to_engine_dict(comp: ComponentConfig) -> dict:
    """将 ComponentConfig 转为 Simulation Engine 能消费的 dict 格式。

    输出字段与 demo/reckon_sandbox.py 中 Component 的配置字段对应
    （不含运行时状态字段如 healthy_replicas/queue 等）。
    """
    return {
        "name": comp.name,
        "type": comp.type,
        "replicas": comp.replicas,
        "cpu_per_replica": comp.cpu_per_replica,
        "memory_limit": comp.memory_limit,
        "memory_per_request": comp.memory_per_request,
        "queue_capacity": comp.queue_capacity,
        "timeout_ms": comp.timeout_ms,
        "retry_count": comp.retry_count,
        "has_circuit_breaker": comp.has_circuit_breaker,
        "has_rate_limiter": comp.has_rate_limiter,
        "rate_limit": comp.rate_limit,
        "dependencies": list(comp.dependencies),
        "micro_skill": micro_skill_to_dict(comp.micro_skill) if comp.micro_skill is not None else None,
    }


def spec_to_engine_components(spec: DesignSpec) -> dict:
    """将整个 DesignSpec 转为 {name: dict} 格式，供 SandboxEngine 使用。

    dict 组件会先转为 ComponentConfig 再统一转换，保证字段集一致。
    """
    result = {}
    for comp in spec.components:
        comp_config = _ensure_component_config(comp)
        result[comp_config.name] = component_to_engine_dict(comp_config)
    return result
