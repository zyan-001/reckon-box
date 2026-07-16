"""Domain Pack 加载器测试"""

import re
from pathlib import Path

import pytest
import yaml

from modules.domain_pack import (
    ComponentTypeSpec,
    DomainPack,
    ParamSpec,
    Phase,
    Scenario,
    load_domain_pack,
    validate,
)

YAML_PATH = str(
    Path(__file__).resolve().parent.parent / "src" / "domain_packs" / "software_systems.yaml"
)


# ---------------------------------------------------------------------------
# test_load_full_pack
# ---------------------------------------------------------------------------

def test_load_full_pack():
    """加载完整领域包，验证组件类型数、场景数、失败模式数、概念数。"""
    pack = load_domain_pack(YAML_PATH)

    assert isinstance(pack, DomainPack)

    # 6 种组件类型
    component_types = pack.get_component_types()
    assert len(component_types) == 6
    for name in ("gateway", "service", "database", "cache", "message_queue", "scheduled_task"):
        assert name in component_types
        assert isinstance(component_types[name], ComponentTypeSpec)

    # 4 个场景
    assert len(pack.get_scenarios()) == 4

    # 6 种失败模式
    assert len(pack.get_failure_taxonomy()) == 6

    # 10 个概念
    assert len(pack.get_concepts()) == 10

    # 验证 gateway 参数空间完整性
    gw = component_types["gateway"]
    expected_params = {
        "replicas", "cpu_per_replica", "memory_limit", "queue_capacity",
        "timeout_ms", "retry_count", "has_circuit_breaker", "has_rate_limiter",
        "rate_limit", "memory_per_request",
    }
    assert set(gw.params.keys()) == expected_params
    assert isinstance(gw.params["replicas"], ParamSpec)
    assert gw.params["replicas"].type == "int"
    assert gw.params["replicas"].min == 1
    assert gw.params["replicas"].max == 10
    assert gw.params["replicas"].default == 2
    assert gw.params["has_circuit_breaker"].type == "bool"
    assert gw.params["has_circuit_breaker"].default is False
    assert gw.params["memory_limit"].unit == "MB"

    # 验证 can_depend_on
    assert gw.can_depend_on == ["service"]
    assert component_types["database"].can_depend_on == []
    assert component_types["service"].can_depend_on == ["service", "database", "cache"]

    # 验证 dependency_rules
    assert pack.dependency_rules["forbid_cycles"] is True
    assert pack.dependency_rules["max_depth"] == 5


# ---------------------------------------------------------------------------
# test_load_scenario_phases
# ---------------------------------------------------------------------------

def test_load_scenario_phases():
    """验证 flash_sale 场景有 4 个 phase，db_slow 有 1 个 inject。"""
    pack = load_domain_pack(YAML_PATH)

    # flash_sale: 4 个 phase
    flash = pack.get_scenario("flash_sale")
    assert flash is not None
    assert isinstance(flash, Scenario)
    assert len(flash.phases) == 4
    assert isinstance(flash.phases[0], Phase)
    assert flash.phases[0].ticks == 20
    assert flash.phases[0].rps == 30
    assert flash.phases[1].ticks == 30
    assert flash.phases[1].rps == 300
    assert flash.phases[2].rps == 200
    assert flash.phases[3].rps == 50
    # flash_sale 无 inject
    assert flash.inject == []

    # db_slow: 1 个 inject
    db_slow = pack.get_scenario("db_slow")
    assert db_slow is not None
    assert len(db_slow.phases) == 1
    assert db_slow.phases[0].ticks == 100
    assert db_slow.phases[0].rps == 40
    assert len(db_slow.inject) == 1
    inj = db_slow.inject[0]
    assert inj.target == "database"
    assert inj.action == "degrade_cpu"
    assert inj.start_tick == 10
    assert inj.interval == 5
    assert inj.amount == 2
    assert inj.at_tick is None

    # partial_failure: 1 个 inject (at_tick 形式)
    pf = pack.get_scenario("partial_failure")
    assert pf is not None
    assert len(pf.inject) == 1
    assert pf.inject[0].target == "payment_service"
    assert pf.inject[0].action == "crash_all"
    assert pf.inject[0].at_tick == 30
    assert pf.inject[0].start_tick is None


# ---------------------------------------------------------------------------
# test_validation_missing_field
# ---------------------------------------------------------------------------

def test_validation_missing_field():
    """构造缺字段的 dict，验证 validate 返回的错误信息包含字段名。"""
    bad_data = {
        "meta": {"name": "test"},  # 缺 description
        "component_types": {
            "gateway": {
                "display_name": "网关",
                "params": {
                    # int 类型但缺 min
                    "replicas": {"type": "int", "max": 10, "default": 2},
                    # 缺 default
                    "timeout_ms": {"type": "int", "min": 100, "max": 10000},
                },
                "can_depend_on": [],
            }
        },
        "scenarios": [
            # phase 缺 rps
            {"id": "normal", "name": "正常", "phases": [{"ticks": 100}]},
            # 缺 name
            {"id": "db_slow", "phases": [{"ticks": 100, "rps": 40}]},
        ],
    }
    errors = validate(bad_data)

    # 至少 4 条错误
    assert len(errors) >= 4
    assert any("description" in e for e in errors)
    assert any("replicas" in e and "min" in e for e in errors)
    assert any("timeout_ms" in e and "default" in e for e in errors)
    assert any("rps" in e for e in errors)
    assert any("name" in e and "缺少" in e for e in errors)


# ---------------------------------------------------------------------------
# test_validation_missing_field_with_line
# ---------------------------------------------------------------------------

def test_validation_missing_field_with_line(tmp_path):
    """验证错误信息包含 YAML 行号。"""
    yaml_content = """\
meta:
  name: "test"
  description: "test"
component_types:
  gateway:
    display_name: "API网关"
    params:
      replicas:
        type: int
        max: 10
        default: 2
    can_depend_on: []
scenarios:
  - id: normal
    name: "正常"
    phases:
      - { ticks: 100, rps: 30 }
"""
    p = tmp_path / "bad_pack.yaml"
    p.write_text(yaml_content, encoding="utf-8")

    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    errors = validate(data, source_path=str(p))

    # replicas 是 int 类型但缺 min
    replicas_errors = [e for e in errors if "replicas" in e and "min" in e]
    assert len(replicas_errors) == 1
    # 错误信息包含行号 "行 N"
    assert re.search(r"行 \d+", replicas_errors[0]), f"错误信息缺少行号: {replicas_errors[0]}"
    # replicas 定义在第 8 行
    assert "行 8" in replicas_errors[0]


# ---------------------------------------------------------------------------
# test_get_scenario
# ---------------------------------------------------------------------------

def test_get_scenario():
    """验证 get_scenario 返回正确对象，不存在时返回 None。"""
    pack = load_domain_pack(YAML_PATH)

    flash = pack.get_scenario("flash_sale")
    assert flash is not None
    assert flash.id == "flash_sale"
    assert flash.name == "秒杀洪峰"
    assert flash.description == "流量暴涨10倍后回落"

    normal = pack.get_scenario("normal")
    assert normal is not None
    assert normal.id == "normal"

    none_result = pack.get_scenario("nonexistent")
    assert none_result is None
