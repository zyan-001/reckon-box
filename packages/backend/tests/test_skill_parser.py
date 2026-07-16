"""
SkillParser 模块测试
====================
验证 parse_skill、build_parse_prompt、_extract_json 的行为。

测试策略：
  - 通过 mock modules.skill_parser.get_llm_config 和 openai 模块模拟 LLM 调用
  - 直接测试 _extract_json 的各种边界情况
  - 验证 prompt 构建包含 NL 描述、模板列表、JSON 输出格式要求

注意：conftest.py 的 autouse fixture 会 mock modules.ai_tutor.get_llm_config 返回 None，
但 skill_parser 导入的是 modules.ai_tutor.get_llm_config，所以需要显式 patch
modules.skill_parser.get_llm_config 来覆盖 conftest 的全局 mock。
"""

import json
from unittest.mock import MagicMock, patch

from modules.skill_parser import (
    _extract_json,
    build_parse_prompt,
    parse_skill,
)
from utils.micro_skill import MicroSkill, StepNode
from utils.strategy_templates import default_params, get_template


# ---------------------------------------------------------------------------
# 测试夹具与辅助
# ---------------------------------------------------------------------------

SAMPLE_NL = "电商订单服务，同步调用支付接口，超时 1 秒，每副本容量 50"

_FAKE_CONFIG = {
    "api_key": "fake-key",
    "base_url": "https://llm-5xhy5uqkd3ej0due.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
    "model": "deepseek-v4-flash",
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


# ---------------------------------------------------------------------------
# 1. build_parse_prompt 测试
# ---------------------------------------------------------------------------

def test_build_parse_prompt_contains_nl():
    """prompt 应包含用户 NL 描述。"""
    prompt = build_parse_prompt("这是一个独特的服务描述")
    assert "这是一个独特的服务描述" in prompt


def test_build_parse_prompt_contains_template_list():
    """prompt 应包含可用的策略模板列表。"""
    prompt = build_parse_prompt("测试描述")
    # 验证包含所有 6 个模板 ID
    assert "sync_direct" in prompt
    assert "retry_no_backoff" in prompt
    assert "retry_with_backoff" in prompt
    assert "async_fire_and_forget" in prompt
    assert "degrade_fallback" in prompt
    assert "circuit_break" in prompt


def test_build_parse_prompt_contains_json_format():
    """prompt 应包含 JSON 输出格式要求。"""
    prompt = build_parse_prompt("测试描述")
    assert "strategy_template_id" in prompt
    assert "params" in prompt


def test_build_parse_prompt_with_step_tree():
    """带步骤树时，prompt 应包含步骤树结构。"""
    step_tree = StepNode(
        id="s1",
        label="创建订单",
        description="用户提交订单信息",
        children=[
            StepNode(id="s1_1", label="验证库存", description="检查商品库存是否充足"),
        ],
    )
    prompt = build_parse_prompt("测试描述", step_tree=step_tree)
    assert "步骤树结构" in prompt
    assert "创建订单" in prompt
    assert "验证库存" in prompt


def test_build_parse_prompt_without_step_tree():
    """不带步骤树时，prompt 不应包含步骤树结构部分。"""
    prompt = build_parse_prompt("测试描述", step_tree=None)
    assert "步骤树结构" not in prompt


# ---------------------------------------------------------------------------
# 2. parse_skill LLM 不可用测试
# ---------------------------------------------------------------------------

def test_parse_skill_no_llm():
    """get_llm_config 返回 None → 返回 sync_direct 默认 MicroSkill。"""
    with patch("modules.skill_parser.get_llm_config", return_value=None):
        skill, message = parse_skill(SAMPLE_NL)

    assert isinstance(skill, MicroSkill)
    assert skill.strategy_template_id == "sync_direct"
    assert skill.params == default_params("sync_direct")
    assert "LLM 不可用" in message


# ---------------------------------------------------------------------------
# 3. parse_skill LLM 正常解析 sync_direct
# ---------------------------------------------------------------------------

def test_parse_skill_sync_direct():
    """LLM 返回 sync_direct 模板和合法参数 → 验证返回 MicroSkill。"""
    fake_data = {
        "strategy_template_id": "sync_direct",
        "params": {"timeout_ms": 1000, "capacity_per_replica": 50},
    }
    fake_openai, _ = _make_fake_openai(json.dumps(fake_data, ensure_ascii=False))

    with patch.dict("sys.modules", {"openai": fake_openai}), \
         patch("modules.skill_parser.get_llm_config",
               return_value=_FAKE_CONFIG):
        skill, message = parse_skill(SAMPLE_NL)

    assert message == "解析成功"
    assert isinstance(skill, MicroSkill)
    assert skill.strategy_template_id == "sync_direct"
    assert skill.params["timeout_ms"] == 1000
    assert skill.params["capacity_per_replica"] == 50
    # 验证 assertions 来自模板
    template = get_template("sync_direct")
    assert skill.assertions == template.assertions


# ---------------------------------------------------------------------------
# 4. parse_skill LLM 正常解析 retry_no_backoff
# ---------------------------------------------------------------------------

def test_parse_skill_retry_no_backoff():
    """LLM 返回 retry_no_backoff 模板 → 验证 assertions 包含 retry_storm。"""
    fake_data = {
        "strategy_template_id": "retry_no_backoff",
        "params": {"max_retries": 3, "capacity_per_replica": 50},
    }
    fake_openai, _ = _make_fake_openai(json.dumps(fake_data, ensure_ascii=False))

    with patch.dict("sys.modules", {"openai": fake_openai}), \
         patch("modules.skill_parser.get_llm_config",
               return_value=_FAKE_CONFIG):
        skill, message = parse_skill(SAMPLE_NL)

    assert message == "解析成功"
    assert isinstance(skill, MicroSkill)
    assert skill.strategy_template_id == "retry_no_backoff"
    assert skill.params["max_retries"] == 3
    # retry_no_backoff 的 assertions 包含 retry_storm
    assert any(
        a.get("crash_risk") == "retry_storm" or a.get("condition") == "retries_exhausted"
        for a in skill.assertions
    )


# ---------------------------------------------------------------------------
# 5. parse_skill LLM 返回未注册模板
# ---------------------------------------------------------------------------

def test_parse_skill_unknown_template():
    """LLM 返回未注册模板 ID → 回退到 sync_direct。"""
    fake_data = {
        "strategy_template_id": "unknown_template",
        "params": {},
    }
    fake_openai, _ = _make_fake_openai(json.dumps(fake_data, ensure_ascii=False))

    with patch.dict("sys.modules", {"openai": fake_openai}), \
         patch("modules.skill_parser.get_llm_config",
               return_value=_FAKE_CONFIG):
        skill, message = parse_skill(SAMPLE_NL)

    assert message == "解析成功"
    assert isinstance(skill, MicroSkill)
    # 应回退到 sync_direct
    assert skill.strategy_template_id == "sync_direct"
    assert skill.params == default_params("sync_direct")


# ---------------------------------------------------------------------------
# 6. parse_skill LLM 返回非法 params
# ---------------------------------------------------------------------------

def test_parse_skill_invalid_params():
    """LLM 返回合法模板 ID 但 params 不合法 → 使用 default_params 替代。"""
    # sync_direct 的 params_schema 要求 timeout_ms: int, capacity_per_replica: int
    # 传入非法类型
    fake_data = {
        "strategy_template_id": "sync_direct",
        "params": {"timeout_ms": "not_a_number", "capacity_per_replica": -1},
    }
    fake_openai, _ = _make_fake_openai(json.dumps(fake_data, ensure_ascii=False))

    with patch.dict("sys.modules", {"openai": fake_openai}), \
         patch("modules.skill_parser.get_llm_config",
               return_value=_FAKE_CONFIG):
        skill, message = parse_skill(SAMPLE_NL)

    assert message == "解析成功"
    assert isinstance(skill, MicroSkill)
    assert skill.strategy_template_id == "sync_direct"
    # params 应被替换为 default_params
    assert skill.params == default_params("sync_direct")


# ---------------------------------------------------------------------------
# 7. parse_skill LLM 调用失败
# ---------------------------------------------------------------------------

def test_parse_skill_llm_call_failed():
    """模拟 OpenAI 抛异常 → 返回 (None, 错误消息)。"""
    fake_client = MagicMock()
    fake_client.chat.completions.create.side_effect = RuntimeError("network error")
    fake_openai = MagicMock()
    fake_openai.OpenAI.return_value = fake_client

    with patch.dict("sys.modules", {"openai": fake_openai}), \
         patch("modules.skill_parser.get_llm_config",
               return_value=_FAKE_CONFIG):
        skill, message = parse_skill(SAMPLE_NL)

    assert skill is None
    assert "LLM 调用失败" in message
    assert "network error" in message


# ---------------------------------------------------------------------------
# 8. parse_skill LLM 返回格式无法解析
# ---------------------------------------------------------------------------

def test_parse_skill_invalid_json():
    """LLM 返回非 JSON → 返回 (None, "LLM 返回格式无法解析")。"""
    fake_openai, _ = _make_fake_openai("这根本不是 JSON 格式的内容")

    with patch.dict("sys.modules", {"openai": fake_openai}), \
         patch("modules.skill_parser.get_llm_config",
               return_value=_FAKE_CONFIG):
        skill, message = parse_skill(SAMPLE_NL)

    assert skill is None
    assert message == "LLM 返回格式无法解析"


# ---------------------------------------------------------------------------
# 9. parse_skill 带步骤树
# ---------------------------------------------------------------------------

def test_parse_skill_with_step_tree():
    """带步骤树时，验证步骤树被传入 prompt 且保留在 MicroSkill 中。"""
    step_tree = StepNode(
        id="s1",
        label="创建订单",
        description="用户提交订单信息",
    )
    fake_data = {
        "strategy_template_id": "sync_direct",
        "params": {"timeout_ms": 500, "capacity_per_replica": 30},
    }
    fake_openai, _ = _make_fake_openai(json.dumps(fake_data, ensure_ascii=False))

    with patch.dict("sys.modules", {"openai": fake_openai}), \
         patch("modules.skill_parser.get_llm_config",
               return_value=_FAKE_CONFIG):
        skill, message = parse_skill(SAMPLE_NL, step_tree=step_tree)

    assert message == "解析成功"
    assert isinstance(skill, MicroSkill)
    # 验证步骤树被保留
    assert skill.step_tree is not None
    assert skill.step_tree.id == "s1"
    assert skill.step_tree.label == "创建订单"
