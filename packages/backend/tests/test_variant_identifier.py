"""
Variant Identifier 模块测试
============================
验证 identify_variants、build_identify_prompt、_extract_json 的行为。

测试策略：
  - 通过 mock modules.variant_identifier.get_llm_config 和 openai 模块模拟 LLM 调用
  - 直接测试 _extract_json 的各种边界情况
  - 验证 prompt 构建包含 BlackBoxSpec 信息

注意：conftest.py 全局 mock 了 modules.ai_tutor.get_llm_config（返回 None），
但 variant_identifier.py 在 import 时已将 get_llm_config 绑定到自身命名空间，
因此测试中需要显式 patch modules.variant_identifier.get_llm_config。
"""

import json
from unittest.mock import MagicMock, patch

from modules.variant_identifier import (
    _extract_json,
    build_identify_prompt,
    identify_variants,
)
from utils.blackbox_spec import BlackBoxSpec


# ---------------------------------------------------------------------------
# 测试夹具：mock blackbox_spec
# ---------------------------------------------------------------------------

SAMPLE_BLACKBOX_SPEC = BlackBoxSpec(
    scenario_description="QPS 达 100w 的订单系统，查多于写，设计热库订单系统",
    functional_points=["创建订单", "查询订单状态", "取消订单"],
    performance_bounds={
        "max_qps": 1000000,
        "target_latency_ms": 50,
        "availability_target": "99.99%",
    },
    exception_scenarios=[
        {
            "id": "db_slowdown",
            "name": "数据库变慢",
            "description": "高峰期数据库响应延迟上升",
            "trigger_condition": "qps > 800000",
        },
    ],
)


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


# LLM 返回的示例变更点：1 个 parameter_mapping + 1 个 llm_context
_FAKE_VARIANTS = [
    {
        "id": "lock_contention",
        "name": "锁竞争场景",
        "description": "并发场景下数据库行锁/表锁的竞争程度",
        "options": [
            {"id": "low", "label": "低锁竞争", "description": "竞争少"},
            {"id": "high", "label": "高锁竞争", "description": "竞争激烈"},
        ],
        "impact_type": "parameter_mapping",
        "parameter_mappings": [
            {
                "option_id": "high",
                "component_type": "database",
                "param": "timeout_ms",
                "multiplier": 2,
                "offset": 0,
            }
        ],
    },
    {
        "id": "consistency_requirement",
        "name": "数据一致性要求",
        "description": "系统对数据一致性的要求程度",
        "options": [
            {"id": "strong", "label": "强一致性", "description": "读到最新写入"},
            {"id": "eventual", "label": "最终一致性", "description": "允许短暂不一致"},
        ],
        "impact_type": "llm_context",
        "parameter_mappings": [],
    },
]


# ---------------------------------------------------------------------------
# 1. 无 LLM 配置时的测试
# ---------------------------------------------------------------------------

def test_identify_no_llm():
    """无 LLM 配置时应返回空列表和提示消息。"""
    with patch("modules.variant_identifier.get_llm_config", return_value=None):
        variants, message = identify_variants(SAMPLE_BLACKBOX_SPEC)
    assert variants == []
    assert message == "请配置 LLM"


# ---------------------------------------------------------------------------
# 2. LLM 成功调用测试
# ---------------------------------------------------------------------------

def test_identify_success():
    """LLM 返回合法 JSON 数组时应正确解析为 DesignVariant 列表。"""
    fake_openai, _ = _make_fake_openai(json.dumps(_FAKE_VARIANTS))

    with patch.dict("sys.modules", {"openai": fake_openai}), \
         patch("modules.variant_identifier.get_llm_config",
               return_value=_FAKE_CONFIG):
        variants, message = identify_variants(SAMPLE_BLACKBOX_SPEC)

    assert len(variants) == 2
    # 第一个：parameter_mapping
    v0 = variants[0]
    assert v0.id == "lock_contention"
    assert v0.name == "锁竞争场景"
    assert v0.impact_type == "parameter_mapping"
    assert len(v0.options) == 2
    assert v0.options[0]["id"] == "low"
    # 第二个：llm_context
    v1 = variants[1]
    assert v1.id == "consistency_requirement"
    assert v1.impact_type == "llm_context"
    assert len(v1.options) == 2
    # 状态消息包含变更点数量
    assert "2" in message


def test_identify_parameter_mapping():
    """impact_type=parameter_mapping 的 variant 应包含 parameter_mappings。"""
    fake_openai, _ = _make_fake_openai(json.dumps(_FAKE_VARIANTS))

    with patch.dict("sys.modules", {"openai": fake_openai}), \
         patch("modules.variant_identifier.get_llm_config",
               return_value=_FAKE_CONFIG):
        variants, _ = identify_variants(SAMPLE_BLACKBOX_SPEC)

    pm_variants = [v for v in variants if v.impact_type == "parameter_mapping"]
    assert len(pm_variants) == 1
    pm = pm_variants[0]
    assert len(pm.parameter_mappings) == 1
    mapping = pm.parameter_mappings[0]
    assert mapping["option_id"] == "high"
    assert mapping["component_type"] == "database"
    assert mapping["param"] == "timeout_ms"
    assert mapping["multiplier"] == 2
    assert mapping["offset"] == 0


def test_identify_llm_context():
    """impact_type=llm_context 的 variant 应无 parameter_mappings。"""
    fake_openai, _ = _make_fake_openai(json.dumps(_FAKE_VARIANTS))

    with patch.dict("sys.modules", {"openai": fake_openai}), \
         patch("modules.variant_identifier.get_llm_config",
               return_value=_FAKE_CONFIG):
        variants, _ = identify_variants(SAMPLE_BLACKBOX_SPEC)

    ctx_variants = [v for v in variants if v.impact_type == "llm_context"]
    assert len(ctx_variants) == 1
    ctx = ctx_variants[0]
    assert ctx.parameter_mappings == []


# ---------------------------------------------------------------------------
# 3. JSON 解析失败的测试
# ---------------------------------------------------------------------------

def test_identify_invalid_json():
    """LLM 返回非 JSON 时应返回空列表和解析失败消息。"""
    fake_openai, _ = _make_fake_openai("这根本不是 JSON 格式的内容")

    with patch.dict("sys.modules", {"openai": fake_openai}), \
         patch("modules.variant_identifier.get_llm_config",
               return_value=_FAKE_CONFIG):
        variants, message = identify_variants(SAMPLE_BLACKBOX_SPEC)

    assert variants == []
    assert message == "LLM 返回格式无法解析"


# ---------------------------------------------------------------------------
# 4. LLM 调用失败的测试
# ---------------------------------------------------------------------------

def test_identify_llm_call_failed():
    """LLM 调用抛异常时应返回空列表和调用失败消息。"""
    fake_client = MagicMock()
    fake_client.chat.completions.create.side_effect = RuntimeError("network error")
    fake_openai = MagicMock()
    fake_openai.OpenAI.return_value = fake_client

    with patch.dict("sys.modules", {"openai": fake_openai}), \
         patch("modules.variant_identifier.get_llm_config",
               return_value=_FAKE_CONFIG):
        variants, message = identify_variants(SAMPLE_BLACKBOX_SPEC)

    assert variants == []
    assert "LLM 调用失败" in message
    assert "network error" in message


# ---------------------------------------------------------------------------
# 5. prompt 内容测试
# ---------------------------------------------------------------------------

def test_identify_prompt_contains_spec():
    """prompt 应包含 BlackBoxSpec 信息（功能点、性能边界、异常场景、场景描述）。"""
    prompt = build_identify_prompt(SAMPLE_BLACKBOX_SPEC)

    # 场景描述
    assert "QPS 达 100w 的订单系统" in prompt
    # 功能点
    assert "创建订单" in prompt
    assert "查询订单状态" in prompt
    assert "取消订单" in prompt
    # 性能边界
    assert "1000000" in prompt  # max_qps
    assert "target_latency_ms" in prompt
    assert "availability_target" in prompt
    # 异常场景
    assert "db_slowdown" in prompt
    assert "数据库变慢" in prompt
    # impact_type 说明
    assert "parameter_mapping" in prompt
    assert "llm_context" in prompt
    # 要求 JSON 输出
    assert "JSON" in prompt or "json" in prompt
