"""
Failure Analyzer
================
独立的失败模式分析模块，从 SandboxEngine._analyze_failures 拆分而来。

检测 8 种失败模式：
  - retry_storm:                   重试风暴（total_retried > 50）
  - spof_cascade:                  单点故障级联（replicas==1 且崩溃且被依赖）
  - queue_overflow:                队列溢出（事件日志中有 QUEUE_OVERFLOW）
  - missing_circuit_breaker:       有依赖、失败多、无熔断器
  - memory_exhaustion:             crash_history 中 reason=memory_exhausted
  - missing_rate_limiter:          网关无 rate_limiter 且总请求 > 1000
  - consumer_lag_accumulation:     消息队列积压（message_queue dropped_messages > 0）
  - scheduled_task_thundering_herd: 定时任务羊群效应（2+ 个 scheduled_task 且 jitter=0）

设计原则：
  - 不依赖 simulation_engine 模块，避免循环导入
  - 通过字符串比较识别事件类型（如 "queue_overflow"），兼容 Enum 与字符串
  - 无状态：接收 components 和 events 作为参数
"""


def _event_type_str(event_type) -> str:
    """获取事件类型的字符串表示，兼容 Enum 和字符串。"""
    return event_type.value if hasattr(event_type, "value") else str(event_type)


def _is_gateway(comp) -> bool:
    """判断组件是否为网关类型，兼容 ComponentType 枚举与字符串。"""
    t = comp.type
    if hasattr(t, "value"):
        t = t.value
    return t == "gateway"


def _is_message_queue(comp) -> bool:
    """判断组件是否为消息队列类型，兼容 ComponentType 枚举与字符串。"""
    t = comp.type
    if hasattr(t, "value"):
        t = t.value
    return t == "message_queue"


def _is_scheduled_task(comp) -> bool:
    """判断组件是否为定时任务类型，兼容 ComponentType 枚举与字符串。"""
    t = comp.type
    if hasattr(t, "value"):
        t = t.value
    return t == "scheduled_task"


class FailureAnalyzer:
    """分析模拟结果中的失败模式。

    从 SandboxEngine._analyze_failures 提取而来，6 种检测规则原样保留，
    另新增 2 种 async 相关失败模式（consumer_lag_accumulation、
    scheduled_task_thundering_herd）。
    """

    def analyze(self, components: dict, events: list, get_gateway_fn=None) -> list[dict]:
        """分析失败模式。

        Args:
            components:    {name: Component} 字典
            events:        SimEvent 列表
            get_gateway_fn: 可选的网关获取函数；为 None 时自动从 components 查找

        Returns:
            failure_modes 列表（格式与原 _analyze_failures 输出一致）
        """
        modes = []

        # 1. 重试风暴
        retry_comps = [(name, comp) for name, comp in components.items()
                       if comp.total_retried > 50]
        for name, comp in retry_comps:
            modes.append({
                "mode": "retry_storm",
                "severity": "high",
                "component": name,
                "evidence": f"{name} 产生 {comp.total_retried} 次重试（配置 retry_count={comp.retry_count}）",
                "design_issue": f"{name} 的 retry_count={comp.retry_count}，依赖故障时放大 {comp.retry_count+1} 倍负载",
                "suggestion": "降低 retry_count、添加指数退避、或增加熔断器防止重试风暴"
            })

        # 2. SPOF 级联
        for name, comp in components.items():
            if comp.replicas == 1 and len(comp.crash_history) > 0:
                dependents = [other.name for other in components.values()
                              if name in other.dependencies]
                if dependents:
                    modes.append({
                        "mode": "spof_cascade",
                        "severity": "critical",
                        "component": name,
                        "evidence": f"{name} 仅 1 副本且崩溃 {len(comp.crash_history)} 次，{len(dependents)} 个服务依赖它",
                        "design_issue": f"{name} 是单点故障（SPOF），崩溃后 {len(dependents)} 个下游服务全部受影响",
                        "suggestion": f"为 {name} 增加副本（至少2个）或添加降级策略"
                    })

        # 3. 队列溢出（字符串比较，不导入 EventType）
        overflow_events = [e for e in events
                           if _event_type_str(e.event_type) == "queue_overflow"]
        if overflow_events:
            overflow_comps = set(e.component for e in overflow_events)
            for comp_name in overflow_comps:
                comp = components[comp_name]
                modes.append({
                    "mode": "queue_overflow",
                    "severity": "medium",
                    "component": comp_name,
                    "evidence": f"{comp_name} 发生 {len([e for e in overflow_events if e.component == comp_name])} 次队列溢出（容量 {comp.queue_capacity}）",
                    "design_issue": f"{comp_name} 队列容量不足且无限流保护",
                    "suggestion": "增大 queue_capacity 或添加 rate_limiter"
                })

        # 4. 缺少熔断器
        no_cb = [(name, comp) for name, comp in components.items()
                 if not comp.has_circuit_breaker and comp.total_failed > 50
                 and comp.dependencies]
        for name, comp in no_cb:
            modes.append({
                "mode": "missing_circuit_breaker",
                "severity": "high",
                "component": name,
                "evidence": f"{name} 失败 {comp.total_failed} 次但无熔断器",
                "design_issue": f"{name} 依赖其他服务但无熔断器，故障时持续重试导致资源耗尽",
                "suggestion": f"为 {name} 添加 circuit_breaker"
            })

        # 5. 内存耗尽
        for name, comp in components.items():
            mem_crashes = [c for c in comp.crash_history if c["reason"] == "memory_exhausted"]
            if mem_crashes:
                modes.append({
                    "mode": "memory_exhaustion",
                    "severity": "high",
                    "component": name,
                    "evidence": f"{name} 因内存耗尽崩溃 {len(mem_crashes)} 次",
                    "design_issue": f"{name} memory_limit={comp.memory_limit}MB 不足",
                    "suggestion": "增大 memory_limit 或减少并发"
                })

        # 6. 无限流
        if get_gateway_fn is not None:
            gateway = get_gateway_fn()
        else:
            gateway = self._find_gateway(components)
        if gateway and not gateway.has_rate_limiter:
            total_in = gateway.total_processed + gateway.total_failed
            if total_in > 1000:
                modes.append({
                    "mode": "missing_rate_limiter",
                    "severity": "medium",
                    "component": gateway.name,
                    "evidence": f"网关无限流，直接承受全部 {total_in} 次请求流量",
                    "design_issue": "网关没有 rate_limiter，洪峰流量直接冲击下游服务",
                    "suggestion": "在网关添加 rate_limiter 限制入站流量"
                })

        # 7. 消费者积压（message_queue dropped_messages > 0）
        for name, comp in components.items():
            if not _is_message_queue(comp):
                continue
            dropped = getattr(comp, "dropped_messages", 0)
            if dropped > 0:
                queue_depth = len(getattr(comp, "in_flight_async", []) or [])
                modes.append({
                    "mode": "consumer_lag_accumulation",
                    "component": name,
                    "severity": "high" if dropped > 10 else "medium",
                    "evidence": f"{name} 消息积压导致 {dropped} 条消息被丢弃，最大积压深度 {queue_depth}",
                    "concept": "async_decoupling",
                    "suggestion": "增加消费者副本数或降低 consumer_lag",
                })

        # 8. 定时任务羊群效应（2+ 个 scheduled_task 且 jitter=0 且有触发记录）
        scheduled_tasks = [(name, comp) for name, comp in components.items()
                           if _is_scheduled_task(comp)]
        if len(scheduled_tasks) >= 2:
            all_no_jitter = all(getattr(comp, "jitter", 0) == 0
                                for _, comp in scheduled_tasks)
            any_triggered = any(getattr(comp, "trigger_count", 0) > 0
                                for _, comp in scheduled_tasks)
            if all_no_jitter and any_triggered:
                n = len(scheduled_tasks)
                modes.append({
                    "mode": "scheduled_task_thundering_herd",
                    "component": scheduled_tasks[0][0],
                    "severity": "medium",
                    "evidence": f"{n} 个定时任务同时触发且无 jitter，可能产生流量尖峰",
                    "concept": "scheduled_trigger",
                    "suggestion": "为定时任务添加 jitter 以错峰执行",
                })

        return modes

    @staticmethod
    def _find_gateway(components: dict):
        """从 components 中查找类型为 gateway 的组件。"""
        for comp in components.values():
            if _is_gateway(comp):
                return comp
        return None
