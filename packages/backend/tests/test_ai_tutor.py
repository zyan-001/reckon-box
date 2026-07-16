"""
AI Tutor 模块测试
=================
验证 generate_educational_feedback 和 build_analysis_prompt 的行为。

测试策略：
  - 使用 mock report 和 design_spec（不依赖沙盒引擎）
  - report 格式参考 demo/reckon_sandbox.py 的 get_report() 输出
  - design_spec 格式参考 reckonbox_mvp_design.md 第210-233行
"""

from modules.ai_tutor import generate_educational_feedback, build_analysis_prompt
from modules.decision_tree_analyzer import DecisionTreeAnalyzer


# ---------------------------------------------------------------------------
# 测试夹具：mock design_spec 和 report
# ---------------------------------------------------------------------------

# 初学者设计规格（3组件，参考 reckonbox_mvp_design.md 第210-233行格式）
NOVICE_DESIGN_SPEC = {
    "domain": "software_systems",
    "components": [
        {
            "name": "api_gateway",
            "type": "gateway",
            "replicas": 2,
            "retry_count": 0,
            "has_circuit_breaker": False,
            "has_rate_limiter": False,
            "memory_limit": 1024,
            "queue_capacity": 500,
            "memory_per_request": 5,
            "timeout_ms": 2000,
            "dependencies": ["order_service"],
        },
        {
            "name": "order_service",
            "type": "service",
            "replicas": 2,
            "retry_count": 3,
            "has_circuit_breaker": False,
            "dependencies": ["database"],
        },
        {
            "name": "database",
            "type": "database",
            "replicas": 1,
            "retry_count": 0,
            "has_circuit_breaker": False,
            "memory_limit": 2048,
            "queue_capacity": 300,
            "memory_per_request": 5,
            "timeout_ms": 3000,
            "dependencies": [],
        },
    ],
}


def _make_flash_sale_report():
    """构造一个 flash_sale 场景的 mock report（含失败模式）。

    report 字段参考 demo/reckon_sandbox.py 的 get_report() 输出结构。
    """
    return {
        "scenario": "flash_sale",
        "total_ticks": 100,
        "total_requests": 8000,
        "successful": 5200,
        "failed": 2800,
        "success_rate": 65.0,
        "component_stats": {
            "api_gateway": {
                "type": "gateway",
                "replicas": 2,
                "healthy_replicas": 2,
                "total_processed": 5200,
                "total_failed": 2800,
                "total_retried": 0,
                "total_rate_limited": 0,
                "crash_count": 0,
                "crash_history": [],
                "has_circuit_breaker": False,
                "has_rate_limiter": False,
                "circuit_opened": False,
                "retry_count_config": 0,
                "queue_capacity": 500,
                "dependencies": ["order_service"],
            },
            "order_service": {
                "type": "service",
                "replicas": 2,
                "healthy_replicas": 1,
                "total_processed": 4000,
                "total_failed": 1200,
                "total_retried": 600,
                "total_rate_limited": 0,
                "crash_count": 1,
                "crash_history": [
                    {"tick": 45, "reason": "memory_exhausted",
                     "memory_used": 600, "limit": 512}
                ],
                "has_circuit_breaker": False,
                "has_rate_limiter": False,
                "circuit_opened": False,
                "retry_count_config": 3,
                "queue_capacity": 200,
                "dependencies": ["database"],
            },
            "database": {
                "type": "database",
                "replicas": 1,
                "healthy_replicas": 1,
                "total_processed": 3800,
                "total_failed": 200,
                "total_retried": 0,
                "total_rate_limited": 0,
                "crash_count": 0,
                "crash_history": [],
                "has_circuit_breaker": False,
                "has_rate_limiter": False,
                "circuit_opened": False,
                "retry_count_config": 0,
                "queue_capacity": 300,
                "dependencies": [],
            },
        },
        "failure_modes": [
            {
                "mode": "retry_storm",
                "severity": "high",
                "component": "order_service",
                "evidence": "order_service 产生 600 次重试（配置 retry_count=3）",
                "design_issue": "order_service 的 retry_count=3，依赖故障时放大 4 倍负载",
                "suggestion": "降低 retry_count、添加指数退避、或增加熔断器防止重试风暴",
            },
            {
                "mode": "missing_rate_limiter",
                "severity": "medium",
                "component": "api_gateway",
                "evidence": "网关无限流，直接承受全部流量",
                "design_issue": "网关没有 rate_limiter，洪峰流量直接冲击下游服务",
                "suggestion": "在网关添加 rate_limiter 限制入站流量",
            },
        ],
        "key_events": [
            {"tick": 25, "type": "queue_overflow", "component": "order_service",
             "detail": "order_service 队列溢出！220/200，丢弃 #1500"},
            {"tick": 45, "type": "component_crash", "component": "order_service",
             "detail": "order_service 内存溢出崩溃！使用 600MB / 限制 512MB，剩余: 1/2"},
            {"tick": 60, "type": "request_timeout", "component": "api_gateway",
             "detail": "请求 #3000 超时（在 api_gateway 队列中等待过久）"},
        ],
        "all_events_count": 150,
    }


def _make_normal_report_no_failures():
    """构造一个 normal 场景、无失败模式的 mock report。"""
    return {
        "scenario": "normal",
        "total_ticks": 100,
        "total_requests": 3000,
        "successful": 2950,
        "failed": 50,
        "success_rate": 98.3,
        "component_stats": {
            "api_gateway": {
                "type": "gateway",
                "replicas": 2,
                "healthy_replicas": 2,
                "total_processed": 2950,
                "total_failed": 50,
                "total_retried": 0,
                "total_rate_limited": 0,
                "crash_count": 0,
                "crash_history": [],
                "has_circuit_breaker": False,
                "has_rate_limiter": False,
                "circuit_opened": False,
                "retry_count_config": 0,
                "queue_capacity": 500,
                "dependencies": ["order_service"],
            },
            "order_service": {
                "type": "service",
                "replicas": 2,
                "healthy_replicas": 2,
                "total_processed": 2900,
                "total_failed": 50,
                "total_retried": 10,
                "total_rate_limited": 0,
                "crash_count": 0,
                "crash_history": [],
                "has_circuit_breaker": False,
                "has_rate_limiter": False,
                "circuit_opened": False,
                "retry_count_config": 3,
                "queue_capacity": 200,
                "dependencies": ["database"],
            },
            "database": {
                "type": "database",
                "replicas": 1,
                "healthy_replicas": 1,
                "total_processed": 2880,
                "total_failed": 20,
                "total_retried": 0,
                "total_rate_limited": 0,
                "crash_count": 0,
                "crash_history": [],
                "has_circuit_breaker": False,
                "has_rate_limiter": False,
                "circuit_opened": False,
                "retry_count_config": 0,
                "queue_capacity": 300,
                "dependencies": [],
            },
        },
        "failure_modes": [],
        "key_events": [],
        "all_events_count": 20,
    }


# ---------------------------------------------------------------------------
# 测试用例
# ---------------------------------------------------------------------------

def test_feedback_contains_coverage():
    """验证反馈包含「知识覆盖率」（决策树概览部分）。"""
    report = _make_flash_sale_report()
    feedback = generate_educational_feedback(
        "初学者设计", report, design_spec=NOVICE_DESIGN_SPEC
    )
    assert "知识覆盖率" in feedback, "反馈中应包含「知识覆盖率」"


def test_feedback_contains_reasoning_gap():
    """验证反馈包含「推理链断裂」（决策树分析部分）。"""
    report = _make_flash_sale_report()
    feedback = generate_educational_feedback(
        "初学者设计", report, design_spec=NOVICE_DESIGN_SPEC
    )
    assert "推理链断裂" in feedback, "反馈中应包含「推理链断裂」"


def test_feedback_contains_guiding_question():
    """验证反馈包含「思考题」（引导性提问）。"""
    report = _make_flash_sale_report()
    feedback = generate_educational_feedback(
        "初学者设计", report, design_spec=NOVICE_DESIGN_SPEC
    )
    assert "思考题" in feedback, "反馈中应包含「思考题」"


def test_feedback_no_failure_suggests_extreme():
    """验证无失败模式时反馈建议尝试更极端的场景。"""
    report = _make_normal_report_no_failures()
    feedback = generate_educational_feedback(
        "初学者设计", report, design_spec=None
    )
    assert "建议尝试更极端的场景" in feedback, (
        "无失败模式时应建议尝试更极端的场景"
    )


def test_interface_signature():
    """验证 generate_educational_feedback 可用3个参数调用且返回 str。"""
    report = _make_flash_sale_report()
    result = generate_educational_feedback(
        "初学者设计", report, NOVICE_DESIGN_SPEC
    )
    assert isinstance(result, str), "generate_educational_feedback 应返回 str"
    assert len(result) > 0, "反馈内容不应为空"
