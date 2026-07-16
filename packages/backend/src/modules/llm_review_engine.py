"""
LLM 审查引擎
============
模拟运行后的 LLM 审查，基于模拟报告、MicroSkill 策略和黑盒规格，
生成结构化的审查结果（问题列表 + 知识卡片）。

与 design_reviewer.py 的区别：
  design_reviewer 分析「设计本身」→ 设计阶段的逻辑/安全/降级缺陷
  llm_review_engine 分析「模拟结果 + 策略选择」→ 模拟后的问题定位与教学反馈

两种问题反馈模式：
  is_guidance=True  → 设计决策引导（Socratic 提问式），如"如果改用 retry_with_backoff 会怎样？"
  is_guidance=False → 知识卡片（直接事实），如"retry_no_backoff 在下游故障时会导致重试风暴"

LLM 调用策略：
  复用 get_llm_config() 读取配置（仅环境变量）。
  LLM 不可用或调用失败时降级到基于规则引擎的基础反馈。

配置来源：
  OPENAI_API_KEY — API 密钥（必需）
  OPENAI_BASE_URL — 自定义 API 端点（可选）
  OPENAI_MODEL / RECKONBOX_LLM_MODEL — 模型名（默认 deepseek-v4-flash）
"""

import json
import re
import sys
from dataclasses import dataclass, field

from utils.llm_config import get_llm_config


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class ReviewIssue:
    """审查发现的问题。

    字段：
    - type: 问题类型
      "missing_degradation" | "logic_issue" | "missing_component" |
      "anti_pattern" | "strategy_mismatch"
    - severity: 严重程度 "high" | "medium" | "low"
    - component: 相关组件名
    - description: 问题描述
    - suggestion: 改进建议
    - is_guidance: True=设计决策引导(Socratic提问), False=知识卡片(直接事实)
    """
    type: str
    severity: str
    component: str
    description: str
    suggestion: str
    is_guidance: bool


@dataclass
class ReviewResult:
    """审查结果。

    字段：
    - issues: 所有发现的问题
    - knowledge_cards: 知识卡片列表，从 is_guidance=False 的 issue 中提取
    - summary: 总结描述
    """
    issues: list[ReviewIssue] = field(default_factory=list)
    knowledge_cards: list[dict] = field(default_factory=list)
    summary: str = ""


# ---------------------------------------------------------------------------
# 内部辅助函数
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> list[dict]:
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

    raise ValueError("无法从 LLM 返回内容中提取 JSON")


# ---------------------------------------------------------------------------
# 规则引擎降级审查
# ---------------------------------------------------------------------------

# 规则引擎关注的事件类型
_RULE_EVENT_TYPES = {
    "component_crash",
    "circuit_breaker_open",
    "assertion_triggered",
}

# 事件类型到问题类型的映射
_EVENT_TO_ISSUE_TYPE = {
    "component_crash": "missing_degradation",
    "circuit_breaker_open": "anti_pattern",
    "assertion_triggered": "logic_issue",
}

# 事件类型到严重程度的映射
_EVENT_TO_SEVERITY = {
    "component_crash": "high",
    "circuit_breaker_open": "medium",
    "assertion_triggered": "high",
}


def _rule_based_review(report: dict) -> ReviewResult:
    """基于规则引擎的降级审查。

    遍历 report["key_events"]，对每个 COMPONENT_CRASH /
    CIRCUIT_BREAKER_OPEN / ASSERTION_TRIGGERED 事件生成 ReviewIssue。

    is_guidance 固定为 False（规则引擎无法做引导性提问）。
    summary = "基于规则引擎的基础反馈（LLM 不可用）"
    """
    issues = []
    key_events = report.get("key_events", [])

    for event in key_events:
        event_type = event.get("type", "")
        if event_type not in _RULE_EVENT_TYPES:
            continue

        component = event.get("component", "未知组件")
        detail = event.get("detail", "")

        issue_type = _EVENT_TO_ISSUE_TYPE.get(event_type, "logic_issue")
        severity = _EVENT_TO_SEVERITY.get(event_type, "medium")

        issues.append(ReviewIssue(
            type=issue_type,
            severity=severity,
            component=component,
            description=detail,
            suggestion="请检查该组件的容错配置",
            is_guidance=False,
        ))

    # 从 is_guidance=False 的 issue 中提取知识卡片
    knowledge_cards = _build_knowledge_cards(issues)

    return ReviewResult(
        issues=issues,
        knowledge_cards=knowledge_cards,
        summary="基于规则引擎的基础反馈（LLM 不可用）",
    )


# ---------------------------------------------------------------------------
# 知识卡片构建
# ---------------------------------------------------------------------------

def _build_knowledge_cards(issues: list[ReviewIssue]) -> list[dict]:
    """从 is_guidance=False 的 issue 中提取知识卡片。

    格式：{title, concept, explanation}
    - title: 组件名 + 问题类型
    - concept: suggestion（改进建议作为核心概念）
    - explanation: description（问题描述作为详细解释）
    """
    cards = []
    for issue in issues:
        if issue.is_guidance:
            continue
        cards.append({
            "title": f"{issue.component} - {issue.type}",
            "concept": issue.suggestion,
            "explanation": issue.description,
        })
    return cards


# ---------------------------------------------------------------------------
# prompt 构建
# ---------------------------------------------------------------------------

def build_review_prompt(
    report: dict,
    micro_skills: dict[str, "MicroSkill"],
    blackbox_spec: "BlackBoxSpec | None" = None,
) -> str:
    """构建 LLM 审查提示词。

    prompt 包含：
    - 模拟报告摘要（成功率、关键事件、崩溃点）
    - 每个组件的 MicroSkill 策略信息
    - 黑盒规格的性能边界（如果有）
    - 要求 LLM 检查的维度：策略选择合理性、遗漏的容错机制、级联风险
    - 要求 LLM 区分 is_guidance=True（引导性提问）和 is_guidance=False（直接知识）
    - 要求返回 JSON 数组
    """
    # 报告摘要
    total_requests = report.get("total_requests", 0)
    success_rate = report.get("success_rate", 0)
    successful = report.get("successful", 0)
    failed = report.get("failed", 0)

    prompt = f"""你是一位资深系统架构师，正在审查系统模拟运行后的结果。
请基于模拟报告和组件策略信息，找出系统中的问题并给出改进建议。

## 模拟报告摘要
- 总请求数: {total_requests}
- 成功: {successful}
- 失败: {failed}
- 成功率: {success_rate}%
"""

    # 关键事件
    key_events = report.get("key_events", [])
    if key_events:
        prompt += "\n## 关键事件\n"
        for event in key_events[:20]:
            tick = event.get("tick", "?")
            comp = event.get("component", "?")
            detail = event.get("detail", "")
            prompt += f"- [Tick {tick}] {comp}: {detail}\n"

    # 崩溃点（从组件统计中提取）
    component_stats = report.get("component_stats", {})
    crash_points = []
    for name, stats in component_stats.items():
        crash_count = stats.get("crash_count", 0)
        if crash_count > 0:
            crash_points.append(f"- {name}: 崩溃 {crash_count} 次")
    if crash_points:
        prompt += "\n## 崩溃点\n"
        prompt += "\n".join(crash_points) + "\n"

    # MicroSkill 策略信息
    if micro_skills:
        prompt += "\n## 组件策略信息\n"
        for comp_name, skill in micro_skills.items():
            strategy_id = skill.strategy_template_id
            params = skill.params
            prompt += f"- {comp_name}: 策略={strategy_id}, 参数={json.dumps(params, ensure_ascii=False)}\n"

    # 黑盒规格的性能边界
    if blackbox_spec is not None:
        bounds = blackbox_spec.performance_bounds
        if bounds:
            prompt += f"\n## 黑盒规格 - 性能边界\n"
            prompt += json.dumps(bounds, ensure_ascii=False, indent=2) + "\n"
        exceptions = blackbox_spec.exception_scenarios
        if exceptions:
            prompt += f"\n## 黑盒规格 - 异常场景\n"
            for exc in exceptions:
                prompt += f"- {exc.get('name', '')}: {exc.get('description', '')}\n"

    # 检查维度与输出格式要求
    prompt += """
## 你需要检查以下维度

1. **策略选择合理性**（type=strategy_mismatch）
   组件的 MicroSkill 策略是否适合当前场景和依赖关系。
   例如高并发场景使用 retry_no_backoff 会导致重试风暴。

2. **遗漏的容错机制**（type=missing_degradation）
   依赖故障时缺少降级、熔断、超时等保护机制。

3. **级联风险**（type=anti_pattern）
   无熔断的级联调用、无幂等的重试、单点故障等安全反模式。

4. **逻辑问题**（type=logic_issue）
   组件之间的协作逻辑不合理，或配置参数存在矛盾。

5. **缺失组件**（type=missing_component）
   设计中暗示但未实现的关键组件。

## 反馈模式

对每个问题，你需要区分两种反馈模式：
- **is_guidance=true**: 设计决策引导（Socratic 提问式），用引导性提问启发思考。
  例如："如果 order_service 改用 retry_with_backoff 策略，在下游故障时会怎样？"
- **is_guidance=false**: 知识卡片（直接事实），提供确定性的知识点。
  例如："retry_no_backoff 在下游故障时会导致重试风暴，因为所有重试会立即打满队列"

建议：对于策略选择问题优先使用 is_guidance=true（引导学生思考），
      对于安全反模式和确定事实使用 is_guidance=false（直接告知风险）。

## 输出格式

请返回严格的 JSON 数组，每个元素是一个 issue，结构如下：

```json
[
  {
    "type": "strategy_mismatch" | "missing_degradation" | "anti_pattern" | "logic_issue" | "missing_component",
    "severity": "high" | "medium" | "low",
    "component": "组件名",
    "description": "问题描述",
    "suggestion": "改进建议",
    "is_guidance": true 或 false
  }
]
```

要求：
- 只返回 JSON 数组，不要附加其他解释文字
- 如果没有问题，返回空数组 `[]`
- severity 用于标注问题严重程度
- component 字段填入相关组件名；如果是跨组件问题，填入主要相关组件名
- is_guidance 必须明确指定为 true 或 false
"""
    return prompt


# ---------------------------------------------------------------------------
# 主接口
# ---------------------------------------------------------------------------

def review(
    report: dict,
    micro_skills: dict[str, "MicroSkill"] | None = None,
    blackbox_spec: "BlackBoxSpec | None" = None,
) -> ReviewResult:
    """模拟后 LLM 审查。

    流程：
    1. LLM 不可用 → 返回基于规则引擎的基础反馈
    2. LLM 可用 → 调用 LLM，解析结果
    3. 解析失败 → 返回基于规则引擎的基础反馈
    """
    config = get_llm_config()
    if config is None:
        return _rule_based_review(report)

    try:
        from openai import OpenAI
    except ImportError:
        return _rule_based_review(report)

    # 确保 micro_skills 不为 None（传给 prompt 构建）
    skills = micro_skills or {}
    prompt = build_review_prompt(report, skills, blackbox_spec)

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
        print(f"[ReckonBox] llm review LLM call failed ({e})",
              file=sys.stderr)
        return _rule_based_review(report)

    # 解析 LLM 返回的 JSON
    try:
        raw_issues = _extract_json(content)
    except ValueError:
        return _rule_based_review(report)

    # 转换为 ReviewIssue 列表
    issues = []
    for item in raw_issues:
        issues.append(ReviewIssue(
            type=item.get("type", "logic_issue"),
            severity=item.get("severity", "medium"),
            component=item.get("component", ""),
            description=item.get("description", ""),
            suggestion=item.get("suggestion", ""),
            is_guidance=bool(item.get("is_guidance", False)),
        ))

    # 构建知识卡片
    knowledge_cards = _build_knowledge_cards(issues)

    summary = f"LLM 审查完成，发现 {len(issues)} 个问题"
    return ReviewResult(
        issues=issues,
        knowledge_cards=knowledge_cards,
        summary=summary,
    )
