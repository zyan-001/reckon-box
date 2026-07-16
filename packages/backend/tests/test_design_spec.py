"""
Design Spec 数据结构测试
========================
覆盖序列化/反序列化往返、循环依赖检测、依赖合法性校验、引擎转换。
"""

import json

from utils.design_spec import (
    ComponentConfig,
    DesignSpec,
    to_dict,
    to_json,
    from_dict,
    from_json,
    validate_cycles,
    validate_dependencies,
    component_to_engine_dict,
    spec_to_engine_components,
)
from utils.micro_skill import MicroSkill, StepNode


# ---------------------------------------------------------------------------
# 序列化 / 反序列化
# ---------------------------------------------------------------------------

def test_to_dict_converts_components_to_dicts():
    """to_dict 应将 ComponentConfig 转为 dict。"""
    spec = DesignSpec(
        domain="software_systems",
        components=[
            ComponentConfig(name="gateway", type="gateway", replicas=2, dependencies=["svc"]),
            ComponentConfig(name="svc", type="service"),
        ],
    )
    result = to_dict(spec)
    assert result["domain"] == "software_systems"
    assert len(result["components"]) == 2
    assert result["components"][0]["name"] == "gateway"
    assert result["components"][0]["replicas"] == 2
    assert result["components"][0]["dependencies"] == ["svc"]


def test_to_json_from_json_roundtrip():
    """to_json 后 from_json 应完全恢复原 DesignSpec。"""
    original = DesignSpec(
        domain="software_systems",
        components=[
            ComponentConfig(
                name="api_gateway",
                type="gateway",
                replicas=2,
                cpu_per_replica=100,
                memory_limit=1024,
                memory_per_request=5,
                queue_capacity=500,
                timeout_ms=2000,
                retry_count=0,
                has_circuit_breaker=False,
                has_rate_limiter=True,
                rate_limit=100,
                dependencies=["order_service", "payment_service"],
            ),
            ComponentConfig(
                name="order_service",
                type="service",
                replicas=3,
                timeout_ms=1500,
                retry_count=2,
                has_circuit_breaker=True,
                dependencies=["order_db", "redis_cache"],
            ),
            ComponentConfig(
                name="order_db",
                type="database",
                replicas=1,
            ),
            ComponentConfig(
                name="redis_cache",
                type="cache",
                dependencies=[],
            ),
        ],
    )

    json_str = to_json(original)
    # 验证是合法 JSON 且非 ASCII 友好
    assert isinstance(json_str, str)
    parsed = json.loads(json_str)
    assert parsed["domain"] == "software_systems"

    restored = from_json(json_str)

    # 验证领域
    assert restored.domain == original.domain
    # 验证组件数量
    assert len(restored.components) == len(original.components)
    # 验证每个组件的参数和依赖完全一致
    for orig, rest in zip(original.components, restored.components):
        assert isinstance(rest, ComponentConfig)
        assert rest.name == orig.name
        assert rest.type == orig.type
        assert rest.replicas == orig.replicas
        assert rest.cpu_per_replica == orig.cpu_per_replica
        assert rest.memory_limit == orig.memory_limit
        assert rest.memory_per_request == orig.memory_per_request
        assert rest.queue_capacity == orig.queue_capacity
        assert rest.timeout_ms == orig.timeout_ms
        assert rest.retry_count == orig.retry_count
        assert rest.has_circuit_breaker == orig.has_circuit_breaker
        assert rest.has_rate_limiter == orig.has_rate_limiter
        assert rest.rate_limit == orig.rate_limit
        assert rest.dependencies == orig.dependencies


def test_from_dict_accepts_dict_components():
    """from_dict 应将 dict 组件转为 ComponentConfig。"""
    data = {
        "domain": "software_systems",
        "components": [
            {"name": "gw", "type": "gateway", "replicas": 3},
            {"name": "db", "type": "database"},
        ],
    }
    spec = from_dict(data)
    assert spec.domain == "software_systems"
    assert len(spec.components) == 2
    assert isinstance(spec.components[0], ComponentConfig)
    assert spec.components[0].name == "gw"
    assert spec.components[0].replicas == 3
    # 未提供的字段使用默认值
    assert spec.components[0].cpu_per_replica == 50
    assert spec.components[1].dependencies == []


def test_from_dict_ignores_unknown_fields():
    """from_dict 应忽略 dict 中的未知字段。"""
    data = {
        "domain": "test",
        "components": [
            {"name": "gw", "type": "gateway", "unknown_field": "xxx", "replicas": 2},
        ],
    }
    spec = from_dict(data)
    assert spec.components[0].name == "gw"
    assert spec.components[0].replicas == 2


# ---------------------------------------------------------------------------
# 循环依赖检测
# ---------------------------------------------------------------------------

def test_validate_cycles_no_cycle():
    """无循环依赖应返回 (False, [])。"""
    spec = DesignSpec(
        domain="software_systems",
        components=[
            ComponentConfig(name="gateway", type="gateway", dependencies=["svc"]),
            ComponentConfig(name="svc", type="service", dependencies=["db"]),
            ComponentConfig(name="db", type="database"),
        ],
    )
    has_cycle, cycle_path = validate_cycles(spec)
    assert has_cycle is False
    assert cycle_path == []


def test_validate_cycles_detected():
    """A→B→A 循环应返回 (True, ["A","B","A"])。"""
    spec = DesignSpec(
        domain="software_systems",
        components=[
            ComponentConfig(name="A", type="gateway", dependencies=["B"]),
            ComponentConfig(name="B", type="service", dependencies=["A"]),
        ],
    )
    has_cycle, cycle_path = validate_cycles(spec)
    assert has_cycle is True
    assert cycle_path == ["A", "B", "A"]


def test_validate_cycles_complex():
    """A→B→C→A 三节点循环。"""
    spec = DesignSpec(
        domain="software_systems",
        components=[
            ComponentConfig(name="A", type="gateway", dependencies=["B"]),
            ComponentConfig(name="B", type="service", dependencies=["C"]),
            ComponentConfig(name="C", type="database", dependencies=["A"]),
        ],
    )
    has_cycle, cycle_path = validate_cycles(spec)
    assert has_cycle is True
    assert cycle_path == ["A", "B", "C", "A"]


def test_validate_cycles_self_loop():
    """组件依赖自身也应被检测为循环。"""
    spec = DesignSpec(
        domain="software_systems",
        components=[
            ComponentConfig(name="A", type="service", dependencies=["A"]),
        ],
    )
    has_cycle, cycle_path = validate_cycles(spec)
    assert has_cycle is True
    assert cycle_path == ["A", "A"]


def test_validate_cycles_skips_nonexistent_deps():
    """不存在的依赖名应跳过，不视为循环。"""
    spec = DesignSpec(
        domain="software_systems",
        components=[
            ComponentConfig(name="A", type="gateway", dependencies=["nonexistent"]),
        ],
    )
    has_cycle, cycle_path = validate_cycles(spec)
    assert has_cycle is False
    assert cycle_path == []


# ---------------------------------------------------------------------------
# 依赖合法性校验
# ---------------------------------------------------------------------------

def test_validate_dependencies_valid():
    """合法依赖应返回空错误列表。"""
    spec = DesignSpec(
        domain="software_systems",
        components=[
            ComponentConfig(name="gateway", type="gateway", dependencies=["svc"]),
            ComponentConfig(name="svc", type="service", dependencies=["db"]),
            ComponentConfig(name="db", type="database"),
        ],
    )
    allowed_map = {
        "gateway": ["service"],
        "service": ["database", "cache"],
    }
    errors = validate_dependencies(spec, allowed_map)
    assert errors == []


def test_validate_dependencies_no_allowed_map_only_checks_existence():
    """allowed_map 为 None 时只检查依赖名是否存在。"""
    spec = DesignSpec(
        domain="software_systems",
        components=[
            ComponentConfig(name="gateway", type="gateway", dependencies=["svc"]),
            ComponentConfig(name="svc", type="service", dependencies=["db"]),
            ComponentConfig(name="db", type="database"),
        ],
    )
    errors = validate_dependencies(spec, allowed_map=None)
    assert errors == []


def test_validate_dependencies_invalid_type():
    """gateway 依赖另一个 gateway（allowed_map 不允许）应返回错误。"""
    spec = DesignSpec(
        domain="software_systems",
        components=[
            ComponentConfig(name="gw1", type="gateway", dependencies=["gw2"]),
            ComponentConfig(name="gw2", type="gateway"),
        ],
    )
    allowed_map = {
        "gateway": ["service"],  # gateway 只能依赖 service
    }
    errors = validate_dependencies(spec, allowed_map)
    assert len(errors) == 1
    assert "gw1" in errors[0]
    assert "gw2" in errors[0]


def test_validate_dependencies_nonexistent_dep():
    """依赖不存在的组件应返回错误。"""
    spec = DesignSpec(
        domain="software_systems",
        components=[
            ComponentConfig(name="svc", type="service", dependencies=["ghost"]),
        ],
    )
    errors = validate_dependencies(spec, allowed_map=None)
    assert len(errors) == 1
    assert "svc" in errors[0]
    assert "ghost" in errors[0]


# ---------------------------------------------------------------------------
# 引擎转换
# ---------------------------------------------------------------------------

def test_component_to_engine_dict_fields():
    """component_to_engine_dict 应包含所有配置字段。"""
    comp = ComponentConfig(
        name="api_gateway",
        type="gateway",
        replicas=2,
        cpu_per_replica=100,
        memory_limit=1024,
        memory_per_request=5,
        queue_capacity=500,
        timeout_ms=2000,
        retry_count=1,
        has_circuit_breaker=True,
        has_rate_limiter=True,
        rate_limit=100,
        dependencies=["svc"],
    )
    result = component_to_engine_dict(comp)
    assert result["name"] == "api_gateway"
    assert result["type"] == "gateway"
    assert result["replicas"] == 2
    assert result["cpu_per_replica"] == 100
    assert result["memory_limit"] == 1024
    assert result["memory_per_request"] == 5
    assert result["queue_capacity"] == 500
    assert result["timeout_ms"] == 2000
    assert result["retry_count"] == 1
    assert result["has_circuit_breaker"] is True
    assert result["has_rate_limiter"] is True
    assert result["rate_limit"] == 100
    assert result["dependencies"] == ["svc"]


def test_spec_to_engine_components():
    """spec_to_engine_components 应输出 {name: dict} 且包含所有组件。"""
    spec = DesignSpec(
        domain="software_systems",
        components=[
            ComponentConfig(
                name="gateway",
                type="gateway",
                replicas=2,
                dependencies=["order_service"],
            ),
            ComponentConfig(
                name="order_service",
                type="service",
                replicas=3,
                dependencies=["order_db"],
            ),
            ComponentConfig(
                name="order_db",
                type="database",
            ),
        ],
    )
    result = spec_to_engine_components(spec)

    # 验证输出是 dict 且包含所有组件
    assert isinstance(result, dict)
    assert len(result) == 3
    assert "gateway" in result
    assert "order_service" in result
    assert "order_db" in result

    # 验证每个组件有 replicas/dependencies 等字段
    gateway_dict = result["gateway"]
    assert gateway_dict["replicas"] == 2
    assert gateway_dict["dependencies"] == ["order_service"]
    assert gateway_dict["type"] == "gateway"

    svc_dict = result["order_service"]
    assert svc_dict["replicas"] == 3
    assert svc_dict["dependencies"] == ["order_db"]

    db_dict = result["order_db"]
    assert db_dict["replicas"] == 1
    assert db_dict["dependencies"] == []


def test_spec_to_engine_components_accepts_dict_components():
    """spec_to_engine_components 应能处理 dict 形式的组件。"""
    spec = DesignSpec(
        domain="software_systems",
        components=[
            {"name": "gw", "type": "gateway", "replicas": 2, "dependencies": ["db"]},
            {"name": "db", "type": "database"},
        ],
    )
    result = spec_to_engine_components(spec)
    assert len(result) == 2
    assert result["gw"]["replicas"] == 2
    assert result["gw"]["dependencies"] == ["db"]
    assert result["db"]["type"] == "database"


# ---------------------------------------------------------------------------
# micro_skill 相关测试
# ---------------------------------------------------------------------------

def test_component_config_with_micro_skill():
    """ComponentConfig 带 micro_skill 的创建。"""
    skill = MicroSkill(
        strategy_template_id="sync_direct",
        params={"timeout_ms": 500},
        assertions=["响应时间 < 1000ms"],
    )
    comp = ComponentConfig(name="svc", type="service", micro_skill=skill)
    assert comp.micro_skill is not None
    assert comp.micro_skill.strategy_template_id == "sync_direct"
    assert comp.micro_skill.params == {"timeout_ms": 500}
    assert comp.micro_skill.assertions == ["响应时间 < 1000ms"]


def test_component_config_micro_skill_default_none():
    """micro_skill 默认为 None。"""
    comp = ComponentConfig(name="db", type="database")
    assert comp.micro_skill is None


def test_to_dict_serializes_micro_skill():
    """to_dict 应正确序列化 micro_skill 字段。"""
    skill = MicroSkill(
        strategy_template_id="sync_direct",
        params={"timeout_ms": 500},
        assertions=["响应时间 < 1000ms"],
        step_tree=StepNode(id="s1", label="步骤1", description="描述1"),
    )
    spec = DesignSpec(
        domain="software_systems",
        components=[
            ComponentConfig(name="svc", type="service", micro_skill=skill),
            ComponentConfig(name="db", type="database"),  # micro_skill=None
        ],
    )
    result = to_dict(spec)

    # 有 micro_skill 的组件
    svc_dict = result["components"][0]
    assert svc_dict["micro_skill"] is not None
    assert svc_dict["micro_skill"]["strategy_template_id"] == "sync_direct"
    assert svc_dict["micro_skill"]["params"] == {"timeout_ms": 500}
    assert svc_dict["micro_skill"]["step_tree"]["id"] == "s1"

    # micro_skill=None 的组件
    db_dict = result["components"][1]
    assert db_dict["micro_skill"] is None


def test_from_dict_parses_micro_skill():
    """from_dict 应将 micro_skill 的 dict 反序列化为 MicroSkill 对象。"""
    data = {
        "domain": "software_systems",
        "components": [
            {
                "name": "svc",
                "type": "service",
                "micro_skill": {
                    "strategy_template_id": "sync_direct",
                    "params": {"timeout_ms": 500},
                    "assertions": ["响应时间 < 1000ms"],
                    "step_tree": None,
                },
            },
        ],
    }
    spec = from_dict(data)
    comp = spec.components[0]
    assert isinstance(comp, ComponentConfig)
    assert isinstance(comp.micro_skill, MicroSkill)
    assert comp.micro_skill.strategy_template_id == "sync_direct"
    assert comp.micro_skill.params == {"timeout_ms": 500}
    assert comp.micro_skill.step_tree is None


def test_from_dict_ignores_old_strategies_field():
    """旧 JSON 含 strategies 字段时应被忽略，micro_skill 默认 None。"""
    data = {
        "domain": "software_systems",
        "components": [
            {
                "name": "svc",
                "type": "service",
                "strategies": ["retry", "circuit_breaker"],
                "dependencies": ["db"],
            },
        ],
    }
    spec = from_dict(data)
    comp = spec.components[0]
    assert isinstance(comp, ComponentConfig)
    assert comp.name == "svc"
    # strategies 字段被忽略，micro_skill 默认 None
    assert comp.micro_skill is None
    # 其他字段正常
    assert comp.dependencies == ["db"]


def test_micro_skill_roundtrip():
    """micro_skill 的完整序列化/反序列化往返测试。"""
    skill = MicroSkill(
        strategy_template_id="sync_direct",
        params={"timeout_ms": 800},
        assertions=["断言1"],
        step_tree=StepNode(
            id="root",
            label="根步骤",
            description="根节点描述",
            children=[
                StepNode(id="c1", label="子步骤", description="子节点描述"),
            ],
        ),
    )
    original = DesignSpec(
        domain="software_systems",
        components=[
            ComponentConfig(name="svc", type="service", micro_skill=skill),
        ],
    )
    json_str = to_json(original)
    restored = from_json(json_str)

    comp = restored.components[0]
    assert isinstance(comp.micro_skill, MicroSkill)
    assert comp.micro_skill.strategy_template_id == "sync_direct"
    assert comp.micro_skill.params == {"timeout_ms": 800}
    assert comp.micro_skill.assertions == ["断言1"]
    assert comp.micro_skill.step_tree is not None
    assert comp.micro_skill.step_tree.id == "root"
    assert len(comp.micro_skill.step_tree.children) == 1
    assert comp.micro_skill.step_tree.children[0].id == "c1"


def test_component_to_engine_dict_with_micro_skill():
    """component_to_engine_dict 应包含 micro_skill 序列化结果。"""
    skill = MicroSkill(
        strategy_template_id="sync_direct",
        params={"timeout_ms": 500},
        assertions=["断言"],
    )
    comp = ComponentConfig(name="svc", type="service", micro_skill=skill)
    result = component_to_engine_dict(comp)
    assert result["micro_skill"] is not None
    assert result["micro_skill"]["strategy_template_id"] == "sync_direct"
    # 不再包含 strategies 字段
    assert "strategies" not in result


def test_component_to_engine_dict_micro_skill_none():
    """component_to_engine_dict 中 micro_skill=None 时输出 None。"""
    comp = ComponentConfig(name="db", type="database")
    result = component_to_engine_dict(comp)
    assert result["micro_skill"] is None
    assert "strategies" not in result
