"""
BlackBox Spec 数据结构测试
========================
覆盖序列化/反序列化往返、DesignVariant 序列化、参数映射应用、边界情况。
"""

from utils.blackbox_spec import (
    DesignVariant,
    BlackBoxSpec,
    to_dict,
    from_dict,
    to_json,
    from_json,
    apply_variant_mappings,
)


# ---------------------------------------------------------------------------
# 序列化 / 反序列化
# ---------------------------------------------------------------------------

def _build_full_spec() -> BlackBoxSpec:
    """构造一个字段齐全的 BlackBoxSpec，供往返测试复用。"""
    return BlackBoxSpec(
        scenario_description="电商订单系统，高并发下单场景",
        functional_points=["创建订单", "查询订单状态", "取消订单"],
        performance_bounds={
            "max_qps": 5000,
            "target_latency_ms": 200,
            "availability_target": 0.999,
        },
        exception_scenarios=[
            {
                "id": "db_down",
                "name": "数据库宕机",
                "description": "主库不可用时的降级行为",
                "trigger_condition": "数据库连接超时 > 3s",
            },
        ],
        design_variants=[
            DesignVariant(
                id="lock_contention",
                name="锁竞争场景",
                description="数据库锁粒度选择",
                options=[
                    {"id": "low", "label": "行级锁", "description": "低竞争"},
                    {"id": "high", "label": "表级锁", "description": "高竞争"},
                ],
                impact_type="parameter_mapping",
                parameter_mappings=[
                    {
                        "option_id": "high",
                        "component_type": "database",
                        "param": "timeout_ms",
                        "multiplier": 2,
                        "offset": 0,
                    },
                ],
            ),
            DesignVariant(
                id="cache_strategy",
                name="缓存策略",
                description="是否启用缓存",
                impact_type="llm_context",
            ),
        ],
    )


def test_serialization_roundtrip():
    """BlackBoxSpec → to_dict → from_dict 后所有字段应一致。"""
    original = _build_full_spec()
    data = to_dict(original)
    restored = from_dict(data)

    assert restored.scenario_description == original.scenario_description
    assert restored.functional_points == original.functional_points
    assert restored.performance_bounds == original.performance_bounds
    assert restored.exception_scenarios == original.exception_scenarios

    # design_variants 应恢复为 DesignVariant 对象且字段一致
    assert len(restored.design_variants) == len(original.design_variants)
    for orig, rest in zip(original.design_variants, restored.design_variants):
        assert isinstance(rest, DesignVariant)
        assert rest.id == orig.id
        assert rest.name == orig.name
        assert rest.description == orig.description
        assert rest.options == orig.options
        assert rest.impact_type == orig.impact_type
        assert rest.parameter_mappings == orig.parameter_mappings


def test_design_variant_serialization():
    """DesignVariant 含 parameter_mappings 时序列化应保留全部字段。"""
    variant = DesignVariant(
        id="lock_contention",
        name="锁竞争场景",
        description="数据库锁粒度选择",
        options=[
            {"id": "high", "label": "表级锁", "description": "高竞争"},
        ],
        impact_type="parameter_mapping",
        parameter_mappings=[
            {
                "option_id": "high",
                "component_type": "database",
                "param": "timeout_ms",
                "multiplier": 2,
                "offset": 0,
            },
        ],
    )
    spec = BlackBoxSpec(
        scenario_description="test",
        design_variants=[variant],
    )
    data = to_dict(spec)

    # design_variants 应被转为 dict 且字段完整
    v_dict = data["design_variants"][0]
    assert isinstance(v_dict, dict)
    assert v_dict["id"] == "lock_contention"
    assert v_dict["name"] == "锁竞争场景"
    assert v_dict["impact_type"] == "parameter_mapping"
    assert v_dict["parameter_mappings"] == [
        {
            "option_id": "high",
            "component_type": "database",
            "param": "timeout_ms",
            "multiplier": 2,
            "offset": 0,
        },
    ]
    assert v_dict["options"] == [
        {"id": "high", "label": "表级锁", "description": "高竞争"},
    ]

    # 反序列化后恢复为 DesignVariant 对象
    restored = from_dict(data)
    rv = restored.design_variants[0]
    assert isinstance(rv, DesignVariant)
    assert rv.impact_type == "parameter_mapping"
    assert rv.parameter_mappings[0]["multiplier"] == 2


# ---------------------------------------------------------------------------
# 参数映射应用
# ---------------------------------------------------------------------------

def test_apply_variant_mappings_multiplier():
    """multiplier=2 时 timeout_ms 应翻倍。"""
    components = [
        {"name": "order_db", "type": "database", "timeout_ms": 1000},
        {"name": "gateway", "type": "gateway", "timeout_ms": 500},
    ]
    variants = [
        DesignVariant(
            id="lock_contention",
            name="锁竞争场景",
            description="数据库锁粒度",
            impact_type="parameter_mapping",
            parameter_mappings=[
                {
                    "option_id": "high",
                    "component_type": "database",
                    "param": "timeout_ms",
                    "multiplier": 2,
                    "offset": 0,
                },
            ],
        ),
    ]
    selected = {"lock_contention": "high"}

    result = apply_variant_mappings(components, variants, selected)

    # database 的 timeout_ms 翻倍，gateway 不受影响
    assert result[0]["timeout_ms"] == 2000
    assert result[1]["timeout_ms"] == 500
    # 原列表不被修改
    assert components[0]["timeout_ms"] == 1000


def test_apply_variant_mappings_offset():
    """offset=100 时 param += 100（multiplier 默认 1）。"""
    components = [
        {"name": "order_db", "type": "database", "timeout_ms": 1000},
    ]
    variants = [
        DesignVariant(
            id="retry_boost",
            name="重试增强",
            description="增加超时时间",
            impact_type="parameter_mapping",
            parameter_mappings=[
                {
                    "option_id": "on",
                    "component_type": "database",
                    "param": "timeout_ms",
                    "multiplier": 1,
                    "offset": 100,
                },
            ],
        ),
    ]
    selected = {"retry_boost": "on"}

    result = apply_variant_mappings(components, variants, selected)

    # 1000 * 1 + 100 = 1100
    assert result[0]["timeout_ms"] == 1100
    # 原列表不被修改
    assert components[0]["timeout_ms"] == 1000


def test_apply_variant_mappings_no_matching_type():
    """component_type 不匹配时组件不应被修改。"""
    components = [
        {"name": "gateway", "type": "gateway", "timeout_ms": 500},
        {"name": "cache", "type": "cache", "timeout_ms": 200},
    ]
    variants = [
        DesignVariant(
            id="db_lock",
            name="数据库锁",
            description="影响 database",
            impact_type="parameter_mapping",
            parameter_mappings=[
                {
                    "option_id": "high",
                    "component_type": "database",
                    "param": "timeout_ms",
                    "multiplier": 3,
                    "offset": 0,
                },
            ],
        ),
    ]
    selected = {"db_lock": "high"}

    result = apply_variant_mappings(components, variants, selected)

    # 没有 database 类型组件，全部保持原值
    assert result[0]["timeout_ms"] == 500
    assert result[1]["timeout_ms"] == 200


def test_apply_variant_mappings_llm_context_skipped():
    """impact_type=llm_context 的 variant 应被跳过，不修改任何参数。"""
    components = [
        {"name": "order_db", "type": "database", "timeout_ms": 1000},
    ]
    variants = [
        DesignVariant(
            id="cache_strategy",
            name="缓存策略",
            description="仅影响 LLM 上下文",
            impact_type="llm_context",
            # 即使配置了 parameter_mappings 也不应应用
            parameter_mappings=[
                {
                    "option_id": "enabled",
                    "component_type": "database",
                    "param": "timeout_ms",
                    "multiplier": 10,
                    "offset": 0,
                },
            ],
        ),
    ]
    selected = {"cache_strategy": "enabled"}

    result = apply_variant_mappings(components, variants, selected)

    # llm_context 类型跳过，参数不变
    assert result[0]["timeout_ms"] == 1000


# ---------------------------------------------------------------------------
# 边界情况
# ---------------------------------------------------------------------------

def test_empty_spec():
    """空 BlackBoxSpec 序列化/反序列化不应报错。"""
    empty = BlackBoxSpec(scenario_description="")
    data = to_dict(empty)
    assert data["scenario_description"] == ""
    assert data["functional_points"] == []
    assert data["performance_bounds"] == {}
    assert data["exception_scenarios"] == []
    assert data["design_variants"] == []

    restored = from_dict(data)
    assert restored.scenario_description == ""
    assert restored.functional_points == []
    assert restored.performance_bounds == {}
    assert restored.exception_scenarios == []
    assert restored.design_variants == []


def test_json_roundtrip():
    """to_json → from_json 后所有字段应一致。"""
    original = _build_full_spec()
    json_str = to_json(original)

    # 验证是合法 JSON 字符串且保留中文（非 ASCII 转义）
    assert isinstance(json_str, str)
    assert "电商订单系统" in json_str

    restored = from_json(json_str)

    assert restored.scenario_description == original.scenario_description
    assert restored.functional_points == original.functional_points
    assert restored.performance_bounds == original.performance_bounds
    assert restored.exception_scenarios == original.exception_scenarios

    assert len(restored.design_variants) == len(original.design_variants)
    for orig, rest in zip(original.design_variants, restored.design_variants):
        assert isinstance(rest, DesignVariant)
        assert rest.id == orig.id
        assert rest.name == orig.name
        assert rest.description == orig.description
        assert rest.options == orig.options
        assert rest.impact_type == orig.impact_type
        assert rest.parameter_mappings == orig.parameter_mappings
