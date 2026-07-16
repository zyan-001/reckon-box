"""RemediationGuide 修复引导模块测试
================================
验证 generate_guidance 生成的 RemediationReport 三部分行为：
  - crash_analysis: 严重性排序、rank 递增、结构完整性
  - teaching_cards: 卡片加载、未知模式跳过
  - design_guidance: LLM 可用/不可用两条路径、LLM 调用失败回退

测试策略：
  - 使用 mock report（不依赖沙盒引擎）
  - 通过 patch modules.remediation_guide.get_llm_config 控制 design_guidance 路径
  - 通过 patch modules.teaching_cards.get_llm_config 控制 teaching_cards 个性化路径
  - LLM 可用路径额外 mock openai 模块

注意：conftest.py 的 autouse fixture 仅 patch modules.ai_tutor.get_llm_config，
不影响 modules.remediation_guide 命名空间下的 get_llm_config 本地引用
（from modules.ai_tutor import get_llm_config 已绑定到本模块命名空间）。
因此本测试自行 patch modules.remediation_guide.get_llm_config。
"""

import json
from unittest.mock import MagicMock, patch

from modules.remediation_guide import (
    FALLBACK_GUIDANCE,
    RemediationReport,
    generate_guidance,
)


# ---------------------------------------------------------------------------
# 测试夹具：mock report 和 design_spec
# ---------------------------------------------------------------------------

SAMPLE_DESIGN_SPEC = {
    "domain": "software_systems",
    "components": [
        {
            "name": "api_gateway",
            "type": "gateway",
            "replicas": 2,
            "retry_count": 0,
            "has_circuit_breaker": False,
            "has_rate_limiter": False,
            "dependencies": ["order_service"],
        },
        {
            "name": "order_service",
            "type": "service",
            "replicas": 1,
            "retry_count": 3,
            "has_circuit_breaker": False,
            "dependencies": ["database"],
        },
        {
            "name": "database",
            "type": "database",
            "replicas": 1,
            "retry_count": 0,
            "dependencies": [],
        },
    ],
}


def _make_report_with_failure_modes(failure_modes):
    """构造一个 mock report，含指定的 failure_modes 列表。

    report 字段参考 SandboxEngine.get_report() 输出结构。
    """
    return {
        "scenario": "flash_sale",
        "total_requests": 8000,
        "successful": 5200,
        "failed": 2800,
        "success_rate": 65.0,
        "component_stats": {},
        "failure_modes": failure_modes,
        "key_events": [],
    }


# 涵盖 critical/high/medium 三种严重性等级的失败模式样本（顺序故意打乱）
MIXED_FAILURE_MODES = [
    {
        "mode": "queue_overflow",
        "severity": "medium",
        "component": "order_service",
        "evidence": "order_service 发生 5 次队列溢出",
        "suggestion": "增大 queue_capacity",
    },
    {
        "mode": "spof_cascade",
        "severity": "critical",
        "component": "database",
        "evidence": "database 仅 1 副本且崩溃 2 次",
        "suggestion": "为 database 增加副本",
    },
    {
        "mode": "retry_storm",
        "severity": "high",
        "component": "order_service",
        "evidence": "order_service 产生 120 次重试",
        "suggestion": "降低 retry_count",
    },
    {
        "mode": "missing_rate_limiter",
        "severity": "medium",
        "component": "api_gateway",
        "evidence": "网关无限流，直接承受全部流量",
        "suggestion": "添加 rate_limiter",
    },
]


_FAKE_CONFIG = {"api_key": "fake-key", "base_url": "https://llm-5xhy5uqkd3ej0due.cn-beijing.maas.aliyuncs.com/compatible-mode/v1", "model": "deepseek-v4-flash"}


def _make_fake_openai(content: str, client=None):
    """构造一个 mock openai 模块，其 OpenAI().chat.completions.create 返回 content。

    如果提供 client，则使用 client（用于测试调用失败等场景）。
    """
    fake_response = MagicMock()
    fake_response.choices = [
        MagicMock(message=MagicMock(content=content))
    ]
    if client is None:
        client = MagicMock()
    client.chat.completions.create.return_value = fake_response

    fake_openai = MagicMock()
    fake_openai.OpenAI.return_value = client
    return fake_openai, client


# ---------------------------------------------------------------------------
# 1. crash_analysis 测试
# ---------------------------------------------------------------------------

def test_crash_analysis_sorted_by_severity():
    """失败模式应按 critical>high>medium>low 排序。"""
    report = _make_report_with_failure_modes(MIXED_FAILURE_MODES)

    with patch("modules.remediation_guide.get_llm_config", return_value=None), \
         patch("modules.teaching_cards.get_llm_config", return_value=None):
        result = generate_guidance(report)

    assert isinstance(result, RemediationReport)
    assert len(result.crash_analysis) == 4
    # 排序应为：critical(spof_cascade) → high(retry_storm)
    #          → medium(queue_overflow, missing_rate_limiter)
    severities = [item["severity"] for item in result.crash_analysis]
    assert severities == ["critical", "high", "medium", "medium"]
    # 第一个应是 spof_cascade（critical）
    assert result.crash_analysis[0]["failure_mode"] == "spof_cascade"
    assert result.crash_analysis[0]["component"] == "database"


def test_crash_analysis_rank():
    """rank 应从 1 开始递增。"""
    report = _make_report_with_failure_modes(MIXED_FAILURE_MODES)

    with patch("modules.remediation_guide.get_llm_config", return_value=None), \
         patch("modules.teaching_cards.get_llm_config", return_value=None):
        result = generate_guidance(report)

    ranks = [item["rank"] for item in result.crash_analysis]
    assert ranks == [1, 2, 3, 4]


def test_crash_analysis_structure():
    """crash_analysis 每项应包含 failure_mode/component/severity/evidence/rank。"""
    report = _make_report_with_failure_modes(MIXED_FAILURE_MODES)

    with patch("modules.remediation_guide.get_llm_config", return_value=None), \
         patch("modules.teaching_cards.get_llm_config", return_value=None):
        result = generate_guidance(report)

    required_keys = {"failure_mode", "component", "severity", "evidence", "rank"}
    for item in result.crash_analysis:
        assert required_keys.issubset(item.keys())
    # evidence 字段应保留原始失败证据
    evidences = [item["evidence"] for item in result.crash_analysis]
    assert any("120 次重试" in e for e in evidences)


# ---------------------------------------------------------------------------
# 2. teaching_cards 测试
# ---------------------------------------------------------------------------

def test_teaching_cards_loaded():
    """每个已知失败模式应有一张教学卡片。"""
    # 使用所有 8 种已知失败模式
    all_modes = [
        {"mode": "retry_storm", "severity": "high", "component": "order_service",
         "evidence": "order_service 产生 120 次重试"},
        {"mode": "spof_cascade", "severity": "critical", "component": "database",
         "evidence": "database 单点崩溃"},
        {"mode": "queue_overflow", "severity": "medium", "component": "order_service",
         "evidence": "queue 溢出"},
        {"mode": "missing_circuit_breaker", "severity": "high",
         "component": "order_service", "evidence": "无熔断器"},
        {"mode": "memory_exhaustion", "severity": "high", "component": "order_service",
         "evidence": "内存耗尽"},
        {"mode": "missing_rate_limiter", "severity": "medium",
         "component": "api_gateway", "evidence": "无限流"},
        {"mode": "consumer_lag_accumulation", "severity": "high",
         "component": "mq", "evidence": "积压"},
        {"mode": "scheduled_task_thundering_herd", "severity": "medium",
         "component": "task", "evidence": "羊群效应"},
    ]
    report = _make_report_with_failure_modes(all_modes)

    with patch("modules.remediation_guide.get_llm_config", return_value=None), \
         patch("modules.teaching_cards.get_llm_config", return_value=None):
        result = generate_guidance(report, design_spec=SAMPLE_DESIGN_SPEC)

    assert len(result.teaching_cards) == 8
    # 每张卡片结构正确
    for item in result.teaching_cards:
        assert "failure_mode" in item
        assert "card" in item
        card = item["card"]
        assert isinstance(card, dict)
        # 卡片应包含 TeachingCardBank 模板的必要字段
        assert "title" in card
        assert "explanation" in card


def test_teaching_cards_unknown_mode_skipped():
    """未知失败模式的卡片应被跳过。"""
    failure_modes = [
        {"mode": "retry_storm", "severity": "high", "component": "order_service",
         "evidence": "重试风暴"},
        {"mode": "totally_unknown_mode", "severity": "low", "component": "x",
         "evidence": "未知模式"},
    ]
    report = _make_report_with_failure_modes(failure_modes)

    with patch("modules.remediation_guide.get_llm_config", return_value=None), \
         patch("modules.teaching_cards.get_llm_config", return_value=None):
        result = generate_guidance(report)

    # 未知模式跳过，只剩一张
    assert len(result.teaching_cards) == 1
    assert result.teaching_cards[0]["failure_mode"] == "retry_storm"


# ---------------------------------------------------------------------------
# 3. design_guidance 测试
# ---------------------------------------------------------------------------

def test_design_guidance_no_llm():
    """LLM 不可用时应使用 FALLBACK_GUIDANCE 模板。"""
    report = _make_report_with_failure_modes(MIXED_FAILURE_MODES)

    with patch("modules.remediation_guide.get_llm_config", return_value=None), \
         patch("modules.teaching_cards.get_llm_config", return_value=None):
        result = generate_guidance(report)

    assert len(result.design_guidance) == 4
    # 每项结构正确
    for item in result.design_guidance:
        assert "failure_mode" in item
        assert "component" in item
        assert "question" in item
        # 问题来自 FALLBACK_GUIDANCE
        assert item["question"] == FALLBACK_GUIDANCE[item["failure_mode"]]
    # 验证具体提问内容
    questions_by_mode = {
        item["failure_mode"]: item["question"] for item in result.design_guidance
    }
    assert "retry_count" in questions_by_mode["retry_storm"]
    assert "副本" in questions_by_mode["spof_cascade"]


def test_design_guidance_with_llm():
    """LLM 可用时应使用 LLM 返回的引导性提问。"""
    report = _make_report_with_failure_modes(MIXED_FAILURE_MODES)

    llm_guidance = [
        {"failure_mode": "spof_cascade", "component": "database",
         "question": "为什么 database 设为单副本？多副本就能保证不丢数据吗？"},
        {"failure_mode": "retry_storm", "component": "order_service",
         "question": "order_service 的 retry_count=3 在下游故障时会如何放大影响？"},
        {"failure_mode": "queue_overflow", "component": "order_service",
         "question": "队列满后丢弃请求和让请求等待，对用户体验有何不同？"},
        {"failure_mode": "missing_rate_limiter", "component": "api_gateway",
         "question": "如果 api_gateway 加了限流，超出部分应该返回什么？"},
    ]
    fake_openai, _ = _make_fake_openai(json.dumps(llm_guidance, ensure_ascii=False))

    with patch.dict("sys.modules", {"openai": fake_openai}), \
         patch("modules.remediation_guide.get_llm_config",
               return_value=_FAKE_CONFIG), \
         patch("modules.teaching_cards.get_llm_config", return_value=None):
        result = generate_guidance(report, design_spec=SAMPLE_DESIGN_SPEC)

    assert len(result.design_guidance) == 4
    # LLM 返回的问题应被使用（而非 FALLBACK_GUIDANCE）
    questions = [item["question"] for item in result.design_guidance]
    assert any("retry_count=3" in q for q in questions)
    assert any("单副本" in q for q in questions)
    # 确认不是 fallback 文本（fallback 中 retry_storm 提问不含 "retry_count=3"）
    fallback_retry_q = FALLBACK_GUIDANCE["retry_storm"]
    assert fallback_retry_q not in questions


def test_design_guidance_llm_failure_falls_back():
    """LLM 调用失败时应回退到 FALLBACK_GUIDANCE。"""
    report = _make_report_with_failure_modes(MIXED_FAILURE_MODES)

    # mock openai 调用抛异常
    fake_client = MagicMock()
    fake_client.chat.completions.create.side_effect = RuntimeError("network error")
    fake_openai = MagicMock()
    fake_openai.OpenAI.return_value = fake_client

    with patch.dict("sys.modules", {"openai": fake_openai}), \
         patch("modules.remediation_guide.get_llm_config",
               return_value=_FAKE_CONFIG), \
         patch("modules.teaching_cards.get_llm_config", return_value=None):
        result = generate_guidance(report)

    # 应回退到 FALLBACK_GUIDANCE
    assert len(result.design_guidance) == 4
    for item in result.design_guidance:
        assert item["question"] == FALLBACK_GUIDANCE[item["failure_mode"]]


def test_design_guidance_llm_invalid_json_falls_back():
    """LLM 返回非 JSON 时应回退到 FALLBACK_GUIDANCE。"""
    report = _make_report_with_failure_modes(MIXED_FAILURE_MODES[:1])

    fake_openai, _ = _make_fake_openai("这根本不是 JSON 格式的内容")

    with patch.dict("sys.modules", {"openai": fake_openai}), \
         patch("modules.remediation_guide.get_llm_config",
               return_value=_FAKE_CONFIG), \
         patch("modules.teaching_cards.get_llm_config", return_value=None):
        result = generate_guidance(report)

    assert len(result.design_guidance) == 1
    assert result.design_guidance[0]["question"] == FALLBACK_GUIDANCE["queue_overflow"]


# ---------------------------------------------------------------------------
# 4. 边界情况
# ---------------------------------------------------------------------------

def test_empty_failure_modes():
    """无失败模式时应返回空列表。"""
    report = _make_report_with_failure_modes([])

    with patch("modules.remediation_guide.get_llm_config", return_value=None), \
         patch("modules.teaching_cards.get_llm_config", return_value=None):
        result = generate_guidance(report)

    assert isinstance(result, RemediationReport)
    assert result.crash_analysis == []
    assert result.teaching_cards == []
    assert result.design_guidance == []


def test_with_blackbox_context():
    """传入 blackbox_spec 时不应报错。"""
    report = _make_report_with_failure_modes(MIXED_FAILURE_MODES)
    blackbox_spec = {
        "scenario_description": "秒杀场景",
        "functional_points": ["创建订单", "查询订单"],
        "performance_bounds": {"max_qps": 5000, "target_latency_ms": 200},
        "exception_scenarios": [{"id": "e1", "name": "下游服务超时"}],
        "design_variants": [],
    }

    with patch("modules.remediation_guide.get_llm_config", return_value=None), \
         patch("modules.teaching_cards.get_llm_config", return_value=None):
        result = generate_guidance(
            report,
            design_spec=SAMPLE_DESIGN_SPEC,
            blackbox_spec=blackbox_spec,
        )

    # 不报错且结构完整
    assert isinstance(result, RemediationReport)
    assert len(result.crash_analysis) == 4
    assert len(result.teaching_cards) >= 1
    assert len(result.design_guidance) == 4


def test_with_blackbox_context_llm():
    """LLM 可用且传入 blackbox_spec 时不应报错，且使用 LLM 输出。"""
    report = _make_report_with_failure_modes(MIXED_FAILURE_MODES[:1])
    blackbox_spec = {
        "scenario_description": "秒杀场景",
        "functional_points": ["创建订单"],
    }
    llm_guidance = [
        {"failure_mode": "queue_overflow", "component": "order_service",
         "question": "在秒杀场景下，队列满后丢弃哪些请求最安全？"}
    ]
    fake_openai, _ = _make_fake_openai(json.dumps(llm_guidance, ensure_ascii=False))

    with patch.dict("sys.modules", {"openai": fake_openai}), \
         patch("modules.remediation_guide.get_llm_config",
               return_value=_FAKE_CONFIG), \
         patch("modules.teaching_cards.get_llm_config", return_value=None):
        result = generate_guidance(
            report,
            design_spec=SAMPLE_DESIGN_SPEC,
            blackbox_spec=blackbox_spec,
        )

    assert len(result.design_guidance) == 1
    assert result.design_guidance[0]["question"] == llm_guidance[0]["question"]
