"""Remediation Guide (修复引导)
==============================
在模拟运行后生成修复引导，区分两类内容：
  - 教学卡片（直接知识）：从 TeachingCardBank 加载，可选 LLM 个性化
  - 设计决策引导（Socratic 提问）：用提问引导学生思考而非直接给答案

与 ai_tutor.py 的区别：
  ai_tutor 生成整体教育性反馈（叙事 + 改进方向）。
  remediation_guide 输出结构化的 RemediationReport，便于 UI 分区展示：
  - crash_analysis: 按严重性排序的崩溃点（含 rank）
  - teaching_cards: 每个失败模式对应的知识卡片
  - design_guidance: 每个失败模式对应的 Socratic 提问

LLM 调用策略与 ai_tutor.py / design_reviewer.py 一致：
  复用 get_llm_config() 读取配置（环境变量 > st.secrets）。
  LLM 不可用或调用失败时使用 FALLBACK_GUIDANCE 硬编码模板。
"""

import json
import re
import sys
from dataclasses import dataclass

from modules.ai_tutor import get_llm_config
from modules.teaching_cards import get_card, personalize_card


# ---------------------------------------------------------------------------
# 严重性排序（数值越小越严重）
# ---------------------------------------------------------------------------

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


# ---------------------------------------------------------------------------
# 硬编码引导模板（LLM 不可用时的 fallback）
# ---------------------------------------------------------------------------

FALLBACK_GUIDANCE = {
    "retry_storm": "如果把 retry_count 改成 0 会怎样？重试和熔断器应该谁先触发？",
    "spof_cascade": "增加副本就能完全解决问题吗？如果两个副本同时崩溃呢？",
    "queue_overflow": "丢弃请求和让请求等待，哪个对用户更好？为什么？",
    "missing_circuit_breaker": "熔断器打开后，请求去哪了？用户体验是什么？如何优雅降级？",
    "memory_exhaustion": "内存不够时，除了加内存，还有什么策略？",
    "missing_rate_limiter": "限流会损失请求，但为什么生产系统都必须有限流？",
    "consumer_lag_accumulation": "消息积压时，是增加消费者还是降低生产速度更有效？",
    "scheduled_task_thundering_herd": "如果 jitter 设为 5，流量分布会怎样变化？",
}


# ---------------------------------------------------------------------------
# RemediationReport 数据结构
# ---------------------------------------------------------------------------

@dataclass
class RemediationReport:
    """修复引导报告。

    Fields:
        crash_analysis: 结构化崩溃点分析（按严重性排序，rank 从 1 开始）
        teaching_cards: 教学卡片列表（每个失败模式一张）
        design_guidance: 设计决策引导（Socratic 提问）
    """
    crash_analysis: list[dict]
    teaching_cards: list[dict]
    design_guidance: list[dict]


# ---------------------------------------------------------------------------
# 内部辅助函数
# ---------------------------------------------------------------------------

def _component_to_dict(comp) -> dict:
    """将单个组件统一转为 dict（支持 dict 和 dataclass 实例）。

    design_spec 中的 components 可能是 dict 或 ComponentConfig，
    这里统一成 dict 便于提取参数。
    """
    if isinstance(comp, dict):
        return dict(comp)
    try:
        from dataclasses import asdict, is_dataclass
        if is_dataclass(comp) and not isinstance(comp, type):
            return asdict(comp)
    except Exception:
        pass
    return {}


def _extract_component_params(design_spec: dict, component_name: str) -> dict:
    """从 design_spec 中按组件名提取参数值。

    返回组件的参数 dict（剔除 name/type/description/dependencies 等元字段）。
    未找到时返回空 dict。
    """
    if not design_spec or not component_name:
        return {}
    components = design_spec.get("components", [])
    meta_keys = {"name", "type", "description", "dependencies"}
    for comp in components:
        comp_dict = _component_to_dict(comp)
        if comp_dict.get("name") == component_name:
            return {k: v for k, v in comp_dict.items() if k not in meta_keys}
    return {}


def _extract_json_array(text: str) -> list[dict]:
    """从 LLM 返回的文本中提取 JSON 数组。

    处理以下情况：
      - 纯 JSON 数组
      - ```json ... ``` 包裹
      - ``` ... ``` 包裹（无语言标识）
      - JSON 前后包含解释文字

    返回 list[dict]。无法提取时抛出 ValueError。
    """
    # 优先提取 ```json ... ``` 或 ``` ... ``` 代码块
    match = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.DOTALL)
    if match:
        text = match.group(1)

    text = text.strip()

    # 尝试直接解析
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        pass

    # 兜底：截取第一个 [ 到最后一个 ] 的子串
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            data = json.loads(text[start:end + 1])
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass

    raise ValueError("无法从 LLM 返回内容中提取 JSON 数组")


# ---------------------------------------------------------------------------
# crash_analysis
# ---------------------------------------------------------------------------

def _build_crash_analysis(failure_modes: list[dict]) -> list[dict]:
    """从失败模式列表构建结构化崩溃点分析。

    - 提取 failure_mode/component/severity/evidence
    - 按严重性排序（critical > high > medium > low）
    - 添加 rank 字段（从 1 开始递增）
    - 未知严重性视作最低优先级
    """
    items = [
        {
            "failure_mode": mode.get("mode", ""),
            "component": mode.get("component", ""),
            "severity": mode.get("severity", "low"),
            "evidence": mode.get("evidence", ""),
        }
        for mode in failure_modes
    ]

    # 按严重性排序；未知严重性排到最后
    items.sort(key=lambda x: SEVERITY_ORDER.get(x["severity"], len(SEVERITY_ORDER)))

    # rank 从 1 开始递增
    for idx, item in enumerate(items, start=1):
        item["rank"] = idx

    return items


# ---------------------------------------------------------------------------
# teaching_cards
# ---------------------------------------------------------------------------

def _build_teaching_cards(failure_modes: list[dict],
                          design_spec: dict = None) -> list[dict]:
    """为每个失败模式加载教学卡片。

    - 调用 TeachingCardBank.get_card(mode["mode"]) 获取模板
    - 模板存在时调用 personalize_card 个性化
      context 包含 component_name/component_params/failure_evidence
    - 未知失败模式（get_card 返回 None）跳过
    """
    cards = []
    for mode in failure_modes:
        mode_name = mode.get("mode", "")
        template = get_card(mode_name)
        if template is None:
            continue

        component_name = mode.get("component", "")
        context = {
            "component_name": component_name,
            "component_params": _extract_component_params(design_spec, component_name),
            "failure_evidence": mode.get("evidence", ""),
        }

        personalized, _status = personalize_card(template, context)
        cards.append({
            "failure_mode": mode_name,
            "card": personalized,
        })

    return cards


# ---------------------------------------------------------------------------
# design_guidance
# ---------------------------------------------------------------------------

def _build_guidance_prompt(crash_analysis: list[dict],
                           blackbox_spec: dict = None) -> str:
    """构建给 LLM 的设计决策引导提示词。

    prompt 要求：
      - 不直接给出答案，用提问引导用户思考（Socratic 方法）
      - 为每个崩溃点生成一个引导性提问
      - 输出 JSON 数组：[{failure_mode, component, question}]
    """
    crash_text = json.dumps(crash_analysis, ensure_ascii=False, indent=2)

    blackbox_text = ""
    if blackbox_spec:
        blackbox_text = f"""
## 黑盒上下文（用户场景）
- 场景描述: {blackbox_spec.get('scenario_description', '')}
- 功能点: {', '.join(blackbox_spec.get('functional_points', []))}
- 性能边界: {json.dumps(blackbox_spec.get('performance_bounds', {}), ensure_ascii=False)}
- 异常场景: {json.dumps(blackbox_spec.get('exception_scenarios', []), ensure_ascii=False)}
"""

    prompt = f"""你是一位系统设计导师。学生在沙盒中运行系统后出现了以下崩溃点。
请为每个崩溃点生成一个 Socratic 引导性提问，帮助学生通过思考自己找到修复方向。

## 崩溃点分析
```json
{crash_text}
```
{blackbox_text}
## 你的任务
对每个崩溃点，生成一个引导性提问。要求：
1. **不要直接给出答案或修复建议**
2. 用提问引导学生思考根本原因和权衡
3. 提问应当能触发学生对核心概念（如重试、熔断、降级、限流、冗余、背压等）的反思
4. 每个提问 1-2 句话，简洁有力
5. 引用具体的组件名和参数值，使提问更有针对性

## 输出格式
请返回严格的 JSON 数组，每个元素结构如下：

```json
[
  {{
    "failure_mode": "retry_storm",
    "component": "order_service",
    "question": "如果把 retry_count 改成 0 会怎样？重试和熔断器应该谁先触发？"
  }}
]
```

要求：
- 只返回 JSON 数组，不要附加其他解释文字
- 每个崩溃点对应一个提问
- failure_mode 和 component 字段必须与输入的崩溃点一致
"""
    return prompt


def _build_design_guidance_with_llm(
    crash_analysis: list[dict],
    blackbox_spec: dict = None,
    config: dict = None,
) -> list[dict] | None:
    """LLM 可用时：调用 LLM 生成 Socratic 引导提问。

    Returns:
        list[dict]: LLM 返回的引导列表（可能为空）
        None: LLM 调用失败、openai 未安装或返回格式无法解析
    """
    try:
        from openai import OpenAI
    except ImportError:
        print("[ReckonBox] openai package not installed, using fallback guidance",
              file=sys.stderr)
        return None

    if config is None:
        config = get_llm_config()
    if config is None:
        return None

    prompt = _build_guidance_prompt(crash_analysis, blackbox_spec)

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
        print(f"[ReckonBox] design_guidance LLM call failed ({e})",
              file=sys.stderr)
        return None

    try:
        return _extract_json_array(content)
    except ValueError:
        return None


def _build_design_guidance_with_fallback(failure_modes: list[dict]) -> list[dict]:
    """LLM 不可用时：使用 FALLBACK_GUIDANCE 硬编码模板。

    为每个失败模式查找预定义的引导性提问。
    未知失败模式（不在 FALLBACK_GUIDANCE 中）跳过。
    """
    guidance = []
    for mode in failure_modes:
        mode_name = mode.get("mode", "")
        question = FALLBACK_GUIDANCE.get(mode_name)
        if not question:
            continue
        guidance.append({
            "failure_mode": mode_name,
            "component": mode.get("component", ""),
            "question": question,
        })
    return guidance


def _build_design_guidance(
    failure_modes: list[dict],
    crash_analysis: list[dict],
    blackbox_spec: dict = None,
) -> list[dict]:
    """构建设计决策引导。

    LLM 可用时调用 LLM 生成 Socratic 提问；
    LLM 不可用或调用失败时使用 FALLBACK_GUIDANCE 模板。
    """
    config = get_llm_config()
    if config is not None:
        guidance = _build_design_guidance_with_llm(
            crash_analysis, blackbox_spec, config
        )
        if guidance is not None:
            return guidance
    return _build_design_guidance_with_fallback(failure_modes)


# ---------------------------------------------------------------------------
# 主接口
# ---------------------------------------------------------------------------

def generate_guidance(
    report: dict,
    design_spec: dict = None,
    blackbox_spec: dict = None,
) -> RemediationReport:
    """生成修复引导报告。

    Args:
        report: SandboxEngine.get_report() 的输出
        design_spec: DesignSpec 的 dict 表示（可选，用于教学卡片个性化上下文）
        blackbox_spec: BlackBoxSpec 的 dict 表示（可选，用于 LLM 引导上下文）

    Returns:
        RemediationReport，包含 crash_analysis / teaching_cards / design_guidance
    """
    failure_modes = report.get("failure_modes") or []

    crash_analysis = _build_crash_analysis(failure_modes)
    teaching_cards = _build_teaching_cards(failure_modes, design_spec)
    design_guidance = _build_design_guidance(
        failure_modes, crash_analysis, blackbox_spec
    )

    return RemediationReport(
        crash_analysis=crash_analysis,
        teaching_cards=teaching_cards,
        design_guidance=design_guidance,
    )
