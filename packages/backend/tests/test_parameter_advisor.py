"""
Parameter Advisor 模块测试
========================
验证 advise_parameters、build_advise_prompt 的行为。

测试策略：
  - 通过 mock get_llm_config 和 openai 模块模拟 LLM 调用
  - 验证 prompt 构建包含组件 description 和性能边界
  - 验证字段缺失的建议被过滤

注意：
  conftest.py mock 了 modules.ai_tutor.get_llm_config（全局），
  但 parameter_advisor 在自身命名空间导入了 get_llm_config，
  所以这里 patch modules.parameter_advisor.get_llm_config。
"""

import json
from unittest.mock import MagicMock, patch

from modules.parameter_advisor import (
    advise_parameters,
    build_advise_prompt,
)


# ---------------------------------------------------------------------------
# 测试夹具：mock design_spec / blackbox_spec
# ---------------------------------------------------------------------------

# 设计中故意包含参数与描述不一致的情况，便于 LLM 发现问题
SAMPLE_DESIGN_SPEC = {
    "domain": "software_systems",
    "components": [
        {
            "name": "api_gateway",
            "type": "gateway",
            "replicas": 2,
            "retry_count": 0,
            "has_circuit_breaker": False,
            "queue_capacity": 200,
            "timeout_ms": 1000,
            "dependencies": ["order_service"],
            "description": "接收用户请求，转发到 order_service，本身不重试",
        },
        {
            "name": "order_service",
            "type": "service",
            "replicas": 1,
            "retry_count": 3,
            "has_circuit_breaker": False,
            "queue_capacity": 100,
            "timeout_ms": 2000,
            "dependencies": ["database"],
            "description": "创建订单并扣减库存，失败时重试 3 次",
        },
        {
            "name": "database",
            "type": "database",
            "replicas": 1,
            "retry_count": 0,
            "has_circuit_breaker": False,
            "dependencies": [],
            "description": "主数据库，单副本",
        },
    ],
}

SAMPLE_BLACKBOX_SPEC = {
    "scenario_description": "电商秒杀场景",
    "functional_points": ["创建订单", "查询订单"],
    "performance_bounds": {
        "max_qps": 1000,
        "target_latency_ms": 200,
        "availability_target": "99.9%",
    },
    "exception_scenarios": [],
    "design_variants": [],
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


_FAKE_CONFIG = {"api_key": "fake-key", "base_url": "https://llm-5xhy5uqkd3ej0due.cn-beijing.maas.aliyuncs.com/compatible-mode/v1", "model": "deepseek-v4-flash"}


# ---------------------------------------------------------------------------
# 1. 无 LLM 配置时的测试
# ---------------------------------------------------------------------------

def test_advise_no_llm():
    """无 LLM 配置时应返回空列表和「请配置 LLM」消息。"""
    with patch("modules.parameter_advisor.get_llm_config", return_value=None):
        suggestions, message = advise_parameters(SAMPLE_DESIGN_SPEC)
    assert suggestions == []
    assert message == "请配置 LLM"


# ---------------------------------------------------------------------------
# 2. LLM 成功调用测试
# ---------------------------------------------------------------------------

def test_advise_success():
    """LLM 返回合法 JSON 数组时应正确解析为 suggestions。"""
    fake_suggestions = [
        {
            "component": "order_service",
            "parameter": "has_circuit_breaker",
            "current_value": False,
            "suggested_value": True,
            "reason": "retry_count=3 但无熔断器，重试风暴风险",
        },
        {
            "component": "api_gateway",
            "parameter": "replicas",
            "current_value": 2,
            "suggested_value": 3,
            "reason": "max_qps=1000 但 gateway 仅 2 副本可能成为瓶颈",
        },
    ]
    fake_openai, _ = _make_fake_openai(json.dumps(fake_suggestions))

    with patch.dict("sys.modules", {"openai": fake_openai}), \
         patch("modules.parameter_advisor.get_llm_config",
               return_value=_FAKE_CONFIG):
        suggestions, message = advise_parameters(SAMPLE_DESIGN_SPEC)

    assert len(suggestions) == 2
    assert suggestions[0]["component"] == "order_service"
    assert suggestions[0]["parameter"] == "has_circuit_breaker"
    assert suggestions[0]["current_value"] is False
    assert suggestions[0]["suggested_value"] is True
    assert "重试风暴" in suggestions[0]["reason"]
    assert suggestions[1]["component"] == "api_gateway"
    assert "发现 2 条参数建议" in message


def test_advise_filters_incomplete_suggestions():
    """LLM 返回的建议中字段缺失的条目应被过滤掉。"""
    raw = [
        {
            "component": "order_service",
            "parameter": "retry_count",
            "current_value": 3,
            "suggested_value": 1,
            "reason": "降低重试次数",
        },
        # 缺少 reason 字段
        {
            "component": "api_gateway",
            "parameter": "replicas",
            "current_value": 2,
            "suggested_value": 3,
        },
        # 缺少 parameter 字段
        {
            "component": "database",
            "current_value": 1,
            "suggested_value": 2,
            "reason": "增加副本",
        },
    ]
    fake_openai, _ = _make_fake_openai(json.dumps(raw))

    with patch.dict("sys.modules", {"openai": fake_openai}), \
         patch("modules.parameter_advisor.get_llm_config",
               return_value=_FAKE_CONFIG):
        suggestions, message = advise_parameters(SAMPLE_DESIGN_SPEC)

    assert len(suggestions) == 1
    assert suggestions[0]["component"] == "order_service"
    assert "发现 1 条参数建议" in message


# ---------------------------------------------------------------------------
# 3. BlackBoxSpec 测试
# ---------------------------------------------------------------------------

def test_advise_with_blackbox():
    """传入 blackbox_spec 时，prompt 应包含性能边界（max_qps、target_latency_ms）。"""
    prompt = build_advise_prompt(SAMPLE_DESIGN_SPEC, SAMPLE_BLACKBOX_SPEC)
    # 性能边界字段应出现在 prompt 中
    assert "max_qps" in prompt
    assert "1000" in prompt
    assert "target_latency_ms" in prompt
    assert "200" in prompt
    # 性能边界章节标题
    assert "## 性能边界" in prompt


def test_advise_without_blackbox():
    """不传 blackbox_spec 时，prompt 不应包含性能边界章节。

    注意：检查项说明文字中会提到「性能边界」一词（属于说明性文字），
    所以这里只检查「## 性能边界」章节标题是否出现。
    """
    prompt = build_advise_prompt(SAMPLE_DESIGN_SPEC, None)
    assert "## 性能边界" not in prompt


# ---------------------------------------------------------------------------
# 4. 空 components 测试
# ---------------------------------------------------------------------------

def test_advise_empty_components():
    """空 components 列表时，LLM 仍被调用但返回空建议。"""
    empty_spec = {"domain": "software_systems", "components": []}
    fake_openai, client = _make_fake_openai("[]")

    with patch.dict("sys.modules", {"openai": fake_openai}), \
         patch("modules.parameter_advisor.get_llm_config",
               return_value=_FAKE_CONFIG):
        suggestions, message = advise_parameters(empty_spec)

    # LLM 被实际调用
    assert client.chat.completions.create.called
    # 返回空建议
    assert suggestions == []
    assert "发现 0 条参数建议" in message


# ---------------------------------------------------------------------------
# 5. JSON 解析失败测试
# ---------------------------------------------------------------------------

def test_advise_invalid_json():
    """LLM 返回非 JSON 时应返回空列表和解析失败消息。"""
    fake_openai, _ = _make_fake_openai("这根本不是 JSON 格式的内容")

    with patch.dict("sys.modules", {"openai": fake_openai}), \
         patch("modules.parameter_advisor.get_llm_config",
               return_value=_FAKE_CONFIG):
        suggestions, message = advise_parameters(SAMPLE_DESIGN_SPEC)

    assert suggestions == []
    assert message == "LLM 返回格式无法解析"


def test_advise_code_blocked_json():
    """LLM 返回 ```json 包裹的内容时也应能正确解析。"""
    content = """```json
[
  {
    "component": "order_service",
    "parameter": "has_circuit_breaker",
    "current_value": false,
    "suggested_value": true,
    "reason": "有重试无熔断"
  }
]
```"""
    fake_openai, _ = _make_fake_openai(content)

    with patch.dict("sys.modules", {"openai": fake_openai}), \
         patch("modules.parameter_advisor.get_llm_config",
               return_value=_FAKE_CONFIG):
        suggestions, message = advise_parameters(SAMPLE_DESIGN_SPEC)

    assert len(suggestions) == 1
    assert suggestions[0]["component"] == "order_service"
    assert "发现 1 条参数建议" in message


# ---------------------------------------------------------------------------
# 6. LLM 调用失败测试
# ---------------------------------------------------------------------------

def test_advise_llm_call_failed():
    """LLM 调用抛异常时应返回空列表和调用失败消息。"""
    fake_client = MagicMock()
    fake_client.chat.completions.create.side_effect = RuntimeError("network error")
    fake_openai = MagicMock()
    fake_openai.OpenAI.return_value = fake_client

    with patch.dict("sys.modules", {"openai": fake_openai}), \
         patch("modules.parameter_advisor.get_llm_config",
               return_value=_FAKE_CONFIG):
        suggestions, message = advise_parameters(SAMPLE_DESIGN_SPEC)

    assert suggestions == []
    assert "LLM 调用失败" in message
    assert "network error" in message


# ---------------------------------------------------------------------------
# 7. prompt 内容测试
# ---------------------------------------------------------------------------

def test_advise_prompt_contains_description():
    """prompt 应包含每个组件的 description 文本。"""
    prompt = build_advise_prompt(SAMPLE_DESIGN_SPEC)
    # api_gateway 的描述
    assert "接收用户请求" in prompt
    # order_service 的描述
    assert "创建订单并扣减库存" in prompt
    assert "失败时重试 3 次" in prompt
    # database 的描述
    assert "主数据库" in prompt
    # description 字段标签
    assert "description" in prompt


def test_advise_prompt_contains_component_params():
    """prompt 应包含组件的当前参数值（replicas、retry_count 等）。"""
    prompt = build_advise_prompt(SAMPLE_DESIGN_SPEC)
    # 参数字段名
    assert "replicas" in prompt
    assert "retry_count" in prompt
    assert "has_circuit_breaker" in prompt
    assert "queue_capacity" in prompt
    assert "timeout_ms" in prompt
    # 具体参数值
    assert "order_service" in prompt
    assert "api_gateway" in prompt
    assert "database" in prompt


def test_advise_prompt_contains_check_items():
    """prompt 应包含 3 类检查项说明。"""
    prompt = build_advise_prompt(SAMPLE_DESIGN_SPEC)
    assert "描述与参数一致性" in prompt
    assert "参数与性能边界匹配" in prompt
    assert "参数配置反模式" in prompt


def test_advise_prompt_requires_json_output():
    """prompt 应明确要求 LLM 返回 JSON 数组格式。"""
    prompt = build_advise_prompt(SAMPLE_DESIGN_SPEC)
    assert "JSON" in prompt or "json" in prompt
    # 输出字段说明
    assert "component" in prompt
    assert "parameter" in prompt
    assert "current_value" in prompt
    assert "suggested_value" in prompt
    assert "reason" in prompt
