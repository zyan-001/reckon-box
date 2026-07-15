"""TeachingCardBank 教学卡片库
=============================
为每种失败模式提供教学卡片模板，支持 LLM 个性化定制。

与 ai_tutor.py 的区别：
  ai_tutor 生成整体教育性反馈（叙事 + 改进方向）。
  teaching_cards 针对单个失败模式提供聚焦的知识卡片，便于在 UI 中
  随失败事件就地展示。

LLM 调用策略：
  复用 get_llm_config() 读取配置（环境变量 > st.secrets）。
  LLM 不可用或调用失败时返回原模板 + 提示消息，纯 Python 可运行，
  不依赖 Streamlit。
"""

import json
import re
import sys
from pathlib import Path

from modules.ai_tutor import get_llm_config
from modules.domain_pack import load_domain_pack


# ---------------------------------------------------------------------------
# 默认领域包路径
# ---------------------------------------------------------------------------

DEFAULT_DOMAIN_PACK_PATH = str(
    Path(__file__).resolve().parent.parent / "domain_packs" / "software_systems.yaml"
)


# ---------------------------------------------------------------------------
# 卡片查询
# ---------------------------------------------------------------------------

def get_card(failure_mode: str, domain_pack=None) -> dict | None:
    """从 domain_pack 的 teaching_cards 中查找卡片模板。

    Args:
        failure_mode: 失败模式名称（如 "retry_storm"）
        domain_pack: DomainPack 对象。如果为 None，尝试从默认路径加载。

    Returns:
        卡片 dict（含 title/concept/explanation/code_example/related_concepts），
        未知失败模式返回 None。
    """
    if domain_pack is None:
        domain_pack = load_domain_pack(DEFAULT_DOMAIN_PACK_PATH)

    cards = getattr(domain_pack, "teaching_cards", None) or {}
    card = cards.get(failure_mode)
    if card is None:
        return None
    # 返回副本，避免调用方修改污染领域包缓存
    return dict(card)


# ---------------------------------------------------------------------------
# LLM 个性化
# ---------------------------------------------------------------------------

def _build_personalize_prompt(template: dict, context: dict) -> str:
    """构建给 LLM 的个性化提示词。

    要求 LLM 重写 explanation 段落，引用具体组件名和参数值，
    并以 JSON 对象形式返回新的 explanation 字段。
    """
    component_name = context.get("component_name", "未知组件")
    component_params = context.get("component_params", {})
    failure_evidence = context.get("failure_evidence", "")

    params_text = json.dumps(component_params, ensure_ascii=False, indent=2) \
        if component_params else "（无参数信息）"

    prompt = f"""你是一位系统设计导师。学生提交的系统在沙盒中触发了失败模式，请基于具体上下文重写教学卡片的 explanation 段落，使其更有针对性。

## 失败模式教学卡片模板
- 标题: {template.get("title", "")}
- 关联概念: {template.get("concept", "")}
- 原始 explanation: {template.get("explanation", "")}
- 代码示例: {template.get("code_example", "")}

## 触发该失败模式的具体上下文
- 组件名: {component_name}
- 组件参数:
{params_text}
- 失败证据: {failure_evidence or "（无具体证据）"}

## 你的任务
重写 explanation 段落，要求：
1. 引用具体的组件名（{component_name}）和关键参数值
2. 结合失败证据解释为什么这些参数会导致该失败模式
3. 保持教学性，解释原理而非仅描述现象
4. 长度控制在 2-4 句话
5. 不要修改 code_example 和 related_concepts

## 输出格式
请返回严格的 JSON 对象，只包含 explanation 字段：

```json
{{
  "explanation": "你重写的 explanation 文本"
}}
```

只返回 JSON 对象，不要附加其他解释文字。
"""
    return prompt


def _extract_json_object(text: str) -> dict:
    """从 LLM 返回的文本中提取 JSON 对象（dict）。

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

    raise ValueError("无法从 LLM 返回内容中提取 JSON 对象")


def personalize_card(template: dict, context: dict) -> tuple[dict, str]:
    """LLM 个性化教学卡片。

    Args:
        template: get_card 返回的模板 dict
        context: {{component_name, component_params, failure_evidence, ...}}

    Returns:
        (personalized_card, status_message)
        LLM 可用时：卡片中的 explanation 引用具体组件名和参数值
        LLM 不可用时：返回原模板 + "未配置 LLM，使用标准模板"
    """
    config = get_llm_config()
    if config is None:
        return dict(template), "未配置 LLM，使用标准模板"

    try:
        from openai import OpenAI
    except ImportError:
        print("[ReckonBox] openai package not installed, using standard template",
              file=sys.stderr)
        return dict(template), "未配置 LLM，使用标准模板"

    prompt = _build_personalize_prompt(template, context)

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
        print(f"[ReckonBox] personalize_card LLM call failed ({e})",
              file=sys.stderr)
        return dict(template), f"LLM 调用失败，使用标准模板: {e}"

    try:
        personalized = _extract_json_object(content)
    except ValueError:
        return dict(template), "LLM 返回格式无法解析，使用标准模板"

    # 合并：保留原始字段，仅用 LLM 输出更新 explanation
    # code_example 和 related_concepts 等其他字段保持不变
    result = dict(template)
    new_explanation = personalized.get("explanation")
    if isinstance(new_explanation, str) and new_explanation.strip():
        result["explanation"] = new_explanation.strip()

    return result, "LLM 个性化完成"
