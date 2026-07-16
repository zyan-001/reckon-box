"""收敛检查器测试
================
覆盖 check_convergence 的各种场景：
- 全部达标 / 部分达标 / 全部不达标
- 成功率、延迟、崩溃点各单项验收
- 无黑盒规格、缺字段等边界情况
"""

import sys
import os

# 确保能导入 src 下的模块
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

from modules.convergence_checker import check_convergence, ConvergenceGap, ConvergenceResult
from utils.blackbox_spec import BlackBoxSpec


# ---------------------------------------------------------------------------
# 测试数据构造辅助
# ---------------------------------------------------------------------------

def _make_report(
    total_requests=1000,
    successful=999,
    failed=1,
    success_rate=99.9,
    component_stats=None,
    assertion_events=None,
    key_events=None,
    total_ticks=100,
):
    """构造性能引擎 report 字典。"""
    return {
        "total_requests": total_requests,
        "successful": successful,
        "failed": failed,
        "success_rate": success_rate,
        "component_stats": component_stats or {},
        "assertion_events": assertion_events or [],
        "key_events": key_events or [],
        "total_ticks": total_ticks,
    }


def _make_spec(availability_target=None, target_latency_ms=None, max_qps=None):
    """构造 BlackBoxSpec，仅填充 performance_bounds。"""
    bounds = {}
    if availability_target is not None:
        bounds["availability_target"] = availability_target
    if target_latency_ms is not None:
        bounds["target_latency_ms"] = target_latency_ms
    if max_qps is not None:
        bounds["max_qps"] = max_qps
    return BlackBoxSpec(
        scenario_description="test",
        performance_bounds=bounds,
    )


# ---------------------------------------------------------------------------
# 测试用例
# ---------------------------------------------------------------------------

class TestCheckConvergence:
    """收敛检查器核心测试。"""

    def test_all_pass(self):
        """全部达标：success_rate=99.95% >= 99.9%，无 timeout，无崩溃点 → converged=True"""
        report = _make_report(success_rate=99.95, successful=999, total_ticks=10)
        spec = _make_spec(availability_target="99.9%", target_latency_ms=200)
        result = check_convergence(report, spec)

        assert result.converged is True
        assert all(g.passed for g in result.gaps)
        # 三项验收：availability, latency, crash_points
        metrics = {g.metric for g in result.gaps}
        assert "availability" in metrics
        assert "latency" in metrics
        assert "crash_points" in metrics

    def test_availability_fail(self):
        """成功率未达标：success_rate=95% < 99.9% → converged=False"""
        report = _make_report(success_rate=95.0, successful=950, failed=50)
        spec = _make_spec(availability_target="99.9%", target_latency_ms=200)
        result = check_convergence(report, spec)

        assert result.converged is False
        avail_gap = next(g for g in result.gaps if g.metric == "availability")
        assert avail_gap.passed is False
        assert "99.9%" in avail_gap.target
        assert "95.0%" in avail_gap.actual

    def test_latency_fail_with_timeout(self):
        """延迟不达标：有 timeout 事件 → converged=False，gaps 包含 latency 项"""
        report = _make_report(
            success_rate=99.95,
            key_events=[{"tick": 50, "type": "request_timeout", "component": "svc", "detail": "超时"}],
        )
        spec = _make_spec(availability_target="99.9%", target_latency_ms=200)
        result = check_convergence(report, spec)

        assert result.converged is False
        latency_gap = next(g for g in result.gaps if g.metric == "latency")
        assert latency_gap.passed is False
        assert "超时" in latency_gap.actual

    def test_crash_points_exist(self):
        """崩溃点存在：assertion_events 非空 → converged=False"""
        report = _make_report(
            success_rate=99.95,
            assertion_events=[
                {"tick": 30, "type": "assertion_triggered", "component": "svc",
                 "detail": "断言触发: 崩溃风险=retry_storm, 重试耗尽"},
            ],
        )
        spec = _make_spec(availability_target="99.9%", target_latency_ms=200)
        result = check_convergence(report, spec)

        assert result.converged is False
        crash_gap = next(g for g in result.gaps if g.metric == "crash_points")
        assert crash_gap.passed is False
        assert "1" in crash_gap.actual
        assert "retry_storm" in crash_gap.actual

    def test_partial_pass(self):
        """部分达标：成功率达标但延迟不达标 → converged=False"""
        report = _make_report(
            success_rate=99.95,
            key_events=[{"tick": 50, "type": "request_timeout", "component": "svc", "detail": "超时"}],
        )
        spec = _make_spec(availability_target="99.9%", target_latency_ms=200)
        result = check_convergence(report, spec)

        assert result.converged is False
        avail_gap = next(g for g in result.gaps if g.metric == "availability")
        assert avail_gap.passed is True
        latency_gap = next(g for g in result.gaps if g.metric == "latency")
        assert latency_gap.passed is False

    def test_no_blackbox_spec(self):
        """无黑盒规格：blackbox_spec=None → converged=True, summary 含 '无验收标准'"""
        report = _make_report(success_rate=50.0)
        result = check_convergence(report, None)

        assert result.converged is True
        assert result.gaps == []
        assert "无验收标准" in result.summary

    def test_availability_target_parsing(self):
        """availability_target 格式解析："99.9%" → 99.9, "99.99%" → 99.99"""
        report1 = _make_report(success_rate=99.95)
        spec1 = _make_spec(availability_target="99.9%")
        result1 = check_convergence(report1, spec1)
        avail1 = next(g for g in result1.gaps if g.metric == "availability")
        assert avail1.passed is True

        report2 = _make_report(success_rate=99.98)
        spec2 = _make_spec(availability_target="99.99%")
        result2 = check_convergence(report2, spec2)
        avail2 = next(g for g in result2.gaps if g.metric == "availability")
        assert avail2.passed is False

    def test_availability_target_missing(self):
        """availability_target 缺失：performance_bounds 中无 availability_target → 该项跳过"""
        report = _make_report(success_rate=50.0)
        spec = _make_spec(target_latency_ms=200)
        result = check_convergence(report, spec)

        metrics = {g.metric for g in result.gaps}
        assert "availability" not in metrics
        # latency 和 crash_points 仍应存在
        assert "latency" in metrics
        assert "crash_points" in metrics

    def test_target_latency_missing(self):
        """target_latency_ms 缺失：performance_bounds 中无 target_latency_ms → 该项跳过"""
        report = _make_report(success_rate=99.95)
        spec = _make_spec(availability_target="99.9%")
        result = check_convergence(report, spec)

        metrics = {g.metric for g in result.gaps}
        assert "latency" not in metrics
        assert "availability" in metrics
        assert "crash_points" in metrics

    def test_empty_report_no_spec(self):
        """空 report：total_requests=0 → 成功率 0%，如果无黑盒规格则通过"""
        report = _make_report(total_requests=0, successful=0, failed=0, success_rate=0)
        result = check_convergence(report, None)

        assert result.converged is True
        assert result.gaps == []

    def test_empty_report_with_spec(self):
        """空 report + 有黑盒规格 → 成功率 0% < 99.9%，应不达标"""
        report = _make_report(total_requests=0, successful=0, failed=0, success_rate=0, total_ticks=0)
        spec = _make_spec(availability_target="99.9%", target_latency_ms=200)
        result = check_convergence(report, spec)

        assert result.converged is False
        avail_gap = next(g for g in result.gaps if g.metric == "availability")
        assert avail_gap.passed is False

    def test_latency_estimated_pass(self):
        """延迟估算达标：无 timeout 事件，估算延迟 <= target → 通过"""
        # total_ticks=10, TICK_MS=100, successful=100 → 估算延迟 = 1000/100 = 10ms <= 200ms
        report = _make_report(success_rate=99.9, successful=100, total_ticks=10)
        spec = _make_spec(availability_target="99.9%", target_latency_ms=200)
        result = check_convergence(report, spec)

        latency_gap = next(g for g in result.gaps if g.metric == "latency")
        assert latency_gap.passed is True

    def test_latency_estimated_fail(self):
        """延迟估算不达标：无 timeout 事件，估算延迟 > target → 不通过"""
        # total_ticks=1000, TICK_MS=100, successful=10 → 估算延迟 = 100000/10 = 10000ms > 200ms
        report = _make_report(success_rate=99.9, successful=10, total_ticks=1000)
        spec = _make_spec(availability_target="99.9%", target_latency_ms=200)
        result = check_convergence(report, spec)

        latency_gap = next(g for g in result.gaps if g.metric == "latency")
        assert latency_gap.passed is False

    def test_multiple_crash_risk_types(self):
        """多种崩溃风险类型：assertion_events 含不同崩溃风险 → 列出所有类型"""
        report = _make_report(
            success_rate=99.95,
            assertion_events=[
                {"tick": 30, "type": "assertion_triggered", "component": "svc_a",
                 "detail": "断言触发: 崩溃风险=retry_storm, 重试耗尽"},
                {"tick": 50, "type": "assertion_triggered", "component": "svc_b",
                 "detail": "断言触发: 崩溃风险=cascading_failure, 级联故障"},
            ],
        )
        spec = _make_spec(availability_target="99.9%", target_latency_ms=200)
        result = check_convergence(report, spec)

        crash_gap = next(g for g in result.gaps if g.metric == "crash_points")
        assert crash_gap.passed is False
        assert "2" in crash_gap.actual
        assert "cascading_failure" in crash_gap.actual
        assert "retry_storm" in crash_gap.actual

    def test_summary_all_pass(self):
        """全部达标时 summary 描述正确"""
        report = _make_report(success_rate=99.95, successful=100, total_ticks=10)
        spec = _make_spec(availability_target="99.9%", target_latency_ms=200)
        result = check_convergence(report, spec)

        assert "达标" in result.summary
        assert "通过" in result.summary

    def test_summary_partial_fail(self):
        """部分不达标时 summary 描述正确"""
        report = _make_report(
            success_rate=95.0,
            successful=950,
            failed=50,
            key_events=[{"tick": 50, "type": "request_timeout", "component": "svc", "detail": "超时"}],
            assertion_events=[
                {"tick": 30, "type": "assertion_triggered", "component": "svc",
                 "detail": "断言触发: 崩溃风险=retry_storm, 重试耗尽"},
            ],
        )
        spec = _make_spec(availability_target="99.9%", target_latency_ms=200)
        result = check_convergence(report, spec)

        assert result.converged is False
        assert "未达标" in result.summary
        failed_count = sum(1 for g in result.gaps if not g.passed)
        assert f"{failed_count}" in result.summary
