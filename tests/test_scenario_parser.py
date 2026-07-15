"""
Scenario Parser 模块测试
========================
验证 parse_scenario、build_parse_prompt、_extract_json 的行为。

测试策略：
  - 通过 mock modules.scenario_parser.get_llm_config 和 openai 模块模拟 LLM 调用
  - 直接测试 _extract_json 的各种边界情况
  - 验证 prompt 构建包含用户 NLP 输入

注意：conftest.py 的 autouse fixture 只 mock 了 modules.ai_tutor.get_llm_config，
不影响 modules.scenario_parser.get_llm_config（命名空间独立）。
但为测试隔离性，本测试仍显式 patch modules.scenario_parser.get_llm_config。
"""

import json
from unittest.mock import MagicMock, patch

from modules.scenario_parser import (
    _extract_json,
    build_parse_prompt,
    parse_scenario,
)
from utils.blackbox_spec import BlackBoxSpec


# ---------------------------------------------------------------------------
# 测试夹具与辅助
# ---------------------------------------------------------------------------

SAMPLE_NLP_INPUT = "电商订单系统，需要支持创建订单、查询订单状态，峰值 QPS 500，延迟不超过 100ms"

_FAKE_CONFIG = {"api_key": "fake-key", "base_url": None, "model": "gpt-4o-mini"}


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
# 1. 无 LLM 配置时的测试
# ---------------------------------------------------------------------------

def test_parse_no_llm():
    """mock get_llm_config 返回 None，应返回 (None, "请配置 LLM 以使用场景驱动模式")。"""
    with patch("modules.scenario_parser.get_llm_config", return_value=None):
        spec, message = parse_scenario(SAMPLE_NLP_INPUT)
    assert spec is None
    assert message == "请配置 LLM 以使用场景驱动模式"


# ---------------------------------------------------------------------------
# 2. 成功解析测试
# ---------------------------------------------------------------------------

def test_parse_success():
    """mock LLM 返回合法 JSON，应正确构建 BlackBoxSpec。"""
    fake_data = {
        "functional_points": ["创建订单", "查询订单状态"],
        "performance_bounds": {
            "max_qps": 500,
            "target_latency_ms": 100,
            "availability_target": "99.9%",
        },
        "exception_scenarios": [
            {
                "id": "ex001",
                "name": "支付服务超时",
                "description": "支付服务响应超过阈值",
                "trigger_condition": "支付服务响应超过 500ms",
            },
        ],
    }
    fake_openai, _ = _make_fake_openai(json.dumps(fake_data, ensure_ascii=False))

    with patch.dict("sys.modules", {"openai": fake_openai}), \
         patch("modules.scenario_parser.get_llm_config",
               return_value=_FAKE_CONFIG):
        spec, message = parse_scenario(SAMPLE_NLP_INPUT)

    assert message == "解析成功"
    assert isinstance(spec, BlackBoxSpec)
    # scenario_description 应为原始 NLP 输入
    assert spec.scenario_description == SAMPLE_NLP_INPUT
    # functional_points
    assert spec.functional_points == ["创建订单", "查询订单状态"]
    # performance_bounds
    assert spec.performance_bounds["max_qps"] == 500
    assert spec.performance_bounds["target_latency_ms"] == 100
    assert spec.performance_bounds["availability_target"] == "99.9%"
    # exception_scenarios
    assert len(spec.exception_scenarios) == 1
    assert spec.exception_scenarios[0]["id"] == "ex001"
    assert spec.exception_scenarios[0]["name"] == "支付服务超时"
    assert spec.exception_scenarios[0]["description"] == "支付服务响应超过阈值"
    assert spec.exception_scenarios[0]["trigger_condition"] == "支付服务响应超过 500ms"
    # design_variants 必须为空（parse_scenario 不识别变更点）
    assert spec.design_variants == []


# ---------------------------------------------------------------------------
# 3. ```json 包裹的 JSON 测试
# ---------------------------------------------------------------------------

def test_parse_json_in_codeblock():
    """mock LLM 返回 ```json 包裹的 JSON，应正确解析。"""
    fake_data = {
        "functional_points": ["创建订单"],
        "performance_bounds": {
            "max_qps": 1000,
            "target_latency_ms": 200,
            "availability_target": "99.9%",
        },
        "exception_scenarios": [],
    }
    content = "```json\n" + json.dumps(fake_data, ensure_ascii=False) + "\n```"
    fake_openai, _ = _make_fake_openai(content)

    with patch.dict("sys.modules", {"openai": fake_openai}), \
         patch("modules.scenario_parser.get_llm_config",
               return_value=_FAKE_CONFIG):
        spec, message = parse_scenario(SAMPLE_NLP_INPUT)

    assert message == "解析成功"
    assert isinstance(spec, BlackBoxSpec)
    assert spec.functional_points == ["创建订单"]
    assert spec.performance_bounds["max_qps"] == 1000
    assert spec.exception_scenarios == []


# ---------------------------------------------------------------------------
# 4. 字段缺失使用默认值测试
# ---------------------------------------------------------------------------

def test_parse_missing_fields():
    """LLM 返回的 JSON 缺少某些字段，应使用默认值不报错。"""
    # 只提供 functional_points，缺失 performance_bounds 和 exception_scenarios
    fake_data = {
        "functional_points": ["查询订单"],
    }
    fake_openai, _ = _make_fake_openai(json.dumps(fake_data, ensure_ascii=False))

    with patch.dict("sys.modules", {"openai": fake_openai}), \
         patch("modules.scenario_parser.get_llm_config",
               return_value=_FAKE_CONFIG):
        spec, message = parse_scenario(SAMPLE_NLP_INPUT)

    assert message == "解析成功"
    assert isinstance(spec, BlackBoxSpec)
    assert spec.functional_points == ["查询订单"]
    # 缺失字段使用默认值
    assert spec.performance_bounds == {}
    assert spec.exception_scenarios == []
    assert spec.design_variants == []


# ---------------------------------------------------------------------------
# 5. 非法 JSON 测试
# ---------------------------------------------------------------------------

def test_parse_invalid_json():
    """LLM 返回非法 JSON，应返回 (None, "LLM 返回格式无法解析")。"""
    fake_openai, _ = _make_fake_openai("这根本不是 JSON 格式的内容")

    with patch.dict("sys.modules", {"openai": fake_openai}), \
         patch("modules.scenario_parser.get_llm_config",
               return_value=_FAKE_CONFIG):
        spec, message = parse_scenario(SAMPLE_NLP_INPUT)

    assert spec is None
    assert message == "LLM 返回格式无法解析"


# ---------------------------------------------------------------------------
# 6. LLM 调用失败测试
# ---------------------------------------------------------------------------

def test_parse_llm_call_failed():
    """mock LLM 调用抛异常，应返回 (None, "LLM 调用失败: ...")。"""
    fake_client = MagicMock()
    fake_client.chat.completions.create.side_effect = RuntimeError("network error")
    fake_openai = MagicMock()
    fake_openai.OpenAI.return_value = fake_client

    with patch.dict("sys.modules", {"openai": fake_openai}), \
         patch("modules.scenario_parser.get_llm_config",
               return_value=_FAKE_CONFIG):
        spec, message = parse_scenario(SAMPLE_NLP_INPUT)

    assert spec is None
    assert "LLM 调用失败" in message
    assert "network error" in message


# ---------------------------------------------------------------------------
# 7. prompt 内容测试
# ---------------------------------------------------------------------------

def test_parse_prompt_contains_nlp():
    """验证 build_parse_prompt 包含用户 NLP 输入。"""
    nlp_input = "这是一个独特的场景描述：库存管理系统，需要支持入库、出库、盘点"
    prompt = build_parse_prompt(nlp_input)
    assert nlp_input in prompt
