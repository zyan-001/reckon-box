"""
Design Reviewer 模块测试
========================
验证 review_design、build_review_prompt、_extract_json 的行为。

测试策略：
  - 通过 mock get_llm_config 和 openai 模块模拟 LLM 调用
  - 直接测试 _extract_json 的各种边界情况
  - 验证 prompt 构建包含拓扑和 description 信息
"""

import json
from unittest.mock import MagicMock, patch

from modules.design_reviewer import (
    _extract_json,
    build_review_prompt,
    review_design,
)


# ---------------------------------------------------------------------------
# 测试夹具：mock design_spec
# ---------------------------------------------------------------------------

# 参考 reckonbox_mvp_design.md 第210-233行格式，附带 description 字段
SAMPLE_DESIGN_SPEC = {
    "domain": "software_systems",
    "components": [
        {
            "name": "api_gateway",
            "type": "gateway",
            "replicas": 2,
            "retry_count": 0,
            "has_circuit_breaker": False,
            "dependencies": ["order_service"],
            "description": "接收用户请求，调用 order_service 创建订单",
        },
        {
            "name": "order_service",
            "type": "service",
            "replicas": 1,
            "retry_count": 3,
            "has_circuit_breaker": False,
            "dependencies": ["database"],
            "description": "创建订单并扣减库存，调用支付服务完成支付",
        },
        {
            "name": "database",
            "type": "database",
            "replicas": 1,
            "dependencies": [],
        },
    ],
}


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


_FAKE_CONFIG = {"api_key": "fake-key", "base_url": None, "model": "gpt-4o-mini"}


# ---------------------------------------------------------------------------
# 1. 无 LLM 配置时的测试
# ---------------------------------------------------------------------------

def test_review_design_no_llm_config():
    """无 LLM 配置时应返回空列表和提示消息。"""
    with patch("modules.design_reviewer.get_llm_config", return_value=None):
        issues, message = review_design(SAMPLE_DESIGN_SPEC)
    assert issues == []
    assert message == "未配置 LLM，跳过架构评估"


# ---------------------------------------------------------------------------
# 2. JSON 解析逻辑测试
# ---------------------------------------------------------------------------

def test_extract_json_plain_array():
    """纯 JSON 数组应能正确解析。"""
    text = json.dumps([
        {"type": "logic_issue", "severity": "high",
         "component": "x", "description": "y", "suggestion": "z"}
    ])
    result = _extract_json(text)
    assert len(result) == 1
    assert result[0]["type"] == "logic_issue"


def test_extract_json_code_block_with_lang():
    """ ```json 包裹的内容应能正确解析。"""
    text = """分析完成，以下是问题列表：
```json
[
  {"type": "anti_pattern", "severity": "low",
   "component": "a", "description": "b", "suggestion": "c"}
]
```
希望对你有帮助。
"""
    result = _extract_json(text)
    assert len(result) == 1
    assert result[0]["type"] == "anti_pattern"


def test_extract_json_code_block_without_lang():
    """``` 无语言标识的代码块也应能解析。"""
    text = "```\n[]\n```"
    result = _extract_json(text)
    assert result == []


def test_extract_json_empty_array():
    """空数组应能正确解析。"""
    assert _extract_json("[]") == []


def test_extract_json_invalid_raises():
    """无法解析的内容应抛出 ValueError。"""
    try:
        _extract_json("这完全不是 JSON 内容")
        assert False, "应抛出 ValueError"
    except ValueError:
        pass


def test_review_design_valid_json_response():
    """LLM 返回合法 JSON 时应正确解析为 issues，状态消息含问题数。"""
    fake_issues = [
        {
            "type": "missing_dependency",
            "severity": "high",
            "component": "order_service",
            "description": "描述提到调用支付服务，但拓扑中无支付服务依赖",
            "suggestion": "添加 payment_service 依赖",
        },
        {
            "type": "anti_pattern",
            "severity": "medium",
            "component": "order_service",
            "description": "重试 3 次但无熔断器",
            "suggestion": "添加熔断器",
        },
    ]
    fake_openai, _ = _make_fake_openai(json.dumps(fake_issues))

    with patch.dict("sys.modules", {"openai": fake_openai}), \
         patch("modules.design_reviewer.get_llm_config",
               return_value=_FAKE_CONFIG):
        issues, message = review_design(SAMPLE_DESIGN_SPEC)

    assert len(issues) == 2
    assert issues[0]["type"] == "missing_dependency"
    assert issues[1]["severity"] == "medium"
    assert "发现 2 个问题" in message


def test_review_design_code_blocked_json_response():
    """LLM 返回 ```json 包裹的内容时也应能正确解析。"""
    content = """```json
[
  {"type": "missing_component", "severity": "high",
   "component": "api_gateway", "description": "缺支付组件",
   "suggestion": "新增 payment_service"}
]
```"""
    fake_openai, _ = _make_fake_openai(content)

    with patch.dict("sys.modules", {"openai": fake_openai}), \
         patch("modules.design_reviewer.get_llm_config",
               return_value=_FAKE_CONFIG):
        issues, message = review_design(SAMPLE_DESIGN_SPEC)

    assert len(issues) == 1
    assert issues[0]["type"] == "missing_component"
    assert "发现 1 个问题" in message


def test_review_design_empty_issues_response():
    """LLM 返回空数组时应返回空列表和「发现 0 个问题」。"""
    fake_openai, _ = _make_fake_openai("[]")

    with patch.dict("sys.modules", {"openai": fake_openai}), \
         patch("modules.design_reviewer.get_llm_config",
               return_value=_FAKE_CONFIG):
        issues, message = review_design(SAMPLE_DESIGN_SPEC)

    assert issues == []
    assert "发现 0 个问题" in message


def test_review_design_invalid_json_response():
    """LLM 返回非 JSON 时应返回空列表和解析失败消息。"""
    fake_openai, _ = _make_fake_openai("这根本不是 JSON 格式的内容")

    with patch.dict("sys.modules", {"openai": fake_openai}), \
         patch("modules.design_reviewer.get_llm_config",
               return_value=_FAKE_CONFIG):
        issues, message = review_design(SAMPLE_DESIGN_SPEC)

    assert issues == []
    assert message == "LLM 返回格式无法解析"


def test_review_design_llm_call_failure():
    """LLM 调用抛异常时应返回空列表和调用失败消息。"""
    fake_client = MagicMock()
    fake_client.chat.completions.create.side_effect = RuntimeError("network error")
    fake_openai = MagicMock()
    fake_openai.OpenAI.return_value = fake_client

    with patch.dict("sys.modules", {"openai": fake_openai}), \
         patch("modules.design_reviewer.get_llm_config",
               return_value=_FAKE_CONFIG):
        issues, message = review_design(SAMPLE_DESIGN_SPEC)

    assert issues == []
    assert "LLM 调用失败" in message
    assert "network error" in message


# ---------------------------------------------------------------------------
# 3. review_prompt 内容测试
# ---------------------------------------------------------------------------

def test_review_prompt_contains_topology_info():
    """prompt 应包含完整拓扑 JSON（组件名、类型、参数、依赖关系）。"""
    prompt = build_review_prompt(SAMPLE_DESIGN_SPEC)
    # 组件名
    assert "api_gateway" in prompt
    assert "order_service" in prompt
    assert "database" in prompt
    # 类型
    assert "gateway" in prompt
    assert "service" in prompt
    # 参数字段
    assert "replicas" in prompt
    assert "retry_count" in prompt
    assert "dependencies" in prompt
    # 依赖关系
    assert "order_service" in prompt


def test_review_prompt_contains_descriptions():
    """prompt 应包含组件 description 文本（如果有）。"""
    prompt = build_review_prompt(SAMPLE_DESIGN_SPEC)
    # api_gateway 的描述
    assert "接收用户请求" in prompt
    # order_service 的描述
    assert "创建订单并扣减库存" in prompt
    # 有 description 时应包含「## 组件描述」章节标题
    assert "## 组件描述" in prompt


def test_review_prompt_contains_check_items():
    """prompt 应包含 5 类检查项及其 type 标识。"""
    prompt = build_review_prompt(SAMPLE_DESIGN_SPEC)
    assert "missing_dependency" in prompt
    assert "logic_issue" in prompt
    assert "missing_component" in prompt
    assert "anti_pattern" in prompt
    assert "missing_degradation" in prompt


def test_review_prompt_requires_json_output():
    """prompt 应明确要求 LLM 返回 JSON 格式。"""
    prompt = build_review_prompt(SAMPLE_DESIGN_SPEC)
    assert "JSON" in prompt or "json" in prompt


def test_review_prompt_no_descriptions_section_when_absent():
    """当组件没有 description 字段时，不应包含「## 组件描述」章节。

    注意：prompt 引言和检查项中会提到「组件描述」一词（属于说明性文字），
    所以这里只检查「## 组件描述」章节标题是否出现。
    """
    spec_no_desc = {
        "domain": "software_systems",
        "components": [
            {"name": "svc", "type": "service", "dependencies": []},
        ],
    }
    prompt = build_review_prompt(spec_no_desc)
    assert "## 组件描述" not in prompt
    # 拓扑信息仍然存在
    assert "svc" in prompt
