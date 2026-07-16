"""
Parameter Advisor (LLM 参数建议)
================================
通过 LLM 解析组件 description，结合 BlackBoxSpec，向学生建议参数调整。

与 design_reviewer 的区别：
  design_reviewer 找「设计逻辑缺陷」（缺失组件、跨组件逻辑、降级策略）
  parameter_advisor 找「参数配置问题」（描述与参数不一致、参数与性能边界不匹配、反模式）

LLM 调用策略与 design_reviewer.py 一致：
  复用 get_llm_config() 读取配置（仅环境变量）。
  LLM 不可用或调用失败时返回空 suggestions 列表 + 提示消息。

配置来源：
  OPENAI_API_KEY — API 密钥（必需）
  OPENAI_BASE_URL — 自定义 API 端点（可选）
  OPENAI_MODEL / RECKONBOX_LLM_MODEL — 模型名（默认 deepseek-v4-flash）
"""

import sys
from dataclasses import asdict, is_dataclass

from utils.llm_config import get_llm_config
from modules.design_reviewer import _extract_json


# ---------------------------------------------------------------------------
# 内部辅助函数
# ---------------------------------------------------------------------------

def _component_to_dict(comp) -> dict:
    """将单个组件统一转为 dict（支持 dict 和 dataclass 实例）。

    design_spec 中的 components 可能是 dict 或 ComponentConfig，
    这里统一成 dict 便于在 prompt 中展示参数。
    """
    if isinstance(comp, dict):
        return dict(comp)
    if is_dataclass(comp) and not isinstance(comp, type):
        return asdict(comp)
    return {}


def _format_component_block(comp_dict: dict) -> str:
    """将单个组件 dict 格式化为 prompt 中的可读文本块。

    先列出 name/type，再列出所有参数值，最后单独高亮 description，
    便于 LLM 将描述与参数对照检查。
    """
    name = comp_dict.get("name", "")
    ctype = comp_dict.get("type", "")
    lines = [f"- {name} ({ctype}):"]

    # 列出所有参数（除 name/type/description 外）
    skip_keys = {"name", "type", "description"}
    for key, value in comp_dict.items():
        if key in skip_keys:
            continue
        lines.append(f"  - {key}: {value}")

    # 描述单独放在最后，便于 LLM 与上方参数对照
    desc = comp_dict.get("description")
    if desc:
        lines.append(f"  - description: {desc}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# prompt 构建
# ---------------------------------------------------------------------------

def build_advise_prompt(design_spec: dict, blackbox_spec: dict = None) -> str:
    """构建给 LLM 的参数建议提示词。

    prompt 包含：
      - 每个组件的 name、type、当前参数值、description 文本
      - 如果有 blackbox_spec，附加性能边界（max_qps、target_latency_ms）
      - 要求 LLM 检查的 3 类参数问题
      - 要求 LLM 返回 JSON 数组格式
    """
    components = design_spec.get("components", [])

    prompt = """你是一位资深系统架构师，正在审查学生提交的系统设计方案中的参数配置。
请基于组件描述和当前参数值，向学生建议参数调整。

## 组件配置
"""
    for comp in components:
        comp_dict = _component_to_dict(comp)
        prompt += _format_component_block(comp_dict) + "\n"

    # 如果有 blackbox_spec，附加性能边界上下文
    if blackbox_spec:
        perf_bounds = blackbox_spec.get("performance_bounds", {}) or {}
        if perf_bounds:
            prompt += "\n## 性能边界\n"
            if "max_qps" in perf_bounds:
                prompt += f"- max_qps: {perf_bounds['max_qps']}\n"
            if "target_latency_ms" in perf_bounds:
                prompt += f"- target_latency_ms: {perf_bounds['target_latency_ms']}\n"

    prompt += """
## 你需要检查以下 3 类问题

1. **描述与参数一致性**
   组件描述中提到的行为是否与当前参数值一致。
   例如描述说「重试 3 次」但 retry_count=0；描述说「双副本」但 replicas=1。

2. **参数与性能边界匹配**
   参数值是否与性能边界（max_qps、target_latency_ms）匹配。
   例如 max_qps=1000 但关键组件 replicas=1；target_latency_ms=100 但 timeout_ms=5000。

3. **参数配置反模式**
   是否有明显的参数配置反模式。
   例如 retry_count>0 但 has_circuit_breaker=False；has_rate_limiter=True 但 rate_limit=0。

## 输出格式

请返回严格的 JSON 数组，每个元素是一条参数调整建议，结构如下：

```json
[
  {
    "component": "组件名",
    "parameter": "参数名",
    "current_value": 当前值,
    "suggested_value": 建议值,
    "reason": "建议原因"
  }
]
```

要求：
- 只返回 JSON 数组，不要附加其他解释文字
- 如果没有需要调整的参数，返回空数组 `[]`
- current_value 和 suggested_value 应为与参数类型一致的值（数字、布尔等）
- reason 用中文简短说明建议原因
"""
    return prompt


# ---------------------------------------------------------------------------
# 主接口
# ---------------------------------------------------------------------------

def advise_parameters(
    design_spec: dict,
    blackbox_spec: dict = None
) -> tuple[list[dict], str]:
    """分析组件描述，建议参数调整。

    Args:
        design_spec: DesignSpec 的 dict 表示（含 components 列表，每个 component 有 description）
        blackbox_spec: BlackBoxSpec 的 dict 表示（可选，提供性能边界上下文）

    Returns:
        (suggestions 列表, 状态消息)
        每条 suggestion: {component, parameter, current_value, suggested_value, reason}
        LLM 不可用时返回 ([], "请配置 LLM")
    """
    config = get_llm_config()
    if config is None:
        return ([], "请配置 LLM")

    try:
        from openai import OpenAI
    except ImportError:
        return ([], "LLM 调用失败: openai 包未安装")

    prompt = build_advise_prompt(design_spec, blackbox_spec)

    try:
        client = OpenAI(
            api_key=config["api_key"],
            base_url=config.get("base_url"),
        )
        response = client.chat.completions.create(
            model=config.get("model", "deepseek-v4-flash"),
            messages=[{"role": "user", "content": prompt}],
        )
        content = response.choices[0].message.content
    except Exception as e:
        print(f"[ReckonBox] parameter advise LLM call failed ({e})",
              file=sys.stderr)
        return ([], f"LLM 调用失败: {e}")

    try:
        raw_suggestions = _extract_json(content)
    except ValueError:
        return ([], "LLM 返回格式无法解析")

    # 过滤掉字段缺失的建议
    required_fields = {
        "component", "parameter", "current_value", "suggested_value", "reason"
    }
    suggestions = []
    for s in raw_suggestions:
        if isinstance(s, dict) and required_fields.issubset(s.keys()):
            suggestions.append(s)

    return (suggestions, f"LLM 评估完成，发现 {len(suggestions)} 条参数建议")
