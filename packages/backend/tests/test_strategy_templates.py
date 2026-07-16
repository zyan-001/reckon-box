"""
策略模板注册表测试
==================
TDD 测试覆盖 strategy_templates 模块的所有接口和边界条件。
"""

import pytest

from utils.strategy_templates import (
    StrategyTemplate,
    get_all_templates,
    get_template,
    validate_params,
    default_params,
)


# ---------------------------------------------------------------------------
# get_all_templates / get_template 基础查询
# ---------------------------------------------------------------------------

class TestTemplateQuery:
    """模板查询相关测试。"""

    def test_get_all_templates_returns_6(self):
        """get_all_templates 应返回 6 个模板。"""
        templates = get_all_templates()
        assert len(templates) == 6

    @pytest.mark.parametrize("template_id", [
        "sync_direct",
        "retry_no_backoff",
        "retry_with_backoff",
        "async_fire_and_forget",
        "degrade_fallback",
        "circuit_break",
    ])
    def test_get_template_for_each_id(self, template_id):
        """每个内置模板 ID 都能正确返回。"""
        template = get_template(template_id)
        assert template is not None
        assert template.id == template_id

    def test_get_template_nonexistent_returns_none(self):
        """不存在的模板 ID 应返回 None。"""
        assert get_template("nonexistent") is None

    def test_get_all_templates_returns_dict(self):
        """get_all_templates 返回字典，键为模板 ID。"""
        templates = get_all_templates()
        expected_ids = {
            "sync_direct", "retry_no_backoff", "retry_with_backoff",
            "async_fire_and_forget", "degrade_fallback", "circuit_break",
        }
        assert set(templates.keys()) == expected_ids

    def test_get_all_templates_is_copy(self):
        """get_all_templates 返回浅拷贝，修改不影响注册表。"""
        templates = get_all_templates()
        templates.clear()
        # 注册表不受影响
        assert len(get_all_templates()) == 6


# ---------------------------------------------------------------------------
# validate_params 参数校验
# ---------------------------------------------------------------------------

class TestValidateParams:
    """参数校验相关测试。"""

    def test_valid_params_sync_direct(self):
        """sync_direct 合法参数应通过校验。"""
        errors = validate_params("sync_direct", {
            "timeout_ms": 1000,
            "capacity_per_replica": 50,
        })
        assert errors == []

    def test_valid_params_retry_no_backoff(self):
        """retry_no_backoff 合法参数应通过校验。"""
        errors = validate_params("retry_no_backoff", {
            "max_retries": 3,
            "capacity_per_replica": 50,
        })
        assert errors == []

    def test_valid_params_retry_with_backoff(self):
        """retry_with_backoff 合法参数应通过校验。"""
        errors = validate_params("retry_with_backoff", {
            "max_retries": 3,
            "base_delay_ms": 100,
            "capacity_per_replica": 50,
        })
        assert errors == []

    def test_valid_params_async_fire_and_forget(self):
        """async_fire_and_forget 合法参数应通过校验。"""
        errors = validate_params("async_fire_and_forget", {
            "consumer_lag": 5,
            "capacity_per_replica": 50,
        })
        assert errors == []

    def test_valid_params_degrade_fallback(self):
        """degrade_fallback 合法参数应通过校验。"""
        errors = validate_params("degrade_fallback", {
            "fallback_target": "backup_db",
            "capacity_per_replica": 50,
        })
        assert errors == []

    def test_valid_params_circuit_break(self):
        """circuit_break 合法参数应通过校验。"""
        errors = validate_params("circuit_break", {
            "failure_threshold": 5,
            "recovery_ticks": 10,
            "capacity_per_replica": 50,
        })
        assert errors == []

    def test_negative_max_retries_returns_error(self):
        """max_retries=-1 应返回错误（负数不允许）。"""
        errors = validate_params("retry_no_backoff", {
            "max_retries": -1,
            "capacity_per_replica": 50,
        })
        assert len(errors) > 0
        assert any("max_retries" in e for e in errors)

    def test_missing_required_param_returns_error(self):
        """缺少必需参数应返回错误。"""
        errors = validate_params("sync_direct", {"timeout_ms": 1000})
        assert len(errors) > 0
        assert any("capacity_per_replica" in e for e in errors)

    def test_wrong_type_returns_error(self):
        """参数类型错误应返回错误。"""
        errors = validate_params("sync_direct", {
            "timeout_ms": "not_a_number",
            "capacity_per_replica": 50,
        })
        assert len(errors) > 0
        assert any("timeout_ms" in e for e in errors)

    def test_unknown_template_returns_error(self):
        """不存在的模板 ID 应返回错误。"""
        errors = validate_params("nonexistent", {})
        assert len(errors) > 0
        assert any("nonexistent" in e for e in errors)

    def test_unknown_param_returns_error(self):
        """多余参数应返回错误。"""
        errors = validate_params("sync_direct", {
            "timeout_ms": 1000,
            "capacity_per_replica": 50,
            "extra_param": 999,
        })
        assert any("extra_param" in e for e in errors)

    def test_bool_as_int_returns_error(self):
        """bool 不应被接受为 int 类型。"""
        errors = validate_params("sync_direct", {
            "timeout_ms": True,
            "capacity_per_replica": 50,
        })
        assert any("timeout_ms" in e for e in errors)

    def test_negative_capacity_per_replica_returns_error(self):
        """capacity_per_replica 为负数应返回错误。"""
        errors = validate_params("sync_direct", {
            "timeout_ms": 1000,
            "capacity_per_replica": -1,
        })
        assert any("capacity_per_replica" in e for e in errors)


# ---------------------------------------------------------------------------
# calculate_capacity 容量计算
# ---------------------------------------------------------------------------

class TestCalculateCapacity:
    """容量计算相关测试。"""

    def test_sync_direct_capacity(self):
        """sync_direct 容量 = replicas * capacity_per_replica。"""
        template = get_template("sync_direct")
        capacity = template.calculate_capacity(
            {"timeout_ms": 1000, "capacity_per_replica": 50}, 3
        )
        assert capacity == 150

    def test_retry_no_backoff_capacity(self):
        """retry_no_backoff 容量 = replicas * capacity_per_replica。"""
        template = get_template("retry_no_backoff")
        capacity = template.calculate_capacity(
            {"max_retries": 3, "capacity_per_replica": 40}, 2
        )
        assert capacity == 80

    def test_retry_with_backoff_capacity(self):
        """retry_with_backoff 容量 = replicas * capacity_per_replica。"""
        template = get_template("retry_with_backoff")
        capacity = template.calculate_capacity(
            {"max_retries": 3, "base_delay_ms": 100, "capacity_per_replica": 30}, 4
        )
        assert capacity == 120

    def test_async_fire_and_forget_capacity(self):
        """async_fire_and_forget 容量 = replicas * capacity_per_replica。"""
        template = get_template("async_fire_and_forget")
        capacity = template.calculate_capacity(
            {"consumer_lag": 5, "capacity_per_replica": 60}, 3
        )
        assert capacity == 180

    def test_degrade_fallback_capacity(self):
        """degrade_fallback 容量 = replicas * capacity_per_replica。"""
        template = get_template("degrade_fallback")
        capacity = template.calculate_capacity(
            {"fallback_target": "backup_db", "capacity_per_replica": 25}, 5
        )
        assert capacity == 125

    def test_circuit_break_capacity(self):
        """circuit_break 容量 = replicas * capacity_per_replica。"""
        template = get_template("circuit_break")
        capacity = template.calculate_capacity(
            {"failure_threshold": 5, "recovery_ticks": 10, "capacity_per_replica": 45}, 2
        )
        assert capacity == 90


# ---------------------------------------------------------------------------
# effective_load_multiplier 负载放大系数
# ---------------------------------------------------------------------------

class TestEffectiveLoadMultiplier:
    """负载放大系数相关测试。"""

    def test_retry_no_backoff_multiplier(self):
        """retry_no_backoff 的 effective_load_multiplier = max_retries + 1。"""
        template = get_template("retry_no_backoff")
        # max_retries=3 → 放大系数=4
        multiplier = template.effective_load_multiplier({"max_retries": 3, "capacity_per_replica": 50})
        assert multiplier == 4

    def test_retry_no_backoff_multiplier_zero_retries(self):
        """max_retries=0 时放大系数=1。"""
        template = get_template("retry_no_backoff")
        multiplier = template.effective_load_multiplier({"max_retries": 0, "capacity_per_replica": 50})
        assert multiplier == 1

    def test_sync_direct_has_no_multiplier(self):
        """sync_direct 没有 effective_load_multiplier。"""
        template = get_template("sync_direct")
        assert template.effective_load_multiplier is None


# ---------------------------------------------------------------------------
# assertions 断言
# ---------------------------------------------------------------------------

class TestAssertions:
    """模板断言相关测试。"""

    def test_sync_direct_assertions(self):
        """sync_direct 应包含 fail_immediately 断言。"""
        template = get_template("sync_direct")
        assert len(template.assertions) == 1
        assert template.assertions[0]["action"] == "fail_immediately"

    def test_circuit_break_circuit_open_effective_capacity(self):
        """circuit_break 的 circuit_open 断言包含 effective_capacity=0。"""
        template = get_template("circuit_break")
        circuit_open_assertion = None
        for assertion in template.assertions:
            if assertion.get("condition") == "circuit_open":
                circuit_open_assertion = assertion
                break
        assert circuit_open_assertion is not None
        assert circuit_open_assertion["effective_capacity"] == 0

    def test_retry_no_backoff_has_retry_storm(self):
        """retry_no_backoff 应包含 retry_storm 崩溃风险。"""
        template = get_template("retry_no_backoff")
        crash_risks = [
            a for a in template.assertions if "crash_risk" in a
        ]
        assert len(crash_risks) == 1
        assert crash_risks[0]["crash_risk"] == "retry_storm"

    def test_degrade_fallback_has_cascading_failure(self):
        """degrade_fallback 应包含 cascading_failure 崩溃风险。"""
        template = get_template("degrade_fallback")
        crash_risks = [
            a for a in template.assertions if "crash_risk" in a
        ]
        assert len(crash_risks) == 1
        assert crash_risks[0]["crash_risk"] == "cascading_failure"


# ---------------------------------------------------------------------------
# default_params 默认参数
# ---------------------------------------------------------------------------

class TestDefaultParams:
    """默认参数相关测试。"""

    def test_default_params_sync_direct(self):
        """sync_direct 的默认参数应包含最低配置。"""
        defaults = default_params("sync_direct")
        assert defaults == {"timeout_ms": 0, "capacity_per_replica": 1}

    def test_default_params_retry_no_backoff(self):
        """retry_no_backoff 的默认参数。"""
        defaults = default_params("retry_no_backoff")
        assert defaults == {"max_retries": 0, "capacity_per_replica": 1}

    def test_default_params_retry_with_backoff(self):
        """retry_with_backoff 的默认参数。"""
        defaults = default_params("retry_with_backoff")
        assert defaults == {"max_retries": 0, "base_delay_ms": 0, "capacity_per_replica": 1}

    def test_default_params_async_fire_and_forget(self):
        """async_fire_and_forget 的默认参数。"""
        defaults = default_params("async_fire_and_forget")
        assert defaults == {"consumer_lag": 0, "capacity_per_replica": 1}

    def test_default_params_degrade_fallback(self):
        """degrade_fallback 的默认参数。"""
        defaults = default_params("degrade_fallback")
        assert defaults == {"fallback_target": "", "capacity_per_replica": 1}

    def test_default_params_circuit_break(self):
        """circuit_break 的默认参数。"""
        defaults = default_params("circuit_break")
        assert defaults == {"failure_threshold": 0, "recovery_ticks": 0, "capacity_per_replica": 1}

    def test_default_params_unknown_raises(self):
        """不存在的模板 ID 应抛出 ValueError。"""
        with pytest.raises(ValueError, match="nonexistent"):
            default_params("nonexistent")

    def test_default_params_are_valid(self):
        """默认参数应能通过 validate_params 校验。"""
        for template_id in get_all_templates():
            defaults = default_params(template_id)
            errors = validate_params(template_id, defaults)
            assert errors == [], (
                f"模板 '{template_id}' 的默认参数未通过校验: {errors}"
            )


# ---------------------------------------------------------------------------
# StrategyTemplate dataclass 结构
# ---------------------------------------------------------------------------

class TestStrategyTemplateDataclass:
    """StrategyTemplate 数据结构相关测试。"""

    def test_template_has_all_fields(self):
        """StrategyTemplate 应包含所有必需字段。"""
        template = get_template("sync_direct")
        assert hasattr(template, "id")
        assert hasattr(template, "description")
        assert hasattr(template, "params_schema")
        assert hasattr(template, "calculate_capacity")
        assert hasattr(template, "assertions")
        assert hasattr(template, "effective_load_multiplier")

    def test_params_schema_keys_match(self):
        """每个模板的 params_schema 键应与 default_params 键一致。"""
        for template_id, template in get_all_templates().items():
            defaults = default_params(template_id)
            assert set(template.params_schema.keys()) == set(defaults.keys()), (
                f"模板 '{template_id}' 的 params_schema 键与 default_params 键不匹配"
            )
