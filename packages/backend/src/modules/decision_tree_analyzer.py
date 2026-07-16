"""
Decision Tree Analyzer
======================
从学生的设计参数中提取隐式决策树，分析认知模式。

核心洞察：学生不需要显式写出推理过程。
  - replicas=1 on database  → 没有考虑单点故障
  - retry_count=3 but no circuit_breaker → 理解重试但不理解故障隔离
  - rate_limiter=false on gateway → 没有考虑背压
  - memory_limit=256 + queue_capacity=200 → 容量规划矛盾

这些模式从参数组合中可以直接推断。
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DesignDecision:
    """单个设计决策的分析结果"""
    component: str
    parameter: str
    value: any
    concept: str                    # 这个决策涉及的知识点
    status: str                     # "considered" / "missed" / "contradicted" / "misconfigured"
    reasoning: str                  # 推断出的学生思维
    blind_spot: Optional[str] = None  # 如果是missed/contradicted，缺失了什么认知
    related_concepts: list = field(default_factory=list)  # 这个决策应该连带考虑但学生没考虑的概念


@dataclass
class DecisionTreeReport:
    """完整的决策树分析报告"""
    decisions: list[DesignDecision]
    concepts_demonstrated: list[str]       # 学生展现出理解的概念
    concepts_missing: list[str]            # 学生完全没考虑的概念
    concepts_partial: list[str]            # 学生考虑了但配置不当的概念
    contradictions: list[dict]             # 设计中的自相矛盾
    coverage_score: float                  # 知识覆盖率 0-1
    reasoning_gaps: list[dict]             # 推理链断裂点
    performance_estimates: dict = field(default_factory=dict)
    # 三个 key：
    #   - throughput: list[dict]，每个组件的理论吞吐上限
    #   - latency: dict，端到端延迟下限
    #   - spof_risk: list[dict]，单点故障风险


class DecisionTreeAnalyzer:
    """
    分析学生的设计决策树。

    不需要学生写推理过程——从参数组合推断认知模式。
    每个参数选择都是一棵隐式决策树的节点。
    """

    # 系统设计核心概念清单
    CONCEPTS = {
        "redundancy": "冗余设计——关键组件应有多个副本",
        "failure_isolation": "故障隔离——熔断器防止单点故障扩散",
        "backpressure": "背压控制——限流器防止洪峰冲击下游",
        "retry_semantics": "重试语义——重试次数与退避策略的权衡",
        "resource_sizing": "资源规划——内存与队列容量的匹配",
        "dependency_management": "依赖管理——理解服务间的依赖链",
        "timeout_strategy": "超时策略——避免请求无限等待",
        "graceful_degradation": "优雅降级——部分故障时保持核心功能",
    }

    def analyze(self, design_spec: dict) -> DecisionTreeReport:
        """分析设计规格，输出决策树报告"""
        components = design_spec.get("components", [])
        decisions = []
        contradictions = []

        # 按组件分析每个决策
        for comp in components:
            decisions.extend(self._analyze_redundancy(comp, components))
            decisions.extend(self._analyze_failure_isolation(comp))
            decisions.extend(self._analyze_backpressure(comp))
            decisions.extend(self._analyze_retry(comp))
            decisions.extend(self._analyze_resource_sizing(comp))
            decisions.extend(self._analyze_timeout(comp))
            decisions.extend(self._analyze_dependencies(comp, components))

        # 跨组件矛盾检测
        contradictions.extend(self._detect_contradictions(components))

        # 汇总概念覆盖
        demonstrated = list(set(
            d.concept for d in decisions if d.status == "considered"
        ))
        missing = list(set(
            d.blind_spot for d in decisions
            if d.status == "missed" and d.blind_spot
        ))
        partial = list(set(
            d.concept for d in decisions if d.status == "misconfigured"
        ))

        # 推理链断裂检测
        reasoning_gaps = self._detect_reasoning_gaps(decisions, components)

        # 覆盖率
        total_concepts = len(self.CONCEPTS)
        covered = len(set(demonstrated) | set(partial))
        coverage = covered / total_concepts if total_concepts > 0 else 0

        # 性能预估（纯设计参数分析，不依赖模拟结果）
        performance_estimates = {
            "throughput": self._estimate_throughput(components),
            "latency": self._estimate_latency(components),
            "spof_risk": self._predict_spof_risk(components),
        }

        return DecisionTreeReport(
            decisions=decisions,
            concepts_demonstrated=demonstrated,
            concepts_missing=missing,
            concepts_partial=partial,
            contradictions=contradictions,
            coverage_score=round(coverage, 2),
            reasoning_gaps=reasoning_gaps,
            performance_estimates=performance_estimates,
        )

    def _analyze_redundancy(self, comp: dict, all_components: list) -> list[DesignDecision]:
        """分析冗余决策"""
        decisions = []
        name = comp["name"]
        replicas = comp.get("replicas", 1)
        comp_type = comp.get("type", "")
        deps = comp.get("dependencies", [])

        # 关键路径判断：被其他组件依赖的就是关键路径
        dependents = [c["name"] for c in all_components if name in c.get("dependencies", [])]
        is_critical = len(dependents) > 0 or comp_type == "database"

        if replicas == 1:
            if is_critical:
                decisions.append(DesignDecision(
                    component=name,
                    parameter="replicas",
                    value=1,
                    concept="redundancy",
                    status="missed",
                    reasoning=f"学生将关键组件 {name}（被 {len(dependents)} 个服务依赖）设为单副本",
                    blind_spot="redundancy",
                    related_concepts=["failure_isolation", "graceful_degradation"],
                ))
            else:
                decisions.append(DesignDecision(
                    component=name,
                    parameter="replicas",
                    value=1,
                    concept="redundancy",
                    status="considered",
                    reasoning=f"{name} 是非关键组件，单副本可以接受",
                ))
        elif replicas >= 2:
            decisions.append(DesignDecision(
                component=name,
                parameter="replicas",
                value=replicas,
                concept="redundancy",
                status="considered",
                reasoning=f"学生为 {name} 配置了 {replicas} 个副本，理解冗余需求",
                related_concepts=["failure_isolation"],
            ))

        return decisions

    def _analyze_failure_isolation(self, comp: dict) -> list[DesignDecision]:
        """分析故障隔离决策（熔断器）"""
        decisions = []
        name = comp["name"]
        has_cb = comp.get("has_circuit_breaker", False)
        has_deps = len(comp.get("dependencies", [])) > 0
        retry = comp.get("retry_count", 0)

        if has_deps and not has_cb:
            decisions.append(DesignDecision(
                component=name,
                parameter="has_circuit_breaker",
                value=False,
                concept="failure_isolation",
                status="missed",
                reasoning=f"{name} 依赖其他服务但未配置熔断器",
                blind_spot="failure_isolation",
                related_concepts=["retry_semantics"],
            ))
        elif has_cb:
            decisions.append(DesignDecision(
                component=name,
                parameter="has_circuit_breaker",
                value=True,
                concept="failure_isolation",
                status="considered",
                reasoning=f"{name} 配置了熔断器，理解故障隔离",
            ))

        # 特殊模式：有重试但没熔断器 → 理解重试但不理解两者的交互
        if retry > 0 and not has_cb and has_deps:
            decisions.append(DesignDecision(
                component=name,
                parameter="retry_count_without_breaker",
                value=f"retry={retry}, circuit_breaker=False",
                concept="failure_isolation",
                status="contradicted",
                reasoning=f"{name} 设了 retry_count={retry} 但没有熔断器",
                blind_spot=None,
                related_concepts=["retry_semantics"],
            ))

        return decisions

    def _analyze_backpressure(self, comp: dict) -> list[DesignDecision]:
        """分析背压控制决策（限流器）"""
        decisions = []
        name = comp["name"]
        comp_type = comp.get("type", "")
        has_rl = comp.get("has_rate_limiter", False)

        if comp_type == "gateway" and not has_rl:
            decisions.append(DesignDecision(
                component=name,
                parameter="has_rate_limiter",
                value=False,
                concept="backpressure",
                status="missed",
                reasoning="网关没有限流器，洪峰流量会直接冲击下游",
                blind_spot="backpressure",
                related_concepts=["graceful_degradation"],
            ))
        elif has_rl:
            decisions.append(DesignDecision(
                component=name,
                parameter="has_rate_limiter",
                value=True,
                concept="backpressure",
                status="considered",
                reasoning=f"{name} 配置了限流器，理解背压控制",
            ))

        return decisions

    def _analyze_retry(self, comp: dict) -> list[DesignDecision]:
        """分析重试决策"""
        decisions = []
        name = comp["name"]
        retry = comp.get("retry_count", 0)
        has_deps = len(comp.get("dependencies", [])) > 0

        if retry == 0 and has_deps:
            # 有依赖但不重试——可能是故意为之（fast fail），也可能是没考虑
            decisions.append(DesignDecision(
                component=name,
                parameter="retry_count",
                value=0,
                concept="retry_semantics",
                status="considered",
                reasoning=f"{name} 不重试，可能倾向于快速失败策略",
            ))
        elif retry > 0:
            if retry <= 1:
                decisions.append(DesignDecision(
                    component=name,
                    parameter="retry_count",
                    value=retry,
                    concept="retry_semantics",
                    status="considered",
                    reasoning=f"{name} 保守重试（{retry}次），理解重试的代价",
                ))
            elif retry <= 3:
                decisions.append(DesignDecision(
                    component=name,
                    parameter="retry_count",
                    value=retry,
                    concept="retry_semantics",
                    status="considered",
                    reasoning=f"{name} 设了 {retry} 次重试，理解重试但可能未充分考虑重试放大效应",
                    related_concepts=["failure_isolation"],
                ))
            else:
                decisions.append(DesignDecision(
                    component=name,
                    parameter="retry_count",
                    value=retry,
                    concept="retry_semantics",
                    status="misconfigured",
                    reasoning=f"{name} 重试 {retry} 次过于激进，故障时放大 {retry+1} 倍负载",
                    blind_spot="retry_amplification",
                    related_concepts=["failure_isolation", "backpressure"],
                ))

        return decisions

    def _analyze_resource_sizing(self, comp: dict) -> list[DesignDecision]:
        """分析资源规划决策"""
        decisions = []
        name = comp["name"]
        memory = comp.get("memory_limit", 512)
        queue = comp.get("queue_capacity", 200)
        mem_per_req = comp.get("memory_per_request", 5)
        replicas = comp.get("replicas", 1)

        # 最大并发请求数 = queue_capacity，每个占 mem_per_request
        max_memory_needed = queue * mem_per_req
        if max_memory_needed > memory:
            decisions.append(DesignDecision(
                component=name,
                parameter="memory_vs_queue",
                value=f"memory={memory}MB, queue={queue}, per_req={mem_per_req}MB",
                concept="resource_sizing",
                status="contradicted",
                reasoning=f"{name} 队列满时需要 {max_memory_needed}MB 但内存限制只有 {memory}MB",
                blind_spot="resource_sizing",
                related_concepts=[],
            ))
        else:
            decisions.append(DesignDecision(
                component=name,
                parameter="memory_vs_queue",
                value=f"memory={memory}MB, queue={queue}",
                concept="resource_sizing",
                status="considered",
                reasoning=f"{name} 内存与队列容量匹配",
            ))

        return decisions

    def _analyze_timeout(self, comp: dict) -> list[DesignDecision]:
        """分析超时策略"""
        decisions = []
        name = comp["name"]
        timeout = comp.get("timeout_ms", 1000)
        comp_type = comp.get("type", "")

        if comp_type == "database" and timeout < 1000:
            decisions.append(DesignDecision(
                component=name,
                parameter="timeout_ms",
                value=timeout,
                concept="timeout_strategy",
                status="misconfigured",
                reasoning=f"数据库超时仅 {timeout}ms，正常查询都可能超时",
                blind_spot=None,
            ))
        elif comp_type == "cache" and timeout > 500:
            decisions.append(DesignDecision(
                component=name,
                parameter="timeout_ms",
                value=timeout,
                concept="timeout_strategy",
                status="misconfigured",
                reasoning=f"缓存超时 {timeout}ms 过长，缓存应该快速响应",
                blind_spot=None,
            ))
        else:
            decisions.append(DesignDecision(
                component=name,
                parameter="timeout_ms",
                value=timeout,
                concept="timeout_strategy",
                status="considered",
                reasoning=f"{name} 超时 {timeout}ms 合理",
            ))

        return decisions

    def _analyze_dependencies(self, comp: dict, all_components: list) -> list[DesignDecision]:
        """分析依赖管理决策"""
        decisions = []
        name = comp["name"]
        deps = comp.get("dependencies", [])
        comp_type = comp.get("type", "")

        # 数据库和缓存不应有依赖（它们是叶节点）
        if comp_type in ("database", "cache") and deps:
            decisions.append(DesignDecision(
                component=name,
                parameter="dependencies",
                value=deps,
                concept="dependency_management",
                status="misconfigured",
                reasoning=f"{comp_type} 不应有依赖，但 {name} 配置了 {deps}",
                blind_spot=None,
            ))
        elif deps:
            decisions.append(DesignDecision(
                component=name,
                parameter="dependencies",
                value=deps,
                concept="dependency_management",
                status="considered",
                reasoning=f"{name} 显式声明了依赖关系",
            ))

        # 检查是否有服务绕过缓存直接访问数据库
        if comp_type == "service":
            has_cache_dep = "cache" in deps
            has_db_dep = "database" in deps
            if has_db_dep and not has_cache_dep:
                decisions.append(DesignDecision(
                    component=name,
                    parameter="dependency_pattern",
                    value="db_without_cache",
                    concept="dependency_management",
                    status="missed",
                    reasoning=f"{name} 直接依赖数据库但未使用缓存，可能未考虑缓存层",
                    blind_spot="caching_pattern",
                    related_concepts=[],
                ))

        return decisions

    def _detect_contradictions(self, components: list) -> list[dict]:
        """检测跨组件的设计矛盾"""
        contradictions = []

        # 矛盾1：给数据库做了冗余但关键服务没有
        db = next((c for c in components if c.get("type") == "database"), None)
        if db and db.get("replicas", 1) >= 2:
            for c in components:
                if (c.get("type") == "service" and
                    c.get("replicas", 1) == 1 and
                    c.get("dependencies")):
                    dependents = [other["name"] for other in components
                                  if c["name"] in other.get("dependencies", [])]
                    if dependents:
                        contradictions.append({
                            "type": "inconsistent_redundancy",
                            "description": f"数据库有 {db['replicas']} 个副本但 {c['name']} 只有1个",
                            "implication": "学生理解数据库需要冗余但没把同样的逻辑应用到关键服务",
                            "components": [db["name"], c["name"]],
                        })

        # 矛盾2：网关有限流但下游服务没有熔断
        gateway = next((c for c in components if c.get("type") == "gateway"), None)
        if gateway and gateway.get("has_rate_limiter"):
            for c in components:
                if (c.get("type") == "service" and
                    c.get("dependencies") and
                    not c.get("has_circuit_breaker")):
                    contradictions.append({
                        "type": "partial_protection",
                        "description": f"网关有限流但 {c['name']} 没有熔断器",
                        "implication": "学生在入口做了保护但没在服务层做故障隔离",
                        "components": [gateway["name"], c["name"]],
                    })

        # 矛盾3：所有服务的retry_count都一样——可能是批量设置而非逐个思考
        services = [c for c in components if c.get("type") == "service"]
        if services:
            retry_values = set(c.get("retry_count", 0) for c in services)
            if len(retry_values) == 1 and 0 not in retry_values:
                contradictions.append({
                    "type": "uniform_retry",
                    "description": f"所有服务的 retry_count 都是 {retry_values.pop()}",
                    "implication": "学生可能批量设置了重试参数而非根据每个服务的特性单独决策",
                    "components": [c["name"] for c in services],
                })

        return contradictions

    def _detect_reasoning_gaps(self, decisions: list, components: list) -> list[dict]:
        """检测推理链断裂——学生考虑了A但没考虑A的后果B"""
        gaps = []

        # Gap 1: 考虑了重试但没考虑熔断
        has_retry = any(d.concept == "retry_semantics" and d.status == "considered"
                        and d.value > 0 for d in decisions)
        missing_cb = any(d.concept == "failure_isolation" and d.status == "missed"
                         for d in decisions)
        if has_retry and missing_cb:
            gaps.append({
                "gap": "retry_without_isolation",
                "description": "学生理解重试机制但未理解故障隔离",
                "implication": "知道重试能提高成功率，但没意识到无限制重试会放大故障",
                "missing_concept": "failure_isolation",
                "trigger_concept": "retry_semantics",
            })

        # Gap 2: 考虑了冗余但没考虑限流
        has_redundancy = any(d.concept == "redundancy" and d.status == "considered"
                             for d in decisions)
        missing_rl = any(d.concept == "backpressure" and d.status == "missed"
                         for d in decisions)
        if has_redundancy and missing_rl:
            gaps.append({
                "gap": "redundancy_without_backpressure",
                "description": "学生理解冗余但未理解背压控制",
                "implication": "知道要增加副本来处理负载，但没想过在入口限制流量",
                "missing_concept": "backpressure",
                "trigger_concept": "redundancy",
            })

        # Gap 3: 考虑了队列容量但没考虑内存限制
        has_queue = any(d.concept == "resource_sizing" and d.status == "contradicted"
                        for d in decisions)
        if has_queue:
            gaps.append({
                "gap": "queue_without_memory",
                "description": "学生配置了队列容量但未匹配内存限制",
                "implication": "理解排队机制但没计算队列满时的内存消耗",
                "missing_concept": "resource_sizing",
                "trigger_concept": "queue_management",
            })

        return gaps

    # ------------------------------------------------------------------
    # 性能预估（纯设计参数分析，不依赖模拟结果）
    # ------------------------------------------------------------------

    def _estimate_throughput(self, components: list) -> list[dict]:
        """基于 replicas × cpu_per_replica 计算每个组件的理论吞吐上限（req/tick）。

        返回 list[dict]，每个 dict 包含：
          - component: 组件名
          - capacity: 理论吞吐上限
          - is_bottleneck: bool，是否是所有组件中容量最低的
        """
        estimates = []
        for comp in components:
            replicas = comp.get("replicas", 1)
            cpu_per_replica = comp.get("cpu_per_replica", 1)
            capacity = replicas * cpu_per_replica
            estimates.append({
                "component": comp["name"],
                "capacity": capacity,
                "is_bottleneck": False,
            })

        if estimates:
            min_capacity = min(e["capacity"] for e in estimates)
            for e in estimates:
                e["is_bottleneck"] = (e["capacity"] == min_capacity)

        return estimates

    def _estimate_latency(self, components: list) -> dict:
        """基于依赖链深度 × timeout_ms 估算端到端延迟下限。

        找到从 gateway（或入口组件）到最深层叶节点的最长路径，
        累加路径上各组件的 timeout_ms。

        返回 dict：
          - estimated_ms: 总延迟下限
          - critical_path: 经过的组件名列表
        """
        comp_by_name = {c["name"]: c for c in components}

        # 入口组件：优先 gateway 类型；否则取没有被任何组件依赖的组件
        entries = [c for c in components if c.get("type") == "gateway"]
        if not entries:
            depended_on = set()
            for c in components:
                for dep in c.get("dependencies", []):
                    depended_on.add(dep)
            entries = [c for c in components if c["name"] not in depended_on]
        if not entries:
            entries = list(components)

        def dfs(name: str, visited: set):
            """返回从 name 出发到最深叶节点的 (路径, 累计 timeout_ms)。"""
            comp = comp_by_name.get(name)
            if comp is None or name in visited:
                return [], 0
            visited = visited | {name}
            timeout = comp.get("timeout_ms", 1000)
            deps = comp.get("dependencies", [])
            if not deps:
                return [name], timeout
            best_path: list = []
            best_total = 0
            for dep in deps:
                dep_path, dep_total = dfs(dep, visited)
                if dep_total > best_total:
                    best_total = dep_total
                    best_path = dep_path
            return [name] + best_path, timeout + best_total

        best_path: list = []
        best_total = -1
        for entry in entries:
            path, total = dfs(entry["name"], set())
            if total > best_total:
                best_total = total
                best_path = path

        return {
            "estimated_ms": best_total if best_total >= 0 else 0,
            "critical_path": best_path,
        }

    def _predict_spof_risk(self, components: list) -> list[dict]:
        """纯拓扑分析：检查所有 replicas=1 且被其他组件依赖的组件。

        返回 list[dict]，每个 dict 包含：
          - component: 组件名
          - dependents: 依赖此组件的组件名列表
          - risk_level: "high"（被 3+ 个组件依赖）或 "medium"（被 1-2 个依赖）
        """
        risks = []
        for comp in components:
            if comp.get("replicas", 1) != 1:
                continue
            name = comp["name"]
            dependents = [c["name"] for c in components
                          if name in c.get("dependencies", [])]
            if not dependents:
                continue
            risk_level = "high" if len(dependents) >= 3 else "medium"
            risks.append({
                "component": name,
                "dependents": dependents,
                "risk_level": risk_level,
            })
        return risks


def format_decision_tree_for_llm(report: DecisionTreeReport, design_spec: dict) -> str:
    """
    将决策树分析结果格式化为LLM可理解的文本。
    这段文本会和模拟结果一起发给LLM，让LLM同时分析
    "学生的设计选择"和"这些选择导致的后果"。
    """
    lines = []
    lines.append("## 学生的设计决策树分析\n")

    # 概念覆盖
    lines.append(f"### 知识覆盖率: {report.coverage_score * 100:.0f}%\n")
    lines.append(f"- 展现出理解的概念: {', '.join(report.concepts_demonstrated) or '无'}")
    lines.append(f"- 完全未考虑的概念: {', '.join(report.concepts_missing) or '无'}")
    lines.append(f"- 考虑了但配置不当: {', '.join(report.concepts_partial) or '无'}\n")

    # 关键决策详情
    lines.append("### 关键设计决策\n")
    for d in report.decisions:
        if d.status != "considered":
            lines.append(f"- **{d.component}.{d.parameter}={d.value}** [{d.status}]")
            lines.append(f"  - 推断: {d.reasoning}")
            if d.blind_spot:
                lines.append(f"  - 认知盲区: {d.blind_spot}")
            if d.related_concepts:
                lines.append(f"  - 应连带考虑: {', '.join(d.related_concepts)}")
            lines.append("")

    # 矛盾
    if report.contradictions:
        lines.append("### 设计中的自相矛盾\n")
        for c in report.contradictions:
            lines.append(f"- **{c['type']}**: {c['description']}")
            lines.append(f"  - 含义: {c['implication']}\n")

    # 推理链断裂
    if report.reasoning_gaps:
        lines.append("### 推理链断裂\n")
        for g in report.reasoning_gaps:
            lines.append(f"- **{g['gap']}**: {g['description']}")
            lines.append(f"  - {g['implication']}\n")

    return "\n".join(lines)
