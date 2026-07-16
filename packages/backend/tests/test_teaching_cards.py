"""TeachingCardBank 教学卡片库测试
================================
验证 get_card、personalize_card、DomainPack.teaching_cards 加载行为。

测试策略：
  - get_card：验证查找命中/未命中、卡片结构完整性
  - personalize_card：mock get_llm_config 和 openai 模块，
    分别验证无 LLM 配置、LLM 个性化成功两条路径
  - DomainPack：验证 load_domain_pack 后 teaching_cards 非空

注意：teaching_cards.py 通过 `from modules.ai_tutor import get_llm_config`
将 get_llm_config 绑定到 modules.teaching_cards 命名空间，因此测试中
patch modules.teaching_cards.get_llm_config（与 design_reviewer 同模式）。
conftest.py 的 autouse fixture 仅 patch modules.ai_tutor.get_llm_config，
不影响本模块。
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from modules.domain_pack import load_domain_pack
from modules.teaching_cards import (
    _extract_json_object,
    get_card,
    personalize_card,
)


YAML_PATH = str(
    Path(__file__).resolve().parent.parent / "src" / "domain_packs" / "software_systems.yaml"
)


# ---------------------------------------------------------------------------
# 测试夹具：mock 上下文与 mock openai
# ---------------------------------------------------------------------------

SAMPLE_TEMPLATE = {
    "title": "重试风暴",
    "concept": "retry_semantics",
    "explanation": "重试放大效应：当 retry_count=N 时，单次请求在下游故障时最多产生 N+1 次调用。",
    "code_example": "retry_count=3 → 故障时负载放大 4 倍",
    "related_concepts": ["backpressure", "failure_isolation"],
}

SAMPLE_CONTEXT = {
    "component_name": "order_service",
    "component_params": {"retry_count": 3, "has_circuit_breaker": False},
    "failure_evidence": "order_service 在 tick 35 触发了 412 次重试",
}

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
# 1. get_card 测试
# ---------------------------------------------------------------------------

def test_get_card_found():
    """get_card("retry_storm") 应返回非 None 的 dict。"""
    card = get_card("retry_storm")
    assert card is not None
    assert isinstance(card, dict)
    # 返回的是副本，修改不应影响后续调用
    card["title"] = "modified"
    card2 = get_card("retry_storm")
    assert card2["title"] != "modified"


def test_get_card_not_found():
    """get_card("unknown_mode") 应返回 None。"""
    card = get_card("unknown_mode")
    assert card is None


def test_card_structure():
    """返回的 dict 应包含 title/concept/explanation/code_example/related_concepts。"""
    card = get_card("retry_storm")
    assert card is not None
    required_keys = {"title", "concept", "explanation", "code_example", "related_concepts"}
    assert required_keys.issubset(card.keys())
    # 字段类型校验
    assert isinstance(card["title"], str) and card["title"]
    assert isinstance(card["concept"], str) and card["concept"]
    assert isinstance(card["explanation"], str) and card["explanation"]
    assert isinstance(card["code_example"], str) and card["code_example"]
    assert isinstance(card["related_concepts"], list) and len(card["related_concepts"]) >= 1


def test_get_card_all_eight_modes():
    """所有 8 种失败模式都应有对应卡片。"""
    modes = [
        "retry_storm",
        "spof_cascade",
        "queue_overflow",
        "missing_circuit_breaker",
        "memory_exhaustion",
        "missing_rate_limiter",
        "consumer_lag_accumulation",
        "scheduled_task_thundering_herd",
    ]
    for mode in modes:
        card = get_card(mode)
        assert card is not None, f"缺少失败模式卡片: {mode}"
        assert "title" in card


# ---------------------------------------------------------------------------
# 2. personalize_card 测试
# ---------------------------------------------------------------------------

def test_personalize_no_llm():
    """mock get_llm_config 返回 None，personalize_card 应返回原模板。"""
    with patch("modules.teaching_cards.get_llm_config", return_value=None):
        result, message = personalize_card(SAMPLE_TEMPLATE, SAMPLE_CONTEXT)

    assert message == "未配置 LLM，使用标准模板"
    # 返回原模板（值相等）
    assert result == SAMPLE_TEMPLATE
    # explanation 未被修改
    assert result["explanation"] == SAMPLE_TEMPLATE["explanation"]
    # 其他字段保持不变
    assert result["code_example"] == SAMPLE_TEMPLATE["code_example"]
    assert result["related_concepts"] == SAMPLE_TEMPLATE["related_concepts"]


def test_personalize_with_llm():
    """mock LLM 返回个性化文本，验证 explanation 被更新，其他字段不变。"""
    personalized_explanation = (
        "order_service 配置了 retry_count=3 但未启用熔断器，当下游故障时"
        "单次请求会放大为 4 次调用，结合失败证据中 tick 35 的 412 次重试，"
        "可以看出重试风暴已经形成。"
    )
    llm_content = json.dumps({"explanation": personalized_explanation},
                             ensure_ascii=False)
    fake_openai, _ = _make_fake_openai(llm_content)

    with patch.dict("sys.modules", {"openai": fake_openai}), \
         patch("modules.teaching_cards.get_llm_config",
               return_value=_FAKE_CONFIG):
        result, message = personalize_card(SAMPLE_TEMPLATE, SAMPLE_CONTEXT)

    assert message == "LLM 个性化完成"
    # explanation 被更新为 LLM 输出
    assert result["explanation"] == personalized_explanation
    # 其他字段保持不变
    assert result["title"] == SAMPLE_TEMPLATE["title"]
    assert result["concept"] == SAMPLE_TEMPLATE["concept"]
    assert result["code_example"] == SAMPLE_TEMPLATE["code_example"]
    assert result["related_concepts"] == SAMPLE_TEMPLATE["related_concepts"]


def test_personalize_with_llm_code_block_response():
    """LLM 返回 ```json 包裹的内容时也应能正确解析。"""
    personalized_explanation = "针对 order_service（retry_count=3）的个性化解释。"
    llm_content = f"""```json
{{
  "explanation": "{personalized_explanation}"
}}
```"""
    fake_openai, _ = _make_fake_openai(llm_content)

    with patch.dict("sys.modules", {"openai": fake_openai}), \
         patch("modules.teaching_cards.get_llm_config",
               return_value=_FAKE_CONFIG):
        result, message = personalize_card(SAMPLE_TEMPLATE, SAMPLE_CONTEXT)

    assert message == "LLM 个性化完成"
    assert result["explanation"] == personalized_explanation


def test_personalize_llm_invalid_json_falls_back():
    """LLM 返回非 JSON 时应回退到原模板。"""
    fake_openai, _ = _make_fake_openai("这根本不是 JSON 格式的内容")

    with patch.dict("sys.modules", {"openai": fake_openai}), \
         patch("modules.teaching_cards.get_llm_config",
               return_value=_FAKE_CONFIG):
        result, message = personalize_card(SAMPLE_TEMPLATE, SAMPLE_CONTEXT)

    assert "使用标准模板" in message
    assert result["explanation"] == SAMPLE_TEMPLATE["explanation"]


# ---------------------------------------------------------------------------
# 3. _extract_json_object 单元测试
# ---------------------------------------------------------------------------

def test_extract_json_object_plain():
    """纯 JSON 对象应能正确解析。"""
    text = json.dumps({"explanation": "hello"}, ensure_ascii=False)
    result = _extract_json_object(text)
    assert result == {"explanation": "hello"}


def test_extract_json_object_code_block():
    """```json 包裹的内容应能正确解析。"""
    text = """前缀文字
```json
{"explanation": "world"}
```
后缀文字"""
    result = _extract_json_object(text)
    assert result == {"explanation": "world"}


def test_extract_json_object_invalid_raises():
    """无法解析的内容应抛出 ValueError。"""
    try:
        _extract_json_object("完全不是 JSON 的内容")
        assert False, "应抛出 ValueError"
    except ValueError:
        pass


# ---------------------------------------------------------------------------
# 4. DomainPack 加载 teaching_cards 测试
# ---------------------------------------------------------------------------

def test_domain_pack_loads_cards():
    """load_domain_pack 后 teaching_cards 应非空，包含 8 张卡片。"""
    pack = load_domain_pack(YAML_PATH)
    assert hasattr(pack, "teaching_cards")
    assert isinstance(pack.teaching_cards, dict)
    assert len(pack.teaching_cards) >= 8
    # 访问器方法
    assert pack.get_teaching_cards() is pack.teaching_cards
    # 关键卡片存在
    for mode in ("retry_storm", "consumer_lag_accumulation",
                 "scheduled_task_thundering_herd"):
        assert mode in pack.teaching_cards
        card = pack.teaching_cards[mode]
        assert "title" in card
        assert "explanation" in card


def test_get_card_uses_loaded_domain_pack():
    """显式传入 domain_pack 时 get_card 应使用该 pack 而非默认路径。"""
    pack = load_domain_pack(YAML_PATH)
    card = get_card("retry_storm", domain_pack=pack)
    assert card is not None
    assert card["title"] == pack.teaching_cards["retry_storm"]["title"]
