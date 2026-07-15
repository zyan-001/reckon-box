"""决策树分析器测试（Module 5 子组件）"""

from modules.decision_tree_analyzer import DecisionTreeAnalyzer


# ---------------------------------------------------------------------------
# 测试夹具：初学者设计规格（6组件）
# ---------------------------------------------------------------------------

def _make_novice_spec():
    """构造初学者设计规格 dict。

    特征：
      - 网关无限流 → backpressure 缺失
      - 所有服务 retry_count=3 且无熔断器 → uniform_retry 矛盾 + retry_without_isolation 断裂
      - payment_service / database 单副本 → redundancy 缺失
    """
    return {
        "domain": "software_systems",
        "components": [
            {
                "name": "api_gateway",
                "type": "gateway",
                "replicas": 2,
                "retry_count": 0,
                "has_circuit_breaker": False,
                "has_rate_limiter": False,
                "rate_limit": 0,
                "memory_limit": 1024,
                "memory_per_request": 5,
                "queue_capacity": 500,
                "timeout_ms": 2000,
                "dependencies": ["order_service", "product_service", "payment_service"],
            },
            {
                "name": "order_service",
                "type": "service",
                "replicas": 2,
                "retry_count": 3,
                "has_circuit_breaker": False,
                "memory_limit": 512,
                "memory_per_request": 5,
                "queue_capacity": 200,
                "timeout_ms": 1000,
                "dependencies": ["database", "cache"],
            },
            {
                "name": "product_service",
                "type": "service",
                "replicas": 2,
                "retry_count": 3,
                "has_circuit_breaker": False,
                "memory_limit": 512,
                "memory_per_request": 5,
                "queue_capacity": 200,
                "timeout_ms": 800,
                "dependencies": ["database", "cache"],
            },
            {
                "name": "payment_service",
                "type": "service",
                "replicas": 1,
                "retry_count": 3,
                "has_circuit_breaker": False,
                "memory_limit": 256,
                "memory_per_request": 5,
                "queue_capacity": 100,
                "timeout_ms": 2000,
                "dependencies": ["database"],
            },
            {
                "name": "database",
                "type": "database",
                "replicas": 1,
                "retry_count": 0,
                "has_circuit_breaker": False,
                "memory_limit": 2048,
                "memory_per_request": 5,
                "queue_capacity": 300,
                "timeout_ms": 3000,
                "dependencies": [],
            },
            {
                "name": "cache",
                "type": "cache",
                "replicas": 2,
                "retry_count": 0,
                "has_circuit_breaker": False,
                "memory_limit": 1024,
                "memory_per_request": 5,
                "queue_capacity": 500,
                "timeout_ms": 100,
                "dependencies": [],
            },
        ],
    }


# ---------------------------------------------------------------------------
# 概念覆盖率：0 < coverage < 1
# ---------------------------------------------------------------------------

def test_concept_coverage():
    """覆盖率应大于 0 且小于 1（部分概念已展现，部分缺失）。"""
    analyzer = DecisionTreeAnalyzer()
    report = analyzer.analyze(_make_novice_spec())
    assert 0 < report.coverage_score < 1, (
        f"覆盖率应在 (0, 1) 区间，实际 {report.coverage_score}"
    )


# ---------------------------------------------------------------------------
# 矛盾检测：uniform_retry
# ---------------------------------------------------------------------------

def test_contradiction_detection():
    """应检测到 uniform_retry 矛盾（所有服务 retry_count 相同）。"""
    analyzer = DecisionTreeAnalyzer()
    report = analyzer.analyze(_make_novice_spec())
    contradiction_types = [c["type"] for c in report.contradictions]
    assert "uniform_retry" in contradiction_types, (
        f"应检测到 uniform_retry 矛盾，实际矛盾类型: {contradiction_types}"
    )


# ---------------------------------------------------------------------------
# 推理链断裂检测：retry_without_isolation
# ---------------------------------------------------------------------------

def test_reasoning_gap_detection():
    """应检测到 retry_without_isolation 推理链断裂。"""
    analyzer = DecisionTreeAnalyzer()
    report = analyzer.analyze(_make_novice_spec())
    gap_names = [g["gap"] for g in report.reasoning_gaps]
    assert "retry_without_isolation" in gap_names, (
        f"应检测到 retry_without_isolation 推理链断裂，实际: {gap_names}"
    )


# ---------------------------------------------------------------------------
# 覆盖率计算公式验证
# ---------------------------------------------------------------------------

def test_coverage_calculation():
    """coverage_score = len(demonstrated ∪ partial) / 8。"""
    analyzer = DecisionTreeAnalyzer()
    report = analyzer.analyze(_make_novice_spec())
    total_concepts = len(DecisionTreeAnalyzer.CONCEPTS)
    expected = len(set(report.concepts_demonstrated) | set(report.concepts_partial)) / total_concepts
    assert report.coverage_score == round(expected, 2), (
        f"覆盖率应为 {round(expected, 2)}，实际 {report.coverage_score}"
    )


# ---------------------------------------------------------------------------
# 缺失概念包含 backpressure 或 failure_isolation
# ---------------------------------------------------------------------------

def test_missing_concepts():
    """concepts_missing 应包含 backpressure 或 failure_isolation。"""
    analyzer = DecisionTreeAnalyzer()
    report = analyzer.analyze(_make_novice_spec())
    missing = set(report.concepts_missing)
    assert "backpressure" in missing or "failure_isolation" in missing, (
        f"缺失概念应包含 backpressure 或 failure_isolation，实际: {missing}"
    )
