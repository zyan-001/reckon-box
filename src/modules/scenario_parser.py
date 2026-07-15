"""
Scenario Parser (NLP 场景解析)
==================================
通过 LLM 将用户的自然语言场景描述解析为 BlackBoxSpec。

与 design_reviewer.py 的关系：
  design_reviewer 分析「设计本身」→ 设计阶段的逻辑/安全/降级缺陷
  scenario_parser 分析「用户场景描述」→ 提取黑盒规格（功能点/性能边界/异常场景）

LLM 调用策略与 design_reviewer.py 一致：
  复用 get_llm_config() 读取配置（环境变量 > st.secrets）。
  LLM 不可用或调用失败时返回 (None, 错误消息)。

注意：parse_scenario 只解析黑盒规格，不识别变更点（那是 VariantIdentifier 的职责）。
所以返回的 BlackBoxSpec.design_variants 始终为空列表。
"""

import json
import re
import sys

from modules.ai_tutor import get_llm_config
from utils.blackbox_spec import BlackBoxSpec


# ---------------------------------------------------------------------------
# 内部辅助函数
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> dict:
    """从 LLM 返回的文本中提取 JSON 对象。

    处理以下情况：
      - 纯 JSON 对象
      - ```json ... ``` 包裹
      - ``` ... ``` 包裹（无语言标识）
      - JSON 前后包含解释文字

    返回 dict。无法提取时抛出 ValueError。
    """
    # 优先提取 ```json ... ``` 或 ``` ... ``` 代码块
    match = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.DOTALL)
    if match:
        text = match.group(1)

    text = text.strip()

    # 尝试直接解析
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    # 兜底：截取第一个 { 到最后一个 } 的子串
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            data = json.loads(text[start:end + 1])
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

    raise ValueError("无法从 LLM 返回内容中提取 JSON")


# ---------------------------------------------------------------------------
# prompt 构建
# ---------------------------------------------------------------------------

def build_parse_prompt(nlp_input: str) -> str:
    """构建给 LLM 的场景解析提示词。

    prompt 包含：
      - 用户原始 NLP 输入
      - 需要提取的 3 类字段（functional_points / performance_bounds / exception_scenarios）
      - 要求 LLM 返回严格 JSON 对象格式
      - 信息不完整时基于常见系统设计模式补全合理默认值
    """
    prompt = f"""你是一位资深系统架构师，正在将用户的自然语言场景描述解析为结构化的黑盒规格。

## 用户场景描述
{nlp_input}

## 你需要提取以下信息

1. **functional_points**: 功能点列表
   从场景描述中识别用户明确提到的或隐含的业务功能。
   形如 ["创建订单", "查询订单状态", "取消订单"]。

2. **performance_bounds**: 性能边界
   - max_qps: 最大 QPS（每秒请求数）
   - target_latency_ms: 目标延迟（毫秒）
   - availability_target: 可用性目标（如 99.9%）

3. **exception_scenarios**: 异常场景列表
   每个元素包含：
   - id: 唯一标识（如 "ex001"）
   - name: 异常名称（如 "支付服务超时"）
   - description: 异常描述
   - trigger_condition: 触发条件（如 "支付服务响应超过 500ms"）

## 信息补全规则

如果 NLP 输入信息不完整，请基于常见系统设计模式补全合理默认值：
- functional_points：根据场景领域补全典型功能点
- performance_bounds：
  - max_qps 默认 1000
  - target_latency_ms 默认 200
  - availability_target 默认 "99.9%"
- exception_scenarios：补全该类系统常见的异常场景（如依赖服务超时、流量突增、数据库故障等）

## 输出格式

请返回严格的 JSON 对象，结构如下：

```json
{{
  "functional_points": ["功能点1", "功能点2"],
  "performance_bounds": {{
    "max_qps": 1000,
    "target_latency_ms": 200,
    "availability_target": "99.9%"
  }},
  "exception_scenarios": [
    {{
      "id": "ex001",
      "name": "异常名称",
      "description": "异常描述",
      "trigger_condition": "触发条件"
    }}
  ]
}}
```

要求：
- 只返回 JSON 对象，不要附加其他解释文字
- functional_points 必须是字符串数组
- performance_bounds 必须是对象
- exception_scenarios 必须是数组，每个元素包含 id/name/description/trigger_condition
"""
    return prompt


# ---------------------------------------------------------------------------
# 主接口
# ---------------------------------------------------------------------------

def parse_scenario(nlp_input: str) -> tuple[BlackBoxSpec | None, str]:
    """将用户 NLP 输入解析为 BlackBoxSpec。

    Args:
        nlp_input: 用户自然语言场景描述

    Returns:
        (BlackBoxSpec, "解析成功") 或 (None, 错误消息)

    错误消息：
        - 未配置 LLM: "请配置 LLM 以使用场景驱动模式"
        - 调用失败: "LLM 调用失败: {error}"
        - 解析失败: "LLM 返回格式无法解析"
    """
    config = get_llm_config()
    if config is None:
        return (None, "请配置 LLM 以使用场景驱动模式")

    try:
        from openai import OpenAI
    except ImportError:
        return (None, "LLM 调用失败: openai 包未安装")

    prompt = build_parse_prompt(nlp_input)

    try:
        client = OpenAI(
            api_key=config["api_key"],
            base_url=config.get("base_url"),
        )
        response = client.chat.completions.create(
            model=config.get("model", "gpt-4o-mini"),
            messages=[{"role": "user", "content": prompt}],
        )
        content = response.choices[0].message.content
    except Exception as e:
        print(f"[ReckonBox] scenario parse LLM call failed ({e})",
              file=sys.stderr)
        return (None, f"LLM 调用失败: {e}")

    try:
        data = _extract_json(content)
    except ValueError:
        return (None, "LLM 返回格式无法解析")

    # 字段缺失时使用默认值
    spec = BlackBoxSpec(
        scenario_description=nlp_input,
        functional_points=data.get("functional_points", []),
        performance_bounds=data.get("performance_bounds", {}),
        exception_scenarios=data.get("exception_scenarios", []),
        design_variants=[],  # 变更点识别由 VariantIdentifier 负责
    )

    return (spec, "解析成功")
