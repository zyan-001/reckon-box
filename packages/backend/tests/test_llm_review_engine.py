"""
LLM 审查引擎模块测试
====================
验证 review、build_review_prompt、_extract_json、_rule_based_review 的行为。

测试策略：
  - 通过 mock get_llm_config 和 openai 模块模拟 LLM 调用
  - 直接测试 _extract_json 的各种边界情况
  - 验证 prompt 构建包含报告摘要、策略信息、黑盒规格
  - 验证 guidance/非 guidance 区分
  - 验证知识卡片提取逻辑
  - 验证降级到规则引擎的各种场景
"""

import json
from unittest.mock import MagicMock, patch

from modules.llm_review_engine import (
    ReviewIssue,
    ReviewResult,
    _extract_json,
    _rule_based_review,
    build_review_prompt,
    review,
)
from utils.micro_skill import MicroSkill
from utils.blackbox_spec import BlackBoxSpec


# ---------------------------------------------------------------------------
# 测试夹具
# ---------------------------------------------------------------------------

SAMPLE_REPORT = {
    "total_requests": 100,
    "successful": 70,
    "failed": 30,
    "success_rate": 70.0,
    "component_stats": {
        "order_service": {
            "type": "service",
            "crash_count": 2,
            "total_processed": 50,
            "total_failed": 20,
        },
        "database": {
            "type": "database",
            "crash_count": 0,
            "total_processed": 50,
            "total_failed": 0,
        },
    },
    "key_events": [
        {
            "tick": 10,
            "type": "component_crash",
            "component": "order_service",
            "detail": "order_service 内存溢出崩溃",
        },
        {
            "tick": 15,
            "type": "circuit_breaker_open",
            "component": "order_service",
            "detail": "order_service 熔断器触发",
        },
        {
            "tick": 20,
            "type": "assertion_triggered",
            "component": "order_service",
            "detail": "断言触发: 崩溃风险=retry_storm",
        },
        {
            "tick": 25,
            "type": "request_timeout",
            "component": "gateway",
            "detail": "请求超时",
        },
    ],
    "failure_modes": [],
}

SAMPLE_MICRO_SKILLS = {
    "order_service": MicroSkill(
        strategy_template_id="retry_no_backoff",
        params={"max_retries": 3},
    ),
    "database": MicroSkill(
        strategy_template_id="sync_direct",
        params={},
    ),
}

SAMPLE_BLACKBOX_SPEC = BlackBoxSpec(
    scenario_description="电商下单场景",
    performance_bounds={"max_qps": 1000, "target_latency_ms": 200, "availability_target": 0.999},
    exception_scenarios=[
        {"id": "e1", "name": "数据库宕机", "description": "数据库全部副本不可用"},
    ],
)

_FAKE_CONFIG = {
    "api_key": "fake-key",
    "base_url": "https://llm-5xhy5uqkd3ej0due.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
    "model": "deepseek-v4-flash",
}


def _make_fake_openai(content: str, client=None):
    """构造一个 mock openai 模块，其 OpenAI().chat.completions.create 返回 content。"""
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
# 1. build_review_prompt 测试
# ---------------------------------------------------------------------------

def test_build_review_prompt_contains_report_summary():
    """prompt 应包含报告摘要（成功率、请求数等）。"""
    prompt = build_review_prompt(SAMPLE_REPORT, SAMPLE_MICRO_SKILLS)
    assert "100" in prompt  # 总请求数
    assert "70.0" in prompt or "70" in prompt  # 成功率
    assert "成功" in prompt


def test_build_review_prompt_contains_micro_skill_info():
    """prompt 应包含每个组件的 MicroSkill 策略信息。"""
    prompt = build_review_prompt(SAMPLE_REPORT, SAMPLE_MICRO_SKILLS)
    assert "retry_no_backoff" in prompt
    assert "sync_direct" in prompt
    assert "order_service" in prompt
    assert "database" in prompt


def test_build_review_prompt_contains_blackbox_spec():
    """prompt 应包含黑盒规格的性能边界信息。"""
    prompt = build_review_prompt(SAMPLE_REPORT, SAMPLE_MICRO_SKILLS, SAMPLE_BLACKBOX_SPEC)
    assert "max_qps" in prompt
    assert "1000" in prompt
    assert "数据库宕机" in prompt


def test_build_review_prompt_without_blackbox_spec():
    """无黑盒规格时 prompt 不应包含性能边界章节。"""
    prompt = build_review_prompt(SAMPLE_REPORT, SAMPLE_MICRO_SKILLS)
    assert "黑盒规格" not in prompt


def test_build_review_prompt_contains_key_events():
    """prompt 应包含关键事件。"""
    prompt = build_review_prompt(SAMPLE_REPORT, SAMPLE_MICRO_SKILLS)
    assert "内存溢出崩溃" in prompt
    assert "熔断器触发" in prompt


def test_build_review_prompt_contains_check_dimensions():
    """prompt 应包含检查维度说明。"""
    prompt = build_review_prompt(SAMPLE_REPORT, SAMPLE_MICRO_SKILLS)
    assert "strategy_mismatch" in prompt
    assert "missing_degradation" in prompt
    assert "anti_pattern" in prompt


def test_build_review_prompt_requires_json_output():
    """prompt 应明确要求 LLM 返回 JSON 格式并包含 is_guidance 字段。"""
    prompt = build_review_prompt(SAMPLE_REPORT, SAMPLE_MICRO_SKILLS)
    assert "JSON" in prompt or "json" in prompt
    assert "is_guidance" in prompt


def test_build_review_prompt_distinguishes_guidance_modes():
    """prompt 应说明 is_guidance=true 和 is_guidance=false 的区别。"""
    prompt = build_review_prompt(SAMPLE_REPORT, SAMPLE_MICRO_SKILLS)
    assert "Socratic" in prompt or "引导" in prompt
    assert "知识卡片" in prompt or "直接" in prompt


# ---------------------------------------------------------------------------
# 2. review LLM 正常审查测试
# ---------------------------------------------------------------------------

def test_review_llm_normal():
    """LLM 正常返回包含 guidance 和非 guidance 的 issues → 验证 ReviewResult。"""
    fake_issues = [
        {
            "type": "strategy_mismatch",
            "severity": "high",
            "component": "order_service",
            "description": "高并发场景使用 retry_no_backoff",
            "suggestion": "如果改用 retry_with_backoff 会怎样？",
            "is_guidance": True,
        },
        {
            "type": "anti_pattern",
            "severity": "medium",
            "component": "order_service",
            "description": "retry_no_backoff 在下游故障时会导致重试风暴",
            "suggestion": "添加熔断器或使用退避重试",
            "is_guidance": False,
        },
    ]
    fake_openai, _ = _make_fake_openai(json.dumps(fake_issues))

    with patch.dict("sys.modules", {"openai": fake_openai}), \
         patch("modules.llm_review_engine.get_llm_config", return_value=_FAKE_CONFIG):
        result = review(SAMPLE_REPORT, SAMPLE_MICRO_SKILLS)

    assert isinstance(result, ReviewResult)
    assert len(result.issues) == 2
    assert result.issues[0].type == "strategy_mismatch"
    assert result.issues[1].type == "anti_pattern"
    assert "发现 2 个问题" in result.summary


# ---------------------------------------------------------------------------
# 3. review LLM 返回 guidance 区分测试
# ---------------------------------------------------------------------------

def test_review_llm_guidance_distinction():
    """验证 is_guidance=True 和 is_guidance=False 的正确区分。"""
    fake_issues = [
        {
            "type": "strategy_mismatch",
            "severity": "high",
            "component": "order_service",
            "description": "考虑使用更好的策略",
            "suggestion": "如果改用 retry_with_backoff 会怎样？",
            "is_guidance": True,
        },
        {
            "type": "anti_pattern",
            "severity": "medium",
            "component": "order_service",
            "description": "重试风暴风险",
            "suggestion": "添加熔断器",
            "is_guidance": False,
        },
        {
            "type": "missing_degradation",
            "severity": "low",
            "component": "database",
            "description": "缺少降级方案",
            "suggestion": "考虑添加只读缓存作为降级",
            "is_guidance": True,
        },
    ]
    fake_openai, _ = _make_fake_openai(json.dumps(fake_issues))

    with patch.dict("sys.modules", {"openai": fake_openai}), \
         patch("modules.llm_review_engine.get_llm_config", return_value=_FAKE_CONFIG):
        result = review(SAMPLE_REPORT, SAMPLE_MICRO_SKILLS)

    # 验证 guidance 区分
    guidance_issues = [i for i in result.issues if i.is_guidance]
    non_guidance_issues = [i for i in result.issues if not i.is_guidance]
    assert len(guidance_issues) == 2
    assert len(non_guidance_issues) == 1
    assert non_guidance_issues[0].type == "anti_pattern"


# ---------------------------------------------------------------------------
# 4. review 知识卡片提取测试
# ---------------------------------------------------------------------------

def test_review_knowledge_cards_extraction():
    """is_guidance=False 的 issue 被提取为 knowledge_cards。"""
    fake_issues = [
        {
            "type": "strategy_mismatch",
            "severity": "high",
            "component": "order_service",
            "description": "考虑使用更好的策略",
            "suggestion": "如果改用 retry_with_backoff？",
            "is_guidance": True,
        },
        {
            "type": "anti_pattern",
            "severity": "medium",
            "component": "order_service",
            "description": "重试风暴风险",
            "suggestion": "添加熔断器",
            "is_guidance": False,
        },
        {
            "type": "missing_degradation",
            "severity": "high",
            "component": "database",
            "description": "无降级方案",
            "suggestion": "添加只读缓存",
            "is_guidance": False,
        },
    ]
    fake_openai, _ = _make_fake_openai(json.dumps(fake_issues))

    with patch.dict("sys.modules", {"openai": fake_openai}), \
         patch("modules.llm_review_engine.get_llm_config", return_value=_FAKE_CONFIG):
        result = review(SAMPLE_REPORT, SAMPLE_MICRO_SKILLS)

    # 知识卡片只从 is_guidance=False 的 issue 提取
    assert len(result.knowledge_cards) == 2
    card = result.knowledge_cards[0]
    assert "title" in card
    assert "concept" in card
    assert "explanation" in card
    assert "order_service" in card["title"]
    assert card["concept"] == "添加熔断器"
    assert card["explanation"] == "重试风暴风险"


# ---------------------------------------------------------------------------
# 5. review LLM 不可用降级测试
# ---------------------------------------------------------------------------

def test_review_llm_unavailable_fallback():
    """get_llm_config 返回 None → 基于规则引擎反馈。"""
    with patch("modules.llm_review_engine.get_llm_config", return_value=None):
        result = review(SAMPLE_REPORT, SAMPLE_MICRO_SKILLS)

    assert isinstance(result, ReviewResult)
    assert "规则引擎" in result.summary
    # 规则引擎从 key_events 提取了 3 个问题（crash + circuit_breaker + assertion）
    assert len(result.issues) == 3
    for issue in result.issues:
        assert issue.is_guidance is False


# ---------------------------------------------------------------------------
# 6. review LLM 调用失败测试
# ---------------------------------------------------------------------------

def test_review_llm_call_failure():
    """OpenAI 抛异常 → 基于规则引擎反馈。"""
    fake_client = MagicMock()
    fake_client.chat.completions.create.side_effect = RuntimeError("network error")
    fake_openai = MagicMock()
    fake_openai.OpenAI.return_value = fake_client

    with patch.dict("sys.modules", {"openai": fake_openai}), \
         patch("modules.llm_review_engine.get_llm_config", return_value=_FAKE_CONFIG):
        result = review(SAMPLE_REPORT, SAMPLE_MICRO_SKILLS)

    assert isinstance(result, ReviewResult)
    assert "规则引擎" in result.summary
    assert len(result.issues) > 0


# ---------------------------------------------------------------------------
# 7. review LLM 返回格式无法解析测试
# ---------------------------------------------------------------------------

def test_review_llm_invalid_json_fallback():
    """LLM 返回格式无法解析 → 基于规则引擎反馈。"""
    fake_openai, _ = _make_fake_openai("这根本不是 JSON 格式的内容")

    with patch.dict("sys.modules", {"openai": fake_openai}), \
         patch("modules.llm_review_engine.get_llm_config", return_value=_FAKE_CONFIG):
        result = review(SAMPLE_REPORT, SAMPLE_MICRO_SKILLS)

    assert isinstance(result, ReviewResult)
    assert "规则引擎" in result.summary


# ---------------------------------------------------------------------------
# 8. _rule_based_review 测试
# ---------------------------------------------------------------------------

def test_rule_based_review_extracts_issues():
    """从 key_events 提取问题，生成 ReviewIssue（is_guidance=False）。"""
    result = _rule_based_review(SAMPLE_REPORT)

    assert isinstance(result, ReviewResult)
    assert "规则引擎" in result.summary
    # 3 个关键事件符合规则：component_crash, circuit_breaker_open, assertion_triggered
    assert len(result.issues) == 3
    # 全部 is_guidance=False
    for issue in result.issues:
        assert issue.is_guidance is False

    # 验证事件类型映射
    types = [i.type for i in result.issues]
    assert "missing_degradation" in types  # component_crash → missing_degradation
    assert "anti_pattern" in types  # circuit_breaker_open → anti_pattern
    assert "logic_issue" in types  # assertion_triggered → logic_issue


def test_rule_based_review_builds_knowledge_cards():
    """规则引擎结果也应有知识卡片。"""
    result = _rule_based_review(SAMPLE_REPORT)
    assert len(result.knowledge_cards) == 3
    for card in result.knowledge_cards:
        assert "title" in card
        assert "concept" in card
        assert "explanation" in card


# ---------------------------------------------------------------------------
# 9. review 空 report 测试
# ---------------------------------------------------------------------------

def test_review_empty_report():
    """无 key_events → issues 为空。"""
    empty_report = {
        "total_requests": 0,
        "successful": 0,
        "failed": 0,
        "success_rate": 0,
        "component_stats": {},
        "key_events": [],
        "failure_modes": [],
    }

    with patch("modules.llm_review_engine.get_llm_config", return_value=None):
        result = review(empty_report)

    assert isinstance(result, ReviewResult)
    assert len(result.issues) == 0
    assert len(result.knowledge_cards) == 0


# ---------------------------------------------------------------------------
# 10. _extract_json 测试
# ---------------------------------------------------------------------------

def test_extract_json_plain_array():
    """纯 JSON 数组应能正确解析。"""
    text = json.dumps([
        {"type": "logic_issue", "severity": "high",
         "component": "x", "description": "y", "suggestion": "z", "is_guidance": False}
    ])
    result = _extract_json(text)
    assert len(result) == 1
    assert result[0]["type"] == "logic_issue"


def test_extract_json_code_block():
    """```json 包裹的内容应能正确解析。"""
    text = """```json
[
  {"type": "anti_pattern", "severity": "low",
   "component": "a", "description": "b", "suggestion": "c", "is_guidance": true}
]
```"""
    result = _extract_json(text)
    assert len(result) == 1
    assert result[0]["is_guidance"] is True


def test_extract_json_invalid_raises():
    """无法解析的内容应抛出 ValueError。"""
    try:
        _extract_json("这完全不是 JSON 内容")
        assert False, "应抛出 ValueError"
    except ValueError:
        pass


# ---------------------------------------------------------------------------
# 11. LLM 返回空 issues 测试
# ---------------------------------------------------------------------------

def test_review_llm_empty_issues():
    """LLM 返回空数组 → ReviewResult.issues 为空。"""
    fake_openai, _ = _make_fake_openai("[]")

    with patch.dict("sys.modules", {"openai": fake_openai}), \
         patch("modules.llm_review_engine.get_llm_config", return_value=_FAKE_CONFIG):
        result = review(SAMPLE_REPORT, SAMPLE_MICRO_SKILLS)

    assert len(result.issues) == 0
    assert len(result.knowledge_cards) == 0
    assert "发现 0 个问题" in result.summary
