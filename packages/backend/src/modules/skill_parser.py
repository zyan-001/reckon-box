"""
SkillParser (NL + 步骤树 → MicroSkill)
========================================
通过 LLM 将自然语言描述和步骤树解析为 MicroSkill。

LLM 仅负责选择策略模板和填充参数，不生成性能公式。

流程：
  1. 获取 LLM 配置（get_llm_config）
  2. LLM 不可用 → 降级到 default_skill("sync_direct")
  3. 构建 prompt，调用 LLM
  4. 提取 JSON 响应 {"strategy_template_id": "...", "params": {...}}
  5. 校验 strategy_template_id 是否在注册表中
     - 不存在 → 回退到 sync_direct，记录警告
  6. 校验 params
     - 校验失败 → 使用 default_params 替代
  7. 获取模板的 assertions
  8. 构建 MicroSkill 并返回
"""

import json
import re
import sys

from utils.llm_config import get_llm_config
from utils.micro_skill import MicroSkill, StepNode, default_skill
from utils.strategy_templates import (
    default_params,
    get_all_templates,
    get_template,
    validate_params,
)


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

def build_parse_prompt(nl_description: str, step_tree: StepNode | None = None) -> str:
    """构建 LLM 提示词。

    prompt 包含：
    - 用户自然语言描述
    - 步骤树结构（如果提供）
    - 可用的策略模板列表（ID + description + params_schema）
    - 要求 LLM 返回 JSON：{"strategy_template_id": "...", "params": {...}}
    """
    # 构建模板列表描述
    all_templates = get_all_templates()
    template_lines = []
    for tid, tpl in all_templates.items():
        params_desc = ", ".join(
            f"{name}: {typ.__name__}" for name, typ in tpl.params_schema.items()
        )
        template_lines.append(f"- **{tid}**: {tpl.description}（参数: {params_desc}）")

    templates_text = "\n".join(template_lines)

    # 构建步骤树部分
    step_tree_text = ""
    if step_tree is not None:
        step_tree_text = f"""
## 步骤树结构

用户提供了一个操作步骤树，请结合步骤的层次结构来判断最适合的策略模板：

```
{_format_step_tree(step_tree)}
```
"""

    prompt = f"""你是一位资深系统架构师，正在根据用户的自然语言描述选择最合适的容错/调用策略模板，并填充参数。

## 用户描述
{nl_description}
{step_tree_text}
## 可用的策略模板

{templates_text}

## 你的任务

1. 从上面的策略模板列表中选择最适合用户描述的模板
2. 根据用户描述中的具体数值或语义填充模板参数
3. 如果用户没有提供具体数值，使用合理的默认值

## 输出格式

请返回严格的 JSON 对象，结构如下：

```json
{{
  "strategy_template_id": "模板ID",
  "params": {{
    "参数名1": 值1,
    "参数名2": 值2
  }}
}}
```

要求：
- strategy_template_id 必须是上面列出的模板 ID 之一
- params 必须符合所选模板的参数模式
- 只返回 JSON 对象，不要附加其他解释文字
"""
    return prompt


def _format_step_tree(node: StepNode, indent: int = 0) -> str:
    """递归格式化步骤树为缩进文本。"""
    prefix = "  " * indent
    lines = [f"{prefix}- [{node.id}] {node.label}: {node.description}"]
    for child in node.children:
        lines.append(_format_step_tree(child, indent + 1))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 主接口
# ---------------------------------------------------------------------------

def parse_skill(nl_description: str, step_tree: StepNode | None = None) -> tuple[MicroSkill | None, str]:
    """将 NL + 步骤树解析为 MicroSkill。

    返回 (MicroSkill, "解析成功") 或 (None, 错误消息)

    流程：
    1. 获取 LLM 配置（get_llm_config）
    2. LLM 不可用 → 返回 default_skill("sync_direct"), "LLM 不可用，使用默认策略"
    3. 构建 prompt，调用 LLM
    4. 提取 JSON 响应
    5. 校验 strategy_template_id 是否在注册表中
       - 不存在 → 回退到 sync_direct，记录警告
    6. 校验 params
       - 校验失败 → 使用 default_params 替代
    7. 获取模板的 assertions
    8. 构建 MicroSkill 并返回
    """
    # 1. 获取 LLM 配置
    config = get_llm_config()
    if config is None:
        # LLM 不可用，降级到默认策略
        return (default_skill("sync_direct"), "LLM 不可用，使用默认策略")

    # 2. 导入 OpenAI
    try:
        from openai import OpenAI
    except ImportError:
        return (default_skill("sync_direct"), "LLM 不可用，使用默认策略")

    # 3. 构建 prompt，调用 LLM
    prompt = build_parse_prompt(nl_description, step_tree)

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
        print(f"[ReckonBox] skill parse LLM call failed ({e})",
              file=sys.stderr)
        return (None, f"LLM 调用失败: {e}")

    # 4. 提取 JSON 响应
    try:
        data = _extract_json(content)
    except ValueError:
        return (None, "LLM 返回格式无法解析")

    # 5. 校验 strategy_template_id
    template_id = data.get("strategy_template_id", "")
    if get_template(template_id) is None:
        # 不存在的模板，回退到 sync_direct
        print(f"[ReckonBox] LLM 返回未注册模板 '{template_id}'，回退到 sync_direct",
              file=sys.stderr)
        template_id = "sync_direct"

    # 6. 校验 params
    raw_params = data.get("params", {})
    errors = validate_params(template_id, raw_params)
    if errors:
        # 校验失败，使用 default_params 替代
        print(f"[ReckonBox] LLM 返回参数校验失败 {errors}，使用默认参数",
              file=sys.stderr)
        params = default_params(template_id)
    else:
        params = raw_params

    # 7. 获取模板的 assertions
    template = get_template(template_id)
    assertions = list(template.assertions)  # 浅拷贝模板断言

    # 8. 构建 MicroSkill 并返回
    skill = MicroSkill(
        strategy_template_id=template_id,
        params=params,
        assertions=assertions,
        step_tree=step_tree,
    )

    return (skill, "解析成功")
