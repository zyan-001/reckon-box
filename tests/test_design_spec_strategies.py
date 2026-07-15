"""
DesignSpec strategies 字段测试
==============================
覆盖 ComponentConfig.strategies 字段的：
  - 默认值
  - to_dict / from_dict 往返
  - to_json / from_json 往返
  - component_to_engine_dict 传递
"""

from utils.design_spec import (
    ComponentConfig,
    DesignSpec,
    to_dict,
    to_json,
    from_dict,
    from_json,
    component_to_engine_dict,
)


def test_component_config_has_strategies_field():
    """ComponentConfig 默认 strategies 应为空列表。"""
    comp = ComponentConfig(name="gateway", type="gateway")
    assert hasattr(comp, "strategies")
    assert comp.strategies == []
    # 确保默认值是独立实例（不是共享引用）
    other = ComponentConfig(name="svc", type="service")
    comp.strategies.append({"trigger": {"metric": "dependency_failed"}})
    assert other.strategies == [], "默认 strategies 不应在实例间共享"


def test_strategies_serialization():
    """to_dict / from_dict 应正确处理 strategies 字段。"""
    strategies = [
        {
            "trigger": {"metric": "dependency_failed"},
            "action": "degrade",
            "target": "fallback_service",
            "description": "依赖失败时降级到 fallback",
        },
        {
            "trigger": {"metric": "consecutive_failures", "threshold": 5},
            "action": "circuit_break",
            "params": {"recovery_ticks": 10},
        },
    ]
    spec = DesignSpec(
        domain="software_systems",
        components=[
            ComponentConfig(
                name="gateway",
                type="gateway",
                strategies=strategies,
            ),
            ComponentConfig(name="svc", type="service"),
        ],
    )
    result = to_dict(spec)
    assert result["components"][0]["strategies"] == strategies
    # 未配置 strategies 的组件应有空列表
    assert result["components"][1]["strategies"] == []

    # 往返：from_dict 后应恢复为相同的 strategies
    restored = from_dict(result)
    assert isinstance(restored.components[0], ComponentConfig)
    assert restored.components[0].strategies == strategies
    assert restored.components[1].strategies == []


def test_strategies_json():
    """to_json / from_json 应正确处理 strategies 字段。"""
    strategies = [
        {
            "trigger": {"metric": "queue_full"},
            "action": "queue_buffer",
            "target": "kafka",
            "description": "队列满时转入消息队列",
        },
    ]
    spec = DesignSpec(
        domain="software_systems",
        components=[
            ComponentConfig(
                name="api",
                type="service",
                strategies=strategies,
            ),
        ],
    )
    json_str = to_json(spec)
    restored = from_json(json_str)
    assert isinstance(restored.components[0], ComponentConfig)
    assert restored.components[0].strategies == strategies


def test_component_to_engine_dict_passes_strategies():
    """component_to_engine_dict 应传递 strategies 字段。"""
    strategies = [
        {
            "trigger": {"metric": "timeout"},
            "action": "degrade",
            "target": "cache",
        }
    ]
    comp = ComponentConfig(
        name="svc",
        type="service",
        strategies=strategies,
    )
    result = component_to_engine_dict(comp)
    assert "strategies" in result
    assert result["strategies"] == strategies
    # 应为副本，不与原对象共享引用
    result["strategies"].append({"another": "rule"})
    assert comp.strategies == strategies, "strategies 应以副本形式传递"
