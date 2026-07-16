"""
ScenarioGenerator 测试
=====================
覆盖正常/洪峰/异常场景生成、inject 推断、variant 映射应用、确定性、默认值。

所有测试基于纯规则引擎，无 LLM 调用，结果确定性。
"""

from utils.blackbox_spec import BlackBoxSpec, DesignVariant
from modules.domain_pack import Scenario, Phase, Inject
from modules.scenario_generator import generate_scenarios


# ---------------------------------------------------------------------------
# 辅助构造
# ---------------------------------------------------------------------------

def _build_spec(
    max_qps=None,
    exception_scenarios=None,
    design_variants=None,
    scenario_description="电商订单系统",
) -> BlackBoxSpec:
    """构造测试用 BlackBoxSpec。max_qps=None 时不写入 performance_bounds。"""
    performance_bounds = {}
    if max_qps is not None:
        performance_bounds["max_qps"] = max_qps
    return BlackBoxSpec(
        scenario_description=scenario_description,
        performance_bounds=performance_bounds,
        exception_scenarios=exception_scenarios or [],
        design_variants=design_variants or [],
    )


# ---------------------------------------------------------------------------
# 正常场景
# ---------------------------------------------------------------------------

def test_generate_normal_scenario():
    """正常场景 phases 正确：ticks=100, rps=max_qps//10。"""
    spec = _build_spec(max_qps=300)
    scenarios = generate_scenarios(spec)

    normal = next(s for s in scenarios if s.id == "dynamic_normal")
    assert isinstance(normal, Scenario)
    assert len(normal.phases) == 1
    assert normal.phases[0].ticks == 100
    assert normal.phases[0].rps == 30  # 300 // 10
    assert normal.inject == []


# ---------------------------------------------------------------------------
# 洪峰场景
# ---------------------------------------------------------------------------

def test_generate_flash_scenario():
    """洪峰场景 4 个 phase：预热/洪峰/回落/恢复。"""
    spec = _build_spec(max_qps=300)
    scenarios = generate_scenarios(spec)

    flash = next(s for s in scenarios if s.id == "dynamic_flash")
    assert len(flash.phases) == 4
    # 预热：ticks=20, rps=max_qps//10
    assert flash.phases[0].ticks == 20
    assert flash.phases[0].rps == 30  # 300 // 10
    # 洪峰：ticks=30, rps=max_qps
    assert flash.phases[1].ticks == 30
    assert flash.phases[1].rps == 300
    # 回落：ticks=30, rps=max_qps*2//3
    assert flash.phases[2].ticks == 30
    assert flash.phases[2].rps == 200  # 300 * 2 // 3
    # 恢复：ticks=20, rps=max_qps//5
    assert flash.phases[3].ticks == 20
    assert flash.phases[3].rps == 60  # 300 // 5
    assert flash.inject == []


# ---------------------------------------------------------------------------
# 异常场景
# ---------------------------------------------------------------------------

def test_generate_exception_scenario():
    """异常场景有 inject，phase rps=max_qps//5。"""
    spec = _build_spec(
        max_qps=300,
        exception_scenarios=[
            {"id": "db_slow", "name": "数据库变慢", "description": "数据库处理变慢"},
        ],
    )
    scenarios = generate_scenarios(spec)

    exc = next(s for s in scenarios if s.id == "dynamic_exception_0")
    assert len(exc.phases) == 1
    assert exc.phases[0].ticks == 100
    assert exc.phases[0].rps == 60  # 300 // 5
    assert len(exc.inject) >= 1


def test_generate_db_slow_inject():
    """描述含'数据库变慢' → degrade_cpu inject (target=database)。"""
    spec = _build_spec(
        max_qps=300,
        exception_scenarios=[
            {"id": "db_slow", "name": "数据库变慢", "description": "数据库查询变慢"},
        ],
    )
    scenarios = generate_scenarios(spec)

    exc = next(s for s in scenarios if s.id == "dynamic_exception_0")
    assert len(exc.inject) == 1
    inj = exc.inject[0]
    assert isinstance(inj, Inject)
    assert inj.action == "degrade_cpu"
    assert inj.target == "database"
    assert inj.start_tick == 10
    assert inj.interval == 5
    assert inj.amount == 2


def test_generate_crash_inject():
    """描述含'崩溃' → crash_all inject，target 推断为第一个 service。"""
    spec = _build_spec(
        max_qps=300,
        exception_scenarios=[
            {"id": "svc_crash", "name": "服务崩溃", "description": "支付服务突然崩溃"},
        ],
    )
    components = [
        {"name": "gateway", "type": "gateway", "timeout_ms": 1000},
        {"name": "payment_service", "type": "service", "timeout_ms": 500},
    ]
    scenarios = generate_scenarios(spec, components=components)

    exc = next(s for s in scenarios if s.id == "dynamic_exception_0")
    assert len(exc.inject) == 1
    inj = exc.inject[0]
    assert isinstance(inj, Inject)
    assert inj.action == "crash_all"
    # target 推断为第一个 service 类型组件
    assert inj.target == "payment_service"
    assert inj.at_tick == 30


def test_generate_memory_inject():
    """描述含'内存/OOM' → degrade_cpu inject (推断 target, amount=5)。"""
    spec = _build_spec(
        max_qps=300,
        exception_scenarios=[
            {"id": "oom", "name": "内存溢出", "description": "服务 OOM 内存不足"},
        ],
    )
    components = [
        {"name": "order_service", "type": "service", "timeout_ms": 500},
    ]
    scenarios = generate_scenarios(spec, components=components)

    exc = next(s for s in scenarios if s.id == "dynamic_exception_0")
    assert len(exc.inject) == 1
    inj = exc.inject[0]
    assert inj.action == "degrade_cpu"
    assert inj.target == "order_service"
    assert inj.start_tick == 20
    assert inj.interval == 10
    assert inj.amount == 5


def test_generate_default_inject():
    """无法推断的异常场景 → degrade_cpu (database, amount=3)。"""
    spec = _build_spec(
        max_qps=300,
        exception_scenarios=[
            {"id": "unknown", "name": "未知异常", "description": "某种未分类的故障"},
        ],
    )
    scenarios = generate_scenarios(spec)

    exc = next(s for s in scenarios if s.id == "dynamic_exception_0")
    assert len(exc.inject) == 1
    inj = exc.inject[0]
    assert inj.action == "degrade_cpu"
    assert inj.target == "database"
    assert inj.start_tick == 20
    assert inj.interval == 10
    assert inj.amount == 3


# ---------------------------------------------------------------------------
# Variant 映射应用
# ---------------------------------------------------------------------------

def test_generate_variant_mapping_applied():
    """传入 components 和 selected_option_ids 后，组件参数被就地修改。"""
    spec = _build_spec(
        max_qps=300,
        design_variants=[
            DesignVariant(
                id="lock_contention",
                name="锁竞争",
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
        ],
    )
    components = [
        {"name": "order_db", "type": "database", "timeout_ms": 1000},
        {"name": "gateway", "type": "gateway", "timeout_ms": 500},
    ]
    selected = {"lock_contention": "high"}

    scenarios = generate_scenarios(
        spec, selected_option_ids=selected, components=components
    )

    # 仍然返回场景列表
    assert isinstance(scenarios, list)
    assert all(isinstance(s, Scenario) for s in scenarios)

    # components 已被就地修改：database.timeout_ms 翻倍，gateway 不变
    assert components[0]["timeout_ms"] == 2000
    assert components[1]["timeout_ms"] == 500


def test_generate_variant_mapping_not_applied_without_selection():
    """未提供 selected_option_ids 时，components 不被修改。"""
    spec = _build_spec(
        max_qps=300,
        design_variants=[
            DesignVariant(
                id="lock_contention",
                name="锁竞争",
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
        ],
    )
    components = [
        {"name": "order_db", "type": "database", "timeout_ms": 1000},
    ]

    generate_scenarios(spec, components=components)

    # 没有 selected_option_ids，不应用映射
    assert components[0]["timeout_ms"] == 1000


# ---------------------------------------------------------------------------
# 确定性
# ---------------------------------------------------------------------------

def test_generate_determinism():
    """相同输入两次运行结果一致。"""
    spec = _build_spec(
        max_qps=300,
        exception_scenarios=[
            {"id": "db_slow", "name": "数据库变慢", "description": "数据库变慢"},
            {"id": "svc_crash", "name": "服务崩溃", "description": "服务崩溃"},
        ],
    )

    first = generate_scenarios(spec)
    second = generate_scenarios(spec)

    assert len(first) == len(second)
    for s1, s2 in zip(first, second):
        assert s1.id == s2.id
        assert s1.name == s2.name
        assert s1.description == s2.description
        assert len(s1.phases) == len(s2.phases)
        for p1, p2 in zip(s1.phases, s2.phases):
            assert p1.ticks == p2.ticks
            assert p1.rps == p2.rps
        assert len(s1.inject) == len(s2.inject)
        for i1, i2 in zip(s1.inject, s2.inject):
            assert i1.target == i2.target
            assert i1.action == i2.action
            assert i1.start_tick == i2.start_tick
            assert i1.interval == i2.interval
            assert i1.at_tick == i2.at_tick
            assert i1.amount == i2.amount


# ---------------------------------------------------------------------------
# 默认 max_qps
# ---------------------------------------------------------------------------

def test_generate_default_qps():
    """max_qps 缺失时使用默认值 30。"""
    spec = _build_spec()  # 不设 max_qps
    scenarios = generate_scenarios(spec)

    normal = next(s for s in scenarios if s.id == "dynamic_normal")
    # 默认 30 // 10 = 3
    assert normal.phases[0].rps == 3

    flash = next(s for s in scenarios if s.id == "dynamic_flash")
    # 洪峰阶段 rps = 默认 30
    assert flash.phases[1].rps == 30
    # 回落阶段 rps = 30 * 2 // 3 = 20
    assert flash.phases[2].rps == 20


def test_generate_default_qps_with_empty_performance_bounds():
    """performance_bounds 为空 dict 时也使用默认值 30。"""
    spec = BlackBoxSpec(scenario_description="test", performance_bounds={})
    scenarios = generate_scenarios(spec)

    normal = next(s for s in scenarios if s.id == "dynamic_normal")
    assert normal.phases[0].rps == 3  # 30 // 10


# ---------------------------------------------------------------------------
# 多异常场景
# ---------------------------------------------------------------------------

def test_generate_multiple_exception_scenarios():
    """多个异常场景生成多个 Scenario，id 按序号递增。"""
    spec = _build_spec(
        max_qps=300,
        exception_scenarios=[
            {"id": "db_slow", "name": "数据库变慢", "description": "数据库变慢"},
            {"id": "svc_crash", "name": "服务崩溃", "description": "服务崩溃"},
            {"id": "oom", "name": "内存溢出", "description": "OOM 内存不足"},
        ],
    )
    scenarios = generate_scenarios(spec)

    # 1 正常 + 1 洪峰 + 3 异常 = 5
    assert len(scenarios) == 5
    exc_scenarios = [s for s in scenarios if s.id.startswith("dynamic_exception_")]
    assert len(exc_scenarios) == 3
    assert exc_scenarios[0].id == "dynamic_exception_0"
    assert exc_scenarios[1].id == "dynamic_exception_1"
    assert exc_scenarios[2].id == "dynamic_exception_2"
    # 每个异常场景都有 inject
    assert all(len(s.inject) >= 1 for s in exc_scenarios)


# ---------------------------------------------------------------------------
# dict 形式 BlackBoxSpec
# ---------------------------------------------------------------------------

def test_generate_scenarios_from_dict_spec():
    """支持以 dict 形式传入 BlackBoxSpec。"""
    spec_dict = {
        "scenario_description": "电商系统",
        "performance_bounds": {"max_qps": 600},
        "exception_scenarios": [
            {"id": "db_slow", "name": "数据库变慢", "description": "数据库变慢"},
        ],
        "design_variants": [],
    }
    scenarios = generate_scenarios(spec_dict)

    normal = next(s for s in scenarios if s.id == "dynamic_normal")
    assert normal.phases[0].rps == 60  # 600 // 10
    exc = next(s for s in scenarios if s.id == "dynamic_exception_0")
    assert exc.inject[0].action == "degrade_cpu"


# ---------------------------------------------------------------------------
# 无异常场景的边界情况
# ---------------------------------------------------------------------------

def test_generate_no_exception_scenarios():
    """没有异常场景时，只生成正常 + 洪峰两个场景。"""
    spec = _build_spec(max_qps=300)
    scenarios = generate_scenarios(spec)

    assert len(scenarios) == 2
    ids = [s.id for s in scenarios]
    assert "dynamic_normal" in ids
    assert "dynamic_flash" in ids
