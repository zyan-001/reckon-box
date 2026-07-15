"""Domain Pack 加载器

读取 YAML 领域包文件，解析为 DomainPack 对象，并校验 schema。
领域包定义一个学科领域的模拟规则：组件类型及参数空间、依赖规则、
压力场景模板、失败分类法。纯数据，不含执行逻辑。
"""

from dataclasses import dataclass, field
from typing import Optional

import yaml


# ---------------------------------------------------------------------------
# 数据结构定义
# ---------------------------------------------------------------------------

@dataclass
class ParamSpec:
    type: str
    default: object
    min: Optional[int] = None
    max: Optional[int] = None
    unit: Optional[str] = None


@dataclass
class ComponentTypeSpec:
    display_name: str
    params: dict
    can_depend_on: list = field(default_factory=list)


@dataclass
class Phase:
    ticks: int
    rps: int


@dataclass
class Inject:
    target: str
    action: str
    start_tick: Optional[int] = None
    interval: Optional[int] = None
    at_tick: Optional[int] = None
    amount: Optional[int] = None


@dataclass
class Scenario:
    id: str
    name: str
    description: str
    phases: list
    inject: list = field(default_factory=list)


@dataclass
class DomainPack:
    meta: dict
    component_types: dict
    dependency_rules: dict
    scenarios: list
    failure_taxonomy: list
    concepts: list
    teaching_cards: dict = field(default_factory=dict)

    # ---- 访问器方法 ----

    def get_component_types(self) -> dict:
        return self.component_types

    def get_scenario(self, scenario_id: str) -> Optional[Scenario]:
        for s in self.scenarios:
            if s.id == scenario_id:
                return s
        return None

    def get_scenarios(self) -> list:
        return self.scenarios

    def get_failure_taxonomy(self) -> list:
        return self.failure_taxonomy

    def get_concepts(self) -> list:
        return self.concepts

    def get_teaching_cards(self) -> dict:
        return self.teaching_cards


# ---------------------------------------------------------------------------
# 行号收集（用于校验错误信息中包含 YAML 行号）
# ---------------------------------------------------------------------------

def _collect_line_marks(node, path: str, marks: dict) -> None:
    """递归遍历 YAML 节点树，收集 path -> 行号(1-indexed) 映射。"""
    if isinstance(node, yaml.MappingNode):
        for key_node, value_node in node.value:
            key = key_node.value
            child_path = f"{path}.{key}" if path else key
            marks[child_path] = key_node.start_mark.line + 1
            _collect_line_marks(value_node, child_path, marks)
    elif isinstance(node, yaml.SequenceNode):
        for i, item in enumerate(node.value):
            child_path = f"{path}[{i}]"
            marks[child_path] = item.start_mark.line + 1
            _collect_line_marks(item, child_path, marks)


# ---------------------------------------------------------------------------
# Schema 校验
# ---------------------------------------------------------------------------

def validate(pack_data: dict, source_path: str = None) -> list:
    """校验领域包 schema，返回错误信息列表。

    每条错误信息包含字段路径和（当 source_path 提供时）YAML 行号。
    """
    errors = []
    marks = {}

    if source_path:
        try:
            with open(source_path, "r", encoding="utf-8") as f:
                root_node = yaml.compose(f)
            if root_node is not None:
                _collect_line_marks(root_node, "", marks)
        except (OSError, yaml.YAMLError):
            pass

    def line(path: str) -> str:
        ln = marks.get(path)
        return f" (行 {ln})" if ln else ""

    if not isinstance(pack_data, dict):
        errors.append(f"root: 顶层结构必须是映射{line('')}")
        return errors

    # ---- meta 校验 ----
    meta = pack_data.get("meta")
    if not isinstance(meta, dict):
        errors.append(f"meta: 缺少必填字段 'meta'{line('meta')}")
    else:
        if "name" not in meta:
            errors.append(f"meta.name: 缺少必填字段 'name'{line('meta')}")
        if "description" not in meta:
            errors.append(f"meta.description: 缺少必填字段 'description'{line('meta')}")

    # ---- component_types 校验 ----
    component_types = pack_data.get("component_types")
    if not isinstance(component_types, dict) or len(component_types) == 0:
        errors.append(f"component_types: 至少需要1个组件类型{line('component_types')}")
    else:
        for ct_name, ct_spec in component_types.items():
            ct_path = f"component_types.{ct_name}"
            if not isinstance(ct_spec, dict):
                errors.append(f"{ct_path}: 组件类型定义必须是映射{line(ct_path)}")
                continue
            params = ct_spec.get("params")
            if not isinstance(params, dict) or len(params) == 0:
                errors.append(f"{ct_path}.params: 缺少必填字段 'params'{line(ct_path)}")
                continue
            for param_name, param_spec in params.items():
                p_path = f"{ct_path}.params.{param_name}"
                if not isinstance(param_spec, dict):
                    errors.append(f"{p_path}: 参数定义必须是映射{line(p_path)}")
                    continue
                if "type" not in param_spec:
                    errors.append(f"{p_path}: 缺少必填字段 'type'{line(p_path)}")
                if "default" not in param_spec:
                    errors.append(f"{p_path}: 缺少必填字段 'default'{line(p_path)}")
                if param_spec.get("type") == "int":
                    if "min" not in param_spec:
                        errors.append(f"{p_path}: 缺少必填字段 'min'{line(p_path)}")
                    if "max" not in param_spec:
                        errors.append(f"{p_path}: 缺少必填字段 'max'{line(p_path)}")

    # ---- scenarios 校验 ----
    scenarios = pack_data.get("scenarios")
    if not isinstance(scenarios, list) or len(scenarios) == 0:
        errors.append(f"scenarios: 至少需要1个场景{line('scenarios')}")
    else:
        for i, scenario in enumerate(scenarios):
            s_path = f"scenarios[{i}]"
            if not isinstance(scenario, dict):
                errors.append(f"{s_path}: 场景定义必须是映射{line(s_path)}")
                continue
            if "id" not in scenario:
                errors.append(f"{s_path}: 缺少必填字段 'id'{line(s_path)}")
            if "name" not in scenario:
                errors.append(f"{s_path}: 缺少必填字段 'name'{line(s_path)}")
            phases = scenario.get("phases")
            if not isinstance(phases, list) or len(phases) == 0:
                errors.append(f"{s_path}.phases: 缺少必填字段 'phases'{line(s_path)}")
            else:
                for j, phase in enumerate(phases):
                    ph_path = f"{s_path}.phases[{j}]"
                    if not isinstance(phase, dict):
                        errors.append(f"{ph_path}: phase 定义必须是映射{line(ph_path)}")
                        continue
                    if "ticks" not in phase:
                        errors.append(f"{ph_path}: 缺少必填字段 'ticks'{line(ph_path)}")
                    if "rps" not in phase:
                        errors.append(f"{ph_path}: 缺少必填字段 'rps'{line(ph_path)}")

    return errors


# ---------------------------------------------------------------------------
# 解析辅助函数
# ---------------------------------------------------------------------------

def _parse_param_spec(data: dict) -> ParamSpec:
    return ParamSpec(
        type=data["type"],
        default=data["default"],
        min=data.get("min"),
        max=data.get("max"),
        unit=data.get("unit"),
    )


def _parse_component_type(data: dict) -> ComponentTypeSpec:
    params = {
        name: _parse_param_spec(spec)
        for name, spec in data.get("params", {}).items()
    }
    return ComponentTypeSpec(
        display_name=data.get("display_name", ""),
        params=params,
        can_depend_on=data.get("can_depend_on", []),
    )


def _parse_phase(data: dict) -> Phase:
    return Phase(ticks=data["ticks"], rps=data["rps"])


def _parse_inject(data: dict) -> Inject:
    return Inject(
        target=data["target"],
        action=data["action"],
        start_tick=data.get("start_tick"),
        interval=data.get("interval"),
        at_tick=data.get("at_tick"),
        amount=data.get("amount"),
    )


def _parse_scenario(data: dict) -> Scenario:
    return Scenario(
        id=data["id"],
        name=data["name"],
        description=data.get("description", ""),
        phases=[_parse_phase(p) for p in data.get("phases", [])],
        inject=[_parse_inject(i) for i in data.get("inject", [])],
    )


# ---------------------------------------------------------------------------
# 加载入口
# ---------------------------------------------------------------------------

def load_domain_pack(path: str) -> DomainPack:
    """读取 YAML 文件，校验 schema，解析为 DomainPack 对象。

    若校验失败，抛出 ValueError，消息包含所有校验错误。
    """
    with open(path, "r", encoding="utf-8") as f:
        pack_data = yaml.safe_load(f)

    errors = validate(pack_data, source_path=path)
    if errors:
        raise ValueError("; ".join(errors))

    return DomainPack(
        meta=pack_data.get("meta", {}),
        component_types={
            name: _parse_component_type(spec)
            for name, spec in pack_data.get("component_types", {}).items()
        },
        dependency_rules=pack_data.get("dependency_rules", {}),
        scenarios=[_parse_scenario(s) for s in pack_data.get("scenarios", [])],
        failure_taxonomy=pack_data.get("failure_taxonomy", []),
        concepts=pack_data.get("concepts", []),
        teaching_cards=pack_data.get("teaching_cards", {}) or {},
    )
