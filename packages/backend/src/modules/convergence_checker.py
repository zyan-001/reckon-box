"""
ReckonBox 2.0 收敛检查器
========================
检查模拟结果是否收敛到黑盒规格定义的性能边界。

验收三项：
1. 成功率 vs availability_target
2. 平均延迟 vs target_latency_ms
3. 崩溃点数量（assertion_events）

依赖：
- BlackBoxSpec（src/utils/blackbox_spec.py）
- PerformanceEngine 的 get_report() 输出格式
"""

from dataclasses import dataclass

from utils.blackbox_spec import BlackBoxSpec


@dataclass
class ConvergenceGap:
    """单项验收差距"""
    metric: str          # "availability" | "latency" | "crash_points"
    target: str          # 目标值描述
    actual: str          # 实际值描述
    passed: bool         # 是否达标
    detail: str          # 差距说明


@dataclass
class ConvergenceResult:
    """收敛检查结果"""
    converged: bool              # 是否全部达标
    gaps: list                   # list[ConvergenceGap] 各项验收差距
    summary: str                 # 总结描述


def _parse_availability_target(raw: str) -> float | None:
    """解析 availability_target 字符串为浮点数。

    支持格式如 "99.9%" → 99.9, "99.99%" → 99.99。
    解析失败返回 None。
    """
    if not isinstance(raw, str):
        return None
    s = raw.strip().rstrip("%")
    try:
        return float(s)
    except ValueError:
        return None


def check_convergence(report: dict, blackbox_spec: BlackBoxSpec | None = None) -> ConvergenceResult:
    """检查模拟结果是否收敛。

    Args:
        report: 性能引擎的 get_report() 输出
        blackbox_spec: 黑盒规格（含性能边界），None 时跳过检查

    验收三项：
    1. 成功率 vs availability_target
       - availability_target 格式如 "99.9%"，解析为 float 99.9
       - report.success_rate >= target → 通过
    2. 平均延迟 vs target_latency_ms
       - 简化实现：如果 assertion_events 中有 timeout 类事件，视为延迟不达标
       - 如果无 timeout 事件，估算延迟 = total_ticks * TICK_MS / max(1, successful)
       - 实际延迟 <= target → 通过
    3. 崩溃点数量
       - assertion_events 数量 = 0 → 通过
       - 有崩溃点 → 不通过，列出崩溃风险类型

    blackbox_spec 为 None 时：
    - converged = True（无法判断，默认通过）
    - gaps 为空列表
    - summary = "无验收标准，跳过收敛检查"
    """
    # 无黑盒规格时默认通过
    if blackbox_spec is None:
        return ConvergenceResult(
            converged=True,
            gaps=[],
            summary="无验收标准，跳过收敛检查",
        )

    gaps: list[ConvergenceGap] = []
    bounds = blackbox_spec.performance_bounds or {}

    success_rate = report.get("success_rate", 0)
    successful = report.get("successful", 0)
    total_ticks = report.get("total_ticks", 0)
    assertion_events = report.get("assertion_events", [])
    key_events = report.get("key_events", [])

    # ---- 验收项1：成功率 vs availability_target ----
    if "availability_target" in bounds:
        raw_target = bounds["availability_target"]
        target_value = _parse_availability_target(raw_target)
        if target_value is not None:
            passed = success_rate >= target_value
            gaps.append(ConvergenceGap(
                metric="availability",
                target=f"≥{target_value}%",
                actual=f"{success_rate}%",
                passed=passed,
                detail=(
                    f"成功率 {success_rate}% 达标（≥{target_value}%）"
                    if passed
                    else f"成功率 {success_rate}% 未达标（需 ≥{target_value}%），差 {target_value - success_rate:.2f}%"
                ),
            ))

    # ---- 验收项2：平均延迟 vs target_latency_ms ----
    if "target_latency_ms" in bounds:
        target_latency = bounds["target_latency_ms"]
        # 检查是否有 timeout 事件
        timeout_events = [e for e in key_events if e.get("type") == "request_timeout"]
        if timeout_events:
            # 有 timeout 事件 → 延迟不达标
            gaps.append(ConvergenceGap(
                metric="latency",
                target=f"≤{target_latency}ms",
                actual="存在超时事件",
                passed=False,
                detail=f"检测到 {len(timeout_events)} 次超时事件，延迟未达标（需 ≤{target_latency}ms）",
            ))
        else:
            # 无 timeout 事件，估算平均延迟
            TICK_MS = 100  # 与 PerformanceEngine.TICK_MS 一致
            estimated_latency = (total_ticks * TICK_MS) / max(1, successful)
            passed = estimated_latency <= target_latency
            gaps.append(ConvergenceGap(
                metric="latency",
                target=f"≤{target_latency}ms",
                actual=f"≈{estimated_latency:.1f}ms",
                passed=passed,
                detail=(
                    f"估算延迟 {estimated_latency:.1f}ms 达标（≤{target_latency}ms）"
                    if passed
                    else f"估算延迟 {estimated_latency:.1f}ms 未达标（需 ≤{target_latency}ms）"
                ),
            ))

    # ---- 验收项3：崩溃点数量 ----
    if assertion_events:
        # 收集崩溃风险类型
        risk_types = set()
        for evt in assertion_events:
            detail = evt.get("detail", "")
            # 从断言事件的 detail 中提取崩溃风险类型
            # 格式如 "断言触发: 崩溃风险=retry_storm, ..."
            if "崩溃风险=" in detail:
                risk_str = detail.split("崩溃风险=")[1].split(",")[0].strip()
                risk_types.add(risk_str)
        risk_desc = ", ".join(sorted(risk_types)) if risk_types else "未知"
        gaps.append(ConvergenceGap(
            metric="crash_points",
            target="0 个崩溃点",
            actual=f"{len(assertion_events)} 个崩溃点（风险: {risk_desc}）",
            passed=False,
            detail=f"存在 {len(assertion_events)} 个崩溃点（风险类型: {risk_desc}），需为 0",
        ))
    else:
        gaps.append(ConvergenceGap(
            metric="crash_points",
            target="0 个崩溃点",
            actual="0 个崩溃点",
            passed=True,
            detail="无崩溃点，验收通过",
        ))

    # ---- 汇总结果 ----
    converged = all(g.passed for g in gaps)
    failed_count = sum(1 for g in gaps if not g.passed)

    if converged:
        summary = "所有验收项均达标，收敛检查通过"
    else:
        summary = f"{len(gaps)} 项验收中 {failed_count} 项未达标，收敛检查未通过"

    return ConvergenceResult(
        converged=converged,
        gaps=gaps,
        summary=summary,
    )
