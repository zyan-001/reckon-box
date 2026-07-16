"""
ReckonBox Simulation Engine (YAML-driven)
=========================================
从 demo/reckon_sandbox.py 迁移并重构为 YAML 驱动。

核心改动（相对 demo 版本）：
  - __init__ 接收 Scenario 对象（来自 domain_pack）而非 scenario_name 字符串
  - generate_incoming_requests 从 scenario.phases 查找 rps，删除所有硬编码分支
  - apply_injects 替代原来的 inject_db_latency / partial_failure crash 逻辑
  - components 兼容 Component / ComponentConfig / dict 三种形式
  - 失败分析拆分到 modules.failure_analyzer.FailureAnalyzer（延迟导入避免循环）

保持不变的部分：
  - ComponentType / HealthStatus / EventType 枚举
  - Component / Request / SimEvent dataclass（所有字段和运行时状态）
  - 核心模拟机制：step / _process_component / _check_circuit_breaker /
    _check_dependency_chain / _pick_target / log_event / get_gateway
  - 事件类型和所有状态转换规则
  - get_report 的输出结构

请求流转模型：
  用户请求 → 网关队列 → 网关路由到目标服务队列 → 服务处理(检查依赖链)
    → 依赖全健康: 成功
    → 依赖不健康: 重试(回到自己的队列) 或 失败
"""

from dataclasses import dataclass, field, fields
from enum import Enum
from typing import Optional
from collections import defaultdict, deque

from utils.design_spec import parse_dep


class ComponentType(Enum):
    GATEWAY = "gateway"
    SERVICE = "service"
    DATABASE = "database"
    CACHE = "cache"
    QUEUE = "queue"
    MESSAGE_QUEUE = "message_queue"
    SCHEDULED_TASK = "scheduled_task"


class HealthStatus(Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DOWN = "down"


class EventType(Enum):
    REQUEST_ARRIVE = "request_arrive"
    REQUEST_SUCCESS = "request_success"
    REQUEST_FAIL = "request_fail"
    REQUEST_TIMEOUT = "request_timeout"
    REQUEST_RETRY = "request_retry"
    QUEUE_OVERFLOW = "queue_overflow"
    COMPONENT_CRASH = "component_crash"
    CIRCUIT_BREAKER_OPEN = "circuit_breaker_open"
    CIRCUIT_BREAKER_CLOSE = "circuit_breaker_close"
    DEPENDENCY_FAIL = "dependency_fail"
    RESOURCE_EXHAUSTED = "resource_exhausted"
    REPLICA_RECOVER = "replica_recover"
    RATE_LIMITED = "rate_limited"
    STRATEGY_TRIGGERED = "strategy_triggered"


@dataclass
class Component:
    name: str
    type: ComponentType
    replicas: int = 1
    cpu_per_replica: int = 50
    memory_limit: int = 512
    memory_per_request: int = 5
    queue_capacity: int = 200
    timeout_ms: int = 1000
    retry_count: int = 0
    has_circuit_breaker: bool = False
    has_rate_limiter: bool = False
    rate_limit: int = 0
    dependencies: list = field(default_factory=list)
    # message_queue / scheduled_task 专用配置（其他类型使用默认值，不影响行为）
    consumer_lag: int = 0
    cron_interval: int = 10
    jitter: int = 0

    # Runtime state
    healthy_replicas: int = 0
    queue: deque = field(default_factory=deque)
    in_flight: int = 0
    memory_used: int = 0
    failure_count: int = 0
    success_count: int = 0
    circuit_open: bool = False
    circuit_open_until: int = 0
    total_processed: int = 0
    total_failed: int = 0
    total_retried: int = 0
    total_rated_limited: int = 0
    crash_history: list = field(default_factory=list)
    # async 依赖暂存队列：元素为 (request, ready_tick)，ready_tick = 写入tick + consumer_lag
    in_flight_async: list = field(default_factory=list)
    dropped_messages: int = 0
    trigger_count: int = 0
    strategies: list = field(default_factory=list)


@dataclass
class Request:
    req_id: int
    arrival_tick: int
    target_component: str
    current_component: str
    remaining_timeout: int
    retry_attempt: int = 0
    path: list = field(default_factory=list)
    ready_tick: int = 0


@dataclass
class SimEvent:
    tick: int
    event_type: EventType
    component: str
    detail: str
    request_id: Optional[int] = None


# Component 配置字段集合（dict → Component 转换时用于过滤无效键）
_COMPONENT_FIELDS = {f.name for f in fields(Component)}


class SandboxEngine:
    TICK_MS = 100

    def __init__(self, components: dict, scenario):
        """初始化引擎。

        Args:
            components: {name: Component | ComponentConfig | dict} 组件字典。
                        非 Component 对象会被 _normalize_components 转换。
            scenario:   domain_pack.Scenario 对象，提供 phases 和 inject 配置。
        """
        self.scenario = scenario
        self.scenario_name = scenario.id
        self.components = self._normalize_components(components)
        self.current_tick = 0
        self.events: list[SimEvent] = []
        self.request_counter = 0
        self.completed_requests: list[Request] = []
        self.failed_requests: list[Request] = []
        self.component_timeline: dict = defaultdict(list)

        for comp in self.components.values():
            comp.healthy_replicas = comp.replicas

    @staticmethod
    def _normalize_components(components: dict) -> dict:
        """将 components dict 中的值统一转为 Component 对象。

        兼容三种输入形式：
          - Component 对象（保持不变）
          - ComponentConfig 对象（字段名一致，type 需转为 ComponentType）
          - dict（用 **kwargs 构造 Component，type 字段转为 ComponentType）

        dict / ComponentConfig 中不在 Component 字段集合内的键会被忽略。
        """
        normalized = {}
        for name, comp in components.items():
            if isinstance(comp, Component):
                normalized[name] = comp
                continue

            if isinstance(comp, dict):
                data = comp
            else:
                # ComponentConfig 或其他带属性的对象：读取配置字段
                data = {
                    "name": comp.name,
                    "type": comp.type,
                    "replicas": comp.replicas,
                    "cpu_per_replica": comp.cpu_per_replica,
                    "memory_limit": comp.memory_limit,
                    "memory_per_request": comp.memory_per_request,
                    "queue_capacity": comp.queue_capacity,
                    "timeout_ms": comp.timeout_ms,
                    "retry_count": comp.retry_count,
                    "has_circuit_breaker": comp.has_circuit_breaker,
                    "has_rate_limiter": comp.has_rate_limiter,
                    "rate_limit": comp.rate_limit,
                    "dependencies": comp.dependencies,
                    "strategies": getattr(comp, "strategies", []),
                }

            kwargs = {k: v for k, v in data.items() if k in _COMPONENT_FIELDS}
            # type 字段可能是字符串（如 "gateway"），转为 ComponentType 枚举
            if isinstance(kwargs.get("type"), str):
                kwargs["type"] = ComponentType(kwargs["type"])
            normalized[name] = Component(**kwargs)
        return normalized

    def log_event(self, event_type, component, detail, request_id=None):
        self.events.append(SimEvent(
            tick=self.current_tick, event_type=event_type,
            component=component, detail=detail, request_id=request_id
        ))

    def get_gateway(self):
        for comp in self.components.values():
            if comp.type == ComponentType.GATEWAY:
                return comp
        return None

    def generate_incoming_requests(self, tick):
        """从 scenario.phases 查找当前 tick 对应的 rps。"""
        elapsed = 0
        for phase in self.scenario.phases:
            if tick <= elapsed + phase.ticks:
                return phase.rps
            elapsed += phase.ticks
        # 超过所有 phase 范围，返回最后一个 phase 的 rps 或 0
        if self.scenario.phases:
            return self.scenario.phases[-1].rps
        return 0

    def apply_injects(self):
        """通用故障注入处理器，根据 scenario.inject 配置执行。

        支持的 action：
          - degrade_cpu: 从 start_tick 开始，每 interval ticks 将 target 的
                         cpu_per_replica 递减 amount（下限 5）
          - crash_all:   在 at_tick 时将 target 的 healthy_replicas 置 0
        """
        if not self.scenario.inject:
            return
        for inj in self.scenario.inject:
            if inj.action == "degrade_cpu":
                if self.current_tick >= inj.start_tick and inj.interval and \
                   (self.current_tick - inj.start_tick) % inj.interval == 0:
                    target = self.components.get(inj.target)
                    if target:
                        target.cpu_per_replica = max(5, target.cpu_per_replica - inj.amount)
            elif inj.action == "crash_all":
                if self.current_tick == inj.at_tick:
                    target = self.components.get(inj.target)
                    if target:
                        target.healthy_replicas = 0
                        self.log_event(
                            EventType.COMPONENT_CRASH, inj.target,
                            f"{inj.target} 全部 {target.replicas} 个副本崩溃（模拟故障注入）"
                        )

    def _check_dependency_chain(self, comp_name: str, visited: set = None) -> bool:
        """递归检查组件及其所有依赖是否健康"""
        if visited is None:
            visited = set()
        if comp_name in visited:
            return True  # 循环依赖，视为可用
        visited.add(comp_name)

        comp = self.components.get(comp_name)
        if not comp:
            return False
        if comp.healthy_replicas == 0:
            return False
        if comp.circuit_open:
            return False

        for dep in comp.dependencies:
            dep_name, _ = parse_dep(dep)
            if not self._check_dependency_chain(dep_name, visited):
                return False
        return True

    def step(self):
        self.current_tick += 1
        self.apply_injects()

        # 1. 生成新请求 → 加入网关队列
        new_count = self.generate_incoming_requests(self.current_tick)
        gateway = self.get_gateway()
        if gateway:
            for _ in range(new_count):
                self.request_counter += 1
                req = Request(
                    req_id=self.request_counter,
                    arrival_tick=self.current_tick,
                    target_component=self._pick_target(),
                    current_component=gateway.name,
                    remaining_timeout=gateway.timeout_ms // self.TICK_MS,
                )
                # 网关限流检查
                if gateway.has_rate_limiter and gateway.rate_limit > 0:
                    if len(gateway.queue) >= gateway.rate_limit:
                        gateway.total_rated_limited += 1
                        gateway.total_failed += 1
                        self.failed_requests.append(req)
                        continue
                gateway.queue.append(req)

        # 2. 处理每个组件的队列
        for comp in self.components.values():
            self._process_component(comp)

        # 3. 超时检查：遍历所有队列中的请求
        for comp in self.components.values():
            still_queued = deque()
            while comp.queue:
                req = comp.queue.popleft()
                # 跳过未到 ready_tick 的请求
                if req.ready_tick > self.current_tick:
                    still_queued.append(req)
                    continue
                req.remaining_timeout -= 1
                if req.remaining_timeout <= 0:
                    # === 策略拦截器：超时 ===
                    timeout_rule = self._find_strategy(comp, "timeout")
                    if timeout_rule and timeout_rule.get("action") == "degrade":
                        if self._execute_degrade(comp, req, timeout_rule, trigger_metric="timeout"):
                            continue  # 降级成功，不丢弃
                    # === 原有超时丢弃逻辑 ===
                    comp.total_failed += 1
                    comp.failure_count += 1
                    self.log_event(
                        EventType.REQUEST_TIMEOUT, comp.name,
                        f"请求 #{req.req_id} 超时（在 {comp.name} 队列中等待过久）",
                        request_id=req.req_id
                    )
                    self.failed_requests.append(req)
                    self._check_circuit_breaker(comp)
                else:
                    still_queued.append(req)
            comp.queue = still_queued

        # 4. 熔断器恢复
        for comp in self.components.values():
            if comp.circuit_open and self.current_tick >= comp.circuit_open_until:
                comp.circuit_open = False
                comp.failure_count = 0
                self.log_event(
                    EventType.CIRCUIT_BREAKER_CLOSE, comp.name,
                    f"{comp.name} 熔断器恢复"
                )

        # 5. 副本自动恢复（模拟K8s重启）
        for comp in self.components.values():
            if comp.healthy_replicas < comp.replicas and self.current_tick % 10 == 0:
                comp.healthy_replicas += 1
                self.log_event(
                    EventType.REPLICA_RECOVER, comp.name,
                    f"{comp.name} 副本恢复: {comp.healthy_replicas}/{comp.replicas}"
                )

        # 6. 记录状态快照
        for name, comp in self.components.items():
            self.component_timeline[name].append({
                "tick": self.current_tick,
                "healthy_replicas": comp.healthy_replicas,
                "queue_size": len(comp.queue),
                "in_flight": comp.in_flight,
                "memory_used": comp.memory_used,
                "circuit_open": comp.circuit_open,
                "total_processed": comp.total_processed,
                "total_failed": comp.total_failed,
            })

    def _pick_target(self):
        services = [name for name, c in self.components.items()
                    if c.type == ComponentType.SERVICE]
        if not services:
            return list(self.components.keys())[-1]
        return services[self.request_counter % len(services)]

    def _process_component(self, comp: Component):
        """处理组件队列中的请求"""
        # 消息队列：每 tick 从 in_flight_async 取出就绪消息（按 consumer_lag 延迟），分发给消费者
        if comp.type == ComponentType.MESSAGE_QUEUE:
            ready = []
            remaining = []
            for msg, ready_tick in comp.in_flight_async:
                if self.current_tick >= ready_tick:
                    ready.append(msg)
                else:
                    remaining.append((msg, ready_tick))
            # 分发就绪消息给消费者（dependencies）；无可用消费者时保留为积压（backlog）
            for msg in ready:
                delivered = False
                for dep in comp.dependencies:
                    consumer_name, _ = parse_dep(dep)
                    consumer = self.components.get(consumer_name)
                    if consumer and consumer.healthy_replicas > 0 and not consumer.circuit_open:
                        self.request_counter += 1
                        new_req = Request(
                            req_id=self.request_counter,
                            arrival_tick=self.current_tick,
                            target_component=consumer.name,
                            current_component=consumer.name,
                            remaining_timeout=consumer.timeout_ms // self.TICK_MS,
                        )
                        consumer.queue.append(new_req)
                        delivered = True
                if not delivered:
                    # 消费者均不可用，消息保留为积压（下一 tick 重试分发）
                    remaining.append((msg, self.current_tick))
            comp.in_flight_async = remaining

        # 定时任务：每 tick 检查触发条件，触发时生成内部请求注入 dependencies
        if comp.type == ComponentType.SCHEDULED_TASK:
            cron_interval = comp.cron_interval
            jitter = comp.jitter
            if jitter > 0:
                # 确定性偏移：相同输入产生相同输出（不使用 random，避免破坏确定性）
                effective_interval = cron_interval + (jitter * hash(comp.name)) % (jitter + 1)
            else:
                effective_interval = cron_interval
            # tick 从 1 开始：(tick - 1) % effective_interval == 0 时触发
            if (self.current_tick - 1) % effective_interval == 0:
                comp.trigger_count += 1
                for dep in comp.dependencies:
                    target_name, _ = parse_dep(dep)
                    target = self.components.get(target_name)
                    if not target or target.healthy_replicas == 0 or target.circuit_open:
                        continue
                    self.request_counter += 1
                    new_req = Request(
                        req_id=self.request_counter,
                        arrival_tick=self.current_tick,
                        target_component=target.name,
                        current_component=target.name,
                        remaining_timeout=target.timeout_ms // self.TICK_MS,
                    )
                    target.queue.append(new_req)

        # 熔断器打开 → 拒绝所有
        if comp.circuit_open:
            while comp.queue:
                req = comp.queue.popleft()
                comp.total_failed += 1
                self.failed_requests.append(req)
            return

        # 内存检查
        comp.in_flight = len(comp.queue)
        comp.memory_used = comp.in_flight * comp.memory_per_request
        if comp.memory_used > comp.memory_limit and comp.healthy_replicas > 0:
            comp.healthy_replicas -= 1
            comp.crash_history.append({
                "tick": self.current_tick,
                "reason": "memory_exhausted",
                "memory_used": comp.memory_used,
                "limit": comp.memory_limit
            })
            self.log_event(
                EventType.COMPONENT_CRASH, comp.name,
                f"{comp.name} 内存溢出崩溃！使用 {comp.memory_used}MB / 限制 {comp.memory_limit}MB，"
                f"剩余: {comp.healthy_replicas}/{comp.replicas}"
            )
            if comp.healthy_replicas == 0:
                self.log_event(
                    EventType.COMPONENT_CRASH, comp.name,
                    f"{comp.name} 所有副本崩溃！完全不可用"
                )

        # 队列溢出检查
        if len(comp.queue) > comp.queue_capacity:
            overflow = len(comp.queue) - comp.queue_capacity
            for _ in range(overflow):
                req = comp.queue.pop()
                # === 策略拦截器：队列溢出 ===
                buffer_rule = self._find_strategy(comp, "queue_full")
                if buffer_rule and buffer_rule.get("action") == "queue_buffer":
                    if self._execute_queue_buffer(comp, req, buffer_rule):
                        continue  # 请求已转入缓冲，不丢弃
                # === 原有丢弃逻辑 ===
                comp.total_failed += 1
                comp.failure_count += 1
                self.log_event(
                    EventType.QUEUE_OVERFLOW, comp.name,
                    f"{comp.name} 队列溢出！{len(comp.queue)+overflow}/{comp.queue_capacity}，丢弃 #{req.req_id}",
                    request_id=req.req_id
                )
                self.failed_requests.append(req)
            self._check_circuit_breaker(comp)

        # 计算处理能力
        capacity = comp.healthy_replicas * comp.cpu_per_replica
        if capacity == 0:
            # 组件down，拒绝所有排队请求
            while comp.queue:
                req = comp.queue.popleft()
                comp.total_failed += 1
                comp.failure_count += 1
                self.failed_requests.append(req)
            self._check_circuit_breaker(comp)
            return

        # 处理队列中的请求
        processed = 0
        new_queue = deque()

        while comp.queue and processed < capacity:
            req = comp.queue.popleft()
            processed += 1

            # 跳过未到 ready_tick 的请求（retry_with_backoff 延迟）
            if req.ready_tick > self.current_tick:
                new_queue.append(req)
                continue

            if comp.type == ComponentType.GATEWAY:
                # 网关：路由请求到目标服务
                target = self.components.get(req.target_component)
                if target and target.healthy_replicas > 0 and not target.circuit_open:
                    # 目标可用，将请求转发到目标服务队列
                    req.current_component = req.target_component
                    req.path.append(comp.name)
                    target.queue.append(req)
                    comp.total_processed += 1
                    comp.success_count += 1
                    comp.failure_count = 0
                else:
                    # 目标不可用
                    comp.total_failed += 1
                    comp.failure_count += 1
                    self.log_event(
                        EventType.DEPENDENCY_FAIL, comp.name,
                        f"网关: 目标 {req.target_component} 不可用，请求 #{req.req_id} 失败",
                        request_id=req.req_id
                    )
                    self.failed_requests.append(req)
                    self._check_circuit_breaker(comp)
            else:
                # 服务/数据库/缓存：处理请求，检查依赖链
                deps_ok = True
                failed_dep = None
                for dep in comp.dependencies:
                    target_name, mode = parse_dep(dep)
                    target = self.components.get(target_name)
                    if mode == "async" or (target is not None and target.type == ComponentType.MESSAGE_QUEUE):
                        # async 依赖：写入目标的 in_flight_async 后立即返回，不阻塞调用方
                        if target is not None:
                            if len(target.in_flight_async) >= target.queue_capacity:
                                target.dropped_messages += 1
                                self.log_event(
                                    EventType.QUEUE_OVERFLOW, target.name,
                                    f"{target.name} 异步队列已满 "
                                    f"({len(target.in_flight_async)}/{target.queue_capacity})，丢弃消息",
                                )
                            else:
                                ready_tick = self.current_tick + target.consumer_lag
                                target.in_flight_async.append((req, ready_tick))
                    else:
                        # sync 依赖：现有逻辑不变——等待被调用方处理完成
                        if not target or target.healthy_replicas == 0 or target.circuit_open:
                            deps_ok = False
                            failed_dep = target_name
                            break

                if deps_ok:
                    # 依赖全部健康 → 请求成功
                    comp.total_processed += 1
                    comp.success_count += 1
                    comp.failure_count = 0
                    req.path.append(comp.name)
                    self.log_event(
                        EventType.REQUEST_SUCCESS, comp.name,
                        f"请求 #{req.req_id} 在 {comp.name} 处理成功",
                        request_id=req.req_id
                    )
                    self.completed_requests.append(req)
                else:
                    # === 策略拦截器：依赖失败 ===
                    strategy_handled = False

                    # 检查 degrade 策略
                    degrade_rule = self._find_strategy(comp, "dependency_failed")
                    if degrade_rule and degrade_rule.get("action") == "degrade":
                        if self._execute_degrade(comp, req, degrade_rule):
                            strategy_handled = True

                    # 检查 retry_with_backoff 策略
                    if not strategy_handled:
                        retry_rule = self._find_strategy(comp, "dependency_failed")
                        if retry_rule and retry_rule.get("action") == "retry_with_backoff":
                            if self._execute_retry_with_backoff(comp, req, retry_rule):
                                strategy_handled = True

                    # 检查 queue_buffer 策略
                    if not strategy_handled:
                        buffer_rule = self._find_strategy(comp, "dependency_failed")
                        if buffer_rule and buffer_rule.get("action") == "queue_buffer":
                            if self._execute_queue_buffer(comp, req, buffer_rule):
                                strategy_handled = True

                    # 检查 circuit_break 策略（基于 consecutive_failures）
                    if not strategy_handled:
                        cb_rule = self._find_strategy(comp, "consecutive_failures")
                        if cb_rule and cb_rule.get("action") == "circuit_break":
                            threshold = cb_rule.get("trigger", {}).get("threshold", 5)
                            if comp.failure_count + 1 >= threshold:
                                self._execute_circuit_break(comp, req, cb_rule)
                                # 熔断后请求失败
                                comp.total_failed += 1
                                comp.failure_count += 1
                                self.failed_requests.append(req)
                                strategy_handled = True

                    if not strategy_handled:
                        # === 原有失败逻辑 ===
                        if req.retry_attempt < comp.retry_count:
                            req.retry_attempt += 1
                            comp.total_retried += 1
                            comp.failure_count += 1
                            self.log_event(
                                EventType.REQUEST_RETRY, comp.name,
                                f"{comp.name}: 依赖 {failed_dep} 不可用，"
                                f"请求 #{req.req_id} 第 {req.retry_attempt}/{comp.retry_count} 次重试",
                                request_id=req.req_id
                            )
                            # 重试放回自己的队列（下一tick处理）
                            new_queue.append(req)
                            self._check_circuit_breaker(comp)
                        else:
                            comp.total_failed += 1
                            comp.failure_count += 1
                            self.log_event(
                                EventType.REQUEST_FAIL, comp.name,
                                f"{comp.name}: 依赖 {failed_dep} 不可用，"
                                f"请求 #{req.req_id} 重试 {req.retry_attempt} 次后失败",
                                request_id=req.req_id
                            )
                            self.failed_requests.append(req)
                            self._check_circuit_breaker(comp)

        # 未处理的请求放回队列
        while comp.queue:
            new_queue.append(comp.queue.popleft())
        comp.queue = new_queue

    def _find_strategy(self, comp, trigger_metric: str):
        """查找匹配触发指标的策略规则。

        Args:
            comp: Component 对象
            trigger_metric: "dependency_failed" | "queue_full" | "consecutive_failures" | "timeout"

        Returns:
            匹配的策略规则 dict，或 None
        """
        for rule in comp.strategies:
            trigger = rule.get("trigger", {})
            if trigger.get("metric") == trigger_metric:
                return rule
        return None

    def _execute_degrade(self, comp, req, rule, trigger_metric="dependency_failed"):
        """执行 degrade 策略：检查 fallback_target 健康，健康则标记请求成功。

        不将请求塞入 fallback_target 的队列。
        Returns: True 表示策略成功，False 表示策略失败需回退原逻辑。
        """
        target_name = rule.get("target")
        if not target_name:
            return False
        target = self.components.get(target_name)
        if not target or target.healthy_replicas <= 0 or target.circuit_open:
            return False
        # 降级成功：标记请求为成功
        comp.total_processed += 1
        comp.success_count += 1
        comp.failure_count = 0
        req.path.append(comp.name)
        self.completed_requests.append(req)
        self._log_strategy_triggered(comp, req, rule, trigger_metric)
        return True

    def _execute_circuit_break(self, comp, req, rule):
        """执行 circuit_break 策略：打开熔断器。

        Returns: True 表示策略已执行（请求应被快速失败），False 表示策略无法执行。
        """
        params = rule.get("params", {})
        recovery_ticks = params.get("recovery_ticks", 10)
        comp.circuit_open = True
        comp.circuit_open_until = self.current_tick + recovery_ticks
        self._log_strategy_triggered(comp, req, rule, "consecutive_failures")
        return True

    def _execute_retry_with_backoff(self, comp, req, rule):
        """执行 retry_with_backoff 策略：指数退避重试。

        Returns: True 表示请求已放回队列等待重试，False 表示策略耗尽。
        """
        params = rule.get("params", {})
        max_retries = params.get("max_retries", 3)
        base_delay_ms = params.get("base_delay_ms", 100)

        if req.retry_attempt >= max_retries:
            return False

        attempt = req.retry_attempt
        delay_ticks = max(1, (base_delay_ms * (2 ** attempt)) // self.TICK_MS)
        req.ready_tick = self.current_tick + delay_ticks
        req.retry_attempt += 1
        comp.total_retried += 1
        # 放回队列（不是 new_queue，确保下一 tick 重新检查 ready_tick）
        comp.queue.append(req)
        self._log_strategy_triggered(comp, req, rule, "dependency_failed")
        return True

    def _execute_queue_buffer(self, comp, req, rule):
        """执行 queue_buffer 策略：将请求转入消息队列。

        Returns: True 表示请求已转入缓冲，False 表示策略失败。
        """
        target_name = rule.get("target")
        if not target_name:
            return False
        target = self.components.get(target_name)
        if not target or target.type != ComponentType.MESSAGE_QUEUE:
            return False
        if len(target.in_flight_async) >= target.queue_capacity:
            return False
        ready_tick = self.current_tick + target.consumer_lag
        target.in_flight_async.append((req, ready_tick))
        self._log_strategy_triggered(comp, req, rule, "queue_full")
        return True

    def _log_strategy_triggered(self, comp, req, rule, trigger_metric):
        """记录 STRATEGY_TRIGGERED 事件，带强语义上下文。"""
        action = rule.get("action", "unknown")
        target = rule.get("target", "")
        description = rule.get("description", "")
        self.log_event(
            EventType.STRATEGY_TRIGGERED, comp.name,
            f"策略触发: 组件={comp.name}, 触发原因={trigger_metric}, "
            f"动作={action}, 目标={target}, 描述=\"{description}\", 请求=#{req.req_id}",
            request_id=req.req_id
        )

    def _check_circuit_breaker(self, comp: Component):
        if not comp.has_circuit_breaker:
            return
        threshold = max(5, comp.queue_capacity // 10)
        if comp.failure_count >= threshold and not comp.circuit_open:
            comp.circuit_open = True
            comp.circuit_open_until = self.current_tick + 10
            self.log_event(
                EventType.CIRCUIT_BREAKER_OPEN, comp.name,
                f"{comp.name} 熔断器触发！连续失败 {comp.failure_count} 次，暂停 {10*self.TICK_MS}ms"
            )

    def run(self, ticks=100):
        for _ in range(ticks):
            self.step()
        return self.get_report()

    def get_report(self):
        # 延迟导入 FailureAnalyzer，避免 simulation_engine ↔ failure_analyzer 循环导入
        from modules.failure_analyzer import FailureAnalyzer

        total = len(self.completed_requests) + len(self.failed_requests)
        success_rate = (len(self.completed_requests) / total * 100) if total > 0 else 0

        analyzer = FailureAnalyzer()
        failure_modes = analyzer.analyze(self.components, self.events)

        component_stats = {}
        for name, comp in self.components.items():
            stat = {
                "type": comp.type.value,
                "replicas": comp.replicas,
                "healthy_replicas": comp.healthy_replicas,
                "total_processed": comp.total_processed,
                "total_failed": comp.total_failed,
                "total_retried": comp.total_retried,
                "total_rate_limited": comp.total_rated_limited,
                "crash_count": len(comp.crash_history),
                "crash_history": comp.crash_history,
                "has_circuit_breaker": comp.has_circuit_breaker,
                "has_rate_limiter": comp.has_rate_limiter,
                "circuit_opened": comp.circuit_open,
                "retry_count_config": comp.retry_count,
                "queue_capacity": comp.queue_capacity,
                "dependencies": comp.dependencies,
            }
            if comp.type == ComponentType.MESSAGE_QUEUE:
                stat["queue_depth"] = len(comp.in_flight_async)
                stat["dropped_messages"] = comp.dropped_messages
            elif comp.type == ComponentType.SCHEDULED_TASK:
                stat["trigger_count"] = comp.trigger_count
            component_stats[name] = stat

        return {
            "scenario": self.scenario_name,
            "total_ticks": self.current_tick,
            "total_requests": total,
            "successful": len(self.completed_requests),
            "failed": len(self.failed_requests),
            "success_rate": round(success_rate, 1),
            "component_stats": component_stats,
            "failure_modes": failure_modes,
            "key_events": [
                {"tick": e.tick, "type": e.event_type.value, "component": e.component, "detail": e.detail}
                for e in self.events
                if e.event_type in (
                    EventType.COMPONENT_CRASH,
                    EventType.CIRCUIT_BREAKER_OPEN,
                    EventType.QUEUE_OVERFLOW,
                    EventType.REQUEST_TIMEOUT,
                    EventType.DEPENDENCY_FAIL,
                    EventType.STRATEGY_TRIGGERED,
                )
            ],
            "all_events_count": len(self.events),
        }
