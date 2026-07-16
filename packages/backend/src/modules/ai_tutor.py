"""
AI Tutor (LLM 评估器 v2)
========================
同时分析「模拟结果」和「学生决策树」，生成深层教育反馈。

两种分析维度的差异：
  模拟结果分析 → "你的设计导致了什么后果"
  决策树分析   → "你的设计选择暴露了什么认知模式"

两者结合 → "你在做X决策时没有考虑到Y，所以系统中出现了Z"

LLM 调用策略：
  始终尝试 LLM 调用（OpenAI 兼容 API），失败时自动 fallback 到模板生成器。
  配置来源：仅环境变量（建议通过 .env 注入）。
  支持的配置项：
    OPENAI_API_KEY    — API 密钥（必需）
    OPENAI_BASE_URL   — 自定义 API 端点（可选）
    OPENAI_MODEL      — 模型名（默认 deepseek-v4-flash）
    RECKONBOX_LLM_MODEL — 兼容旧变量名（可选）
"""

import sys

from modules.decision_tree_analyzer import DecisionTreeAnalyzer, format_decision_tree_for_llm
from utils.llm_config import get_llm_config, is_llm_configured


def build_analysis_prompt(design_name: str, design_description: str,
                          scenario: str, report: dict,
                          design_spec: dict = None) -> str:
    """构建给LLM的分析提示词（增强版：包含决策树分析）"""

    # 构建组件描述查找表（design_spec.components 可能为 dict 或 ComponentConfig）
    comp_desc_map = {}
    if design_spec:
        for comp in design_spec.get("components", []):
            if isinstance(comp, dict):
                name = comp.get("name", "")
                desc = comp.get("description", "")
            else:
                name = getattr(comp, "name", "")
                desc = getattr(comp, "description", "")
            if name and desc:
                comp_desc_map[name] = desc

    prompt = f"""你是一位系统设计导师。学生提交了一个系统设计方案，在沙盒中运行后出现了以下结果。

## 学生的设计方案
{design_name}: {design_description}

## 组件配置
"""
    for name, stats in report["component_stats"].items():
        prompt += f"""
- {name} ({stats['type']}):
  - 副本数: {stats['replicas']}
  - 重试次数: {stats['retry_count_config']}
  - 队列容量: {stats['queue_capacity']}
  - 熔断器: {'有' if stats['has_circuit_breaker'] else '无'}
  - 依赖: {stats['dependencies']}
"""
        if name in comp_desc_map:
            prompt += f"  - 描述: {comp_desc_map[name]}\n"

    # 如果有设计规格，附加决策树分析
    if design_spec:
        analyzer = DecisionTreeAnalyzer()
        dt_report = analyzer.analyze(design_spec)
        dt_text = format_decision_tree_for_llm(dt_report, design_spec)
        prompt += f"""
{dt_text}
"""
        # 附加性能预估摘要
        if dt_report.performance_estimates:
            pe = dt_report.performance_estimates
            perf_lines = []
            throughput = pe.get("throughput") or []
            if throughput:
                bottleneck = next(
                    (e for e in throughput if e.get("is_bottleneck")), None
                )
                if bottleneck is None:
                    bottleneck = min(
                        throughput, key=lambda e: e.get("capacity", 0)
                    )
                perf_lines.append(
                    f"- 吞吐瓶颈: {bottleneck['component']} "
                    f"({bottleneck['capacity']} req/tick)"
                )
            latency = pe.get("latency") or {}
            if latency and latency.get("estimated_ms") is not None:
                perf_lines.append(
                    f"- 端到端延迟下限: {latency['estimated_ms']}ms"
                )
            spof_risks = pe.get("spof_risk") or []
            for risk in spof_risks:
                perf_lines.append(
                    f"- SPOF 风险: {risk['component']} ({risk['risk_level']})"
                )
            if perf_lines:
                prompt += "\n## 性能预估\n" + "\n".join(perf_lines) + "\n"

    prompt += f"""
## 测试场景: {scenario}

## 运行结果
- 总请求数: {report['total_requests']}
- 成功: {report['successful']}
- 失败: {report['failed']}
- 成功率: {report['success_rate']}%

## 各组件统计
"""
    for name, stats in report["component_stats"].items():
        prompt += f"- {name}: 处理 {stats['total_processed']}, 失败 {stats['total_failed']}, 重试 {stats['total_retried']}, 崩溃 {stats['crash_count']} 次\n"

    prompt += f"""
## 关键事件
"""
    for event in report["key_events"][:15]:
        prompt += f"- [Tick {event['tick']}] {event['component']}: {event['detail']}\n"

    prompt += f"""
## 自动检测到的失败模式
"""
    for mode in report["failure_modes"]:
        prompt += f"""
- {mode['mode']} ({mode['severity']}): {mode['evidence']}
"""

    prompt += """
## 你的任务
请基于以上模拟结果和决策树分析，为这位学生提供教育性反馈。

你的反馈需要同时覆盖两个维度：
1. **后果维度**：系统中发生了什么失败，这些失败如何从设计参数中产生
2. **认知维度**：学生的设计选择暴露了什么认知模式——哪些概念理解了，哪些概念缺失了，推理链在哪里断裂

具体格式：

1. **发生了什么**：用叙事语言描述系统在压力下的崩溃过程
2. **你的设计决策分析**：
   - 指出2-3个关键的设计决策，分析学生在这个决策上展现了什么理解、缺失了什么理解
   - 如果决策树分析发现了"推理链断裂"（如理解重试但不理解故障隔离），重点解释这个断裂
   - 如果发现了矛盾（如给数据库冗余但给关键服务没有），指出这个不一致
3. **改进方向**：针对每个认知盲区，给引导性提问而非直接答案
4. **延伸思考**：一个进阶问题，引导学生从当前水平进一步思考

语气：像一个有经验的高级工程师在code review中给后辈建议。友善但直接，指出问题时不绕弯子。
"""
    return prompt


def generate_educational_feedback(design_name: str, report: dict,
                                  design_spec: dict = None) -> str:
    """
    生成教育性反馈（增强版：融合决策树分析）

    始终尝试 LLM 调用，失败时自动 fallback 到模板生成器。
    """
    config = get_llm_config()
    if config:
        result = _generate_with_llm(design_name, report, design_spec, config)
        if result is not None:
            return result
    return _generate_with_template(design_name, report, design_spec)


def _generate_with_template(design_name: str, report: dict,
                            design_spec: dict = None) -> str:
    """本地模板生成器：融合决策树分析生成教育性反馈"""
    feedback_parts = []

    # ---- 如果有设计规格，先做决策树分析 ----
    dt_report = None
    if design_spec:
        analyzer = DecisionTreeAnalyzer()
        dt_report = analyzer.analyze(design_spec)

    # ---- 提取组件描述（用于设计意图段落和失败模式引用） ----
    comp_descriptions = {}
    if design_spec:
        for comp in design_spec.get("components", []):
            if isinstance(comp, dict):
                name = comp.get("name", "")
                desc = comp.get("description", "")
            else:
                name = getattr(comp, "name", "")
                desc = getattr(comp, "description", "")
            if name and desc:
                comp_descriptions[name] = desc

    # ---- 标题 ----
    feedback_parts.append(f"# 沙盒反馈报告\n")
    feedback_parts.append(f"**设计方案**: {design_name}\n")
    feedback_parts.append(f"**测试场景**: {report['scenario']}\n")
    feedback_parts.append(f"**系统成功率**: {report['success_rate']}%\n")
    feedback_parts.append(f"**总请求数**: {report['total_requests']}（成功 {report['successful']} / 失败 {report['failed']}）\n\n")

    # ---- 决策树概览 ----
    if dt_report:
        feedback_parts.append("## 设计决策树概览\n")
        feedback_parts.append(f"**知识覆盖率**: {dt_report.coverage_score * 100:.0f}%\n\n")

        if dt_report.concepts_demonstrated:
            feedback_parts.append(f"**已展现理解的概念**: {', '.join(dt_report.concepts_demonstrated)}\n")
        if dt_report.concepts_missing:
            feedback_parts.append(f"**完全未考虑的概念**: {', '.join(dt_report.concepts_missing)}\n")
        if dt_report.concepts_partial:
            feedback_parts.append(f"**考虑了但配置不当**: {', '.join(dt_report.concepts_partial)}\n")
        feedback_parts.append("\n")

    # ---- 你的设计意图（来自组件 description） ----
    if comp_descriptions:
        feedback_parts.append("## 你的设计意图\n")
        feedback_parts.append("从你的设计描述中，可以读出你对各组件的设计意图：\n\n")
        for name, desc in comp_descriptions.items():
            feedback_parts.append(f"- **{name}**: {desc}\n")
        feedback_parts.append("\n")

    # ---- 1. 发生了什么 ----
    feedback_parts.append("## 1. 你的系统经历了什么\n")
    if report["success_rate"] >= 95:
        feedback_parts.append("你的系统在这个场景下表现良好，大部分请求都成功处理了。但这不代表设计没有问题——让我们看看决策树分析。\n\n")
    elif report["success_rate"] >= 70:
        feedback_parts.append(f"你的系统在 **{report['scenario']}** 场景下成功率只有 {report['success_rate']}%，"
                             "约三分之一的请求失败了。让我们看看失败发生在哪里。\n\n")
    else:
        feedback_parts.append(f"你的系统在 **{report['scenario']}** 场景下大面积崩溃，成功率仅 {report['success_rate']}。\n\n")

    if report["key_events"]:
        feedback_parts.append("以下是模拟过程中的关键事件：\n")
        for event in report["key_events"][:8]:
            feedback_parts.append(f"- **[Tick {event['tick']}]** {event['component']}: {event['detail']}\n")
        if len(report["key_events"]) > 8:
            feedback_parts.append(f"- ... 还有 {len(report['key_events']) - 8} 个事件\n")
        feedback_parts.append("\n")

    # ---- 2. 设计决策分析（核心增强） ----
    feedback_parts.append("## 2. 你的设计决策分析\n")

    if dt_report:
        # 2a. 推理链断裂
        if dt_report.reasoning_gaps:
            feedback_parts.append("### 推理链断裂\n")
            feedback_parts.append("你的设计中有几个地方展现了部分理解，但推理链没有走完：\n\n")
            for gap in dt_report.reasoning_gaps:
                feedback_parts.append(f"**{gap['description']}**\n")
                feedback_parts.append(f"{gap['implication']}\n\n")

                # 关联到具体的模拟后果
                if gap["gap"] == "retry_without_isolation":
                    retry_modes = [m for m in report["failure_modes"] if m["mode"] == "retry_storm"]
                    if retry_modes:
                        feedback_parts.append(f"> 在模拟中，这直接导致了：{retry_modes[0]['evidence']}\n\n")
                elif gap["gap"] == "redundancy_without_backpressure":
                    overflow_modes = [m for m in report["failure_modes"] if m["mode"] == "queue_overflow"]
                    if overflow_modes:
                        feedback_parts.append(f"> 在模拟中，这直接导致了：{overflow_modes[0]['evidence']}\n\n")

                feedback_parts.append(f"> 思考题：你已经理解了「{gap['trigger_concept']}」，那么当这个机制失效时，"
                                     f"「{gap['missing_concept']}」应该如何配合？\n\n")

        # 2b. 设计矛盾
        if dt_report.contradictions:
            feedback_parts.append("### 设计中的不一致\n")
            feedback_parts.append("你的不同组件之间有一些自相矛盾的配置，说明某些设计决策不是逐个思考的：\n\n")
            for c in dt_report.contradictions:
                feedback_parts.append(f"**{c['description']}**\n")
                feedback_parts.append(f"{c['implication']}\n\n")

        # 2c. 认知盲区
        missed_decisions = [d for d in dt_report.decisions if d.status == "missed"]
        if missed_decisions:
            feedback_parts.append("### 未考虑的概念\n")
            feedback_parts.append("以下是你完全没考虑到的设计维度：\n\n")
            for d in missed_decisions[:3]:
                feedback_parts.append(f"- **{d.component}**: {d.reasoning}\n")
                feedback_parts.append(f"  缺失概念: {d.blind_spot}\n")
                if d.related_concepts:
                    feedback_parts.append(f"  这通常意味着你也未考虑: {', '.join(d.related_concepts)}\n")
                feedback_parts.append("\n")

        # 2d. 做对了什么
        considered = [d for d in dt_report.decisions if d.status == "considered"]
        if considered:
            feedback_parts.append("### 做对的部分\n")
            feedback_parts.append("你的设计中也有一些好的决策：\n\n")
            for d in considered[:3]:
                feedback_parts.append(f"- **{d.component}.{d.parameter}={d.value}**: {d.reasoning}\n")
            feedback_parts.append("\n")

    # ---- 3. 改进方向 ----
    feedback_parts.append("## 3. 改进方向\n")
    if report["failure_modes"]:
        for mode in report["failure_modes"]:
            feedback_parts.append(f"**关于 {_mode_name_cn(mode['mode'])}**:\n")
            feedback_parts.append(f"{mode['suggestion']}\n\n")

            # 如果该失败模式的组件有 description，引用它对照模拟结果
            mode_comp = mode.get("component")
            if mode_comp and mode_comp in comp_descriptions:
                feedback_parts.append(
                    f"> 你在描述中说\"{comp_descriptions[mode_comp]}\"，"
                    f"但模拟中暴露了问题：{mode['evidence']}\n\n"
                )

            if mode["mode"] == "retry_storm":
                feedback_parts.append("> 思考题：如果把 retry_count 改成 0 会怎样？重试和熔断器应该谁先触发？\n\n")
            elif mode["mode"] == "spof_cascade":
                feedback_parts.append("> 思考题：增加副本就能完全解决问题吗？如果两个副本同时崩溃呢？\n\n")
            elif mode["mode"] == "missing_circuit_breaker":
                feedback_parts.append("> 思考题：熔断器打开后，请求去哪了？用户体验是什么？如何优雅降级？\n\n")
            elif mode["mode"] == "queue_overflow":
                feedback_parts.append("> 思考题：丢弃请求和让请求等待，哪个对用户更好？为什么？\n\n")
            elif mode["mode"] == "memory_exhaustion":
                feedback_parts.append("> 思考题：内存不够时，除了加内存，还有什么策略？\n\n")
            elif mode["mode"] == "missing_rate_limiter":
                feedback_parts.append("> 思考题：限流会损失请求，但为什么生产系统都必须有限流？\n\n")
    else:
        feedback_parts.append("当前设计在这个场景下表现不错。建议尝试更极端的场景来检验边界。\n\n")

    # ---- 4. 延伸思考 ----
    feedback_parts.append("## 4. 延伸思考\n")
    if dt_report and dt_report.coverage_score < 0.5:
        feedback_parts.append(
            "你的知识覆盖率较低，但这完全正常——系统设计是一个庞大的知识体系。"
            "关键不在于记住所有模式，而在于理解每个模式解决什么问题。\n\n"
            "建议你从当前最严重的失败模式开始，理解它的原理后再修改设计重新运行沙盒。"
            "每修一个问题，你的决策树就会多一个『已理解』的节点。\n"
        )
    elif report["success_rate"] < 50:
        feedback_parts.append(
            "你的系统在压力下崩溃了，但这其实是好事——在沙盒里崩溃总比在生产环境崩溃好。\n\n"
            "**进阶任务**：尝试修改你的设计，让系统在同样的压力下成功率提升到 90% 以上。"
            "每次修改后在沙盒中重新运行，观察决策树覆盖率的变化。\n"
        )
    else:
        feedback_parts.append(
            "你的系统在这个场景下存活了，但换个角度思考：**这个设计的成本是多少？**\n\n"
            "**进阶任务**：尝试在保持 95%+ 成功率的前提下，最小化系统的总副本数。\n"
        )

    return "".join(feedback_parts)


def _generate_with_llm(design_name: str, report: dict,
                       design_spec: dict = None,
                       config: dict = None) -> str | None:
    """使用 OpenAI 兼容 LLM 生成教育性反馈，失败时返回 None（由调用方 fallback）"""
    try:
        from openai import OpenAI
    except ImportError:
        print("[ReckonBox] openai package not installed, using template",
              file=sys.stderr)
        return None

    if config is None:
        config = get_llm_config()
    if config is None:
        return None

    if design_spec:
        domain = design_spec.get("domain", "system")
        components = design_spec.get("components", [])
        design_description = f"包含 {len(components)} 个组件的 {domain} 设计"
    else:
        design_description = design_name

    scenario = report.get("scenario", "normal")
    prompt = build_analysis_prompt(design_name, design_description, scenario,
                                   report, design_spec)

    try:
        client = OpenAI(
            api_key=config["api_key"],
            base_url=config.get("base_url"),
        )
        response = client.chat.completions.create(
            model=config.get("model", "deepseek-v4-flash"),
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"[ReckonBox] LLM call failed ({e}), using template",
              file=sys.stderr)
        return None


def _mode_name_cn(mode: str) -> str:
    names = {
        "retry_storm": "重试风暴",
        "spof_cascade": "单点故障级联",
        "queue_overflow": "队列溢出",
        "missing_circuit_breaker": "缺少熔断器保护",
        "memory_exhaustion": "内存耗尽",
        "missing_rate_limiter": "缺少限流保护",
    }
    return names.get(mode, mode)


def _severity_cn(severity: str) -> str:
    names = {
        "critical": "严重",
        "high": "高",
        "medium": "中",
        "low": "低",
    }
    return names.get(severity, severity)
