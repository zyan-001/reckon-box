"""
ReckonBox 2.0 性能引擎
=====================
基于现有 SandboxEngine 重构，核心改变是 _process_component
从硬编码分支改为策略模板分发。

与 SandboxEngine 的区别：
  - Component 新增 micro_skill / dependency_edges 字段，删除 strategies
  - _process_component 通过 _dispatch_by_strategy 分发到 6 个策略处理函数
  - 依赖检查使用 DependencyEdge 语义（sync 传播失败，async 阻断级联）
  - 新增断言检查机制，触发时记录 ASSERTION_TRIGGERED 事件
  - get_report 新增 strategy_dispatch_info / assertion_events

复用现有引擎的数据结构和部分机制：
  - ComponentType / HealthStatus / EventType 枚举（新增 ASSERTION_TRIGGERED）
  - Component / Request / SimEvent dataclass（修改字段）
  - TICK_MS / log_event / get_gateway
  - generate_incoming_requests / apply_injects
  - step 循环主框架
"""

from dataclasses import dataclass, field, fields
from enum import Enum
from typing import Optional
from collections import defaultdict, deque

from utils.micro_skill import MicroSkill, default_skill, from_dict as micro_skill_from_dict
from utils.dependency_edge import DependencyEdge, from_dict as edge_from_dict, is_sync, is_async
from utils.design_spec import parse_dep


# ---------------------------------------------------------------------------
# 枚举定义
# ---------------------------------------------------------------------------

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
    # 新增：断言触发事件
    ASSERTION_TRIGGERED = "assertion_triggered"


# ---------------------------------------------------------------------------
# 数据类定义
# ---------------------------------------------------------------------------

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
    # message_queue / scheduled_task 专用配置
    consumer_lag: int = 0
    cron_interval: int = 10
    jitter: int = 0

    # 新增字段
    micro_skill: MicroSkill | None = None
    dependency_edges: list = field(default_factory=list)  # list[DependencyEdge]

    # 运行时状态
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
    # async 依赖暂存队列：元素为 (request, ready_tick)
    in_flight_async: list = field(default_factory=list)
    dropped_messages: int = 0
    trigger_count: int = 0
    # circuit_break 策略的连续失败计数（与 has_circuit_breaker 独立）
    consecutive_failures: int = 0


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


# ---------------------------------------------------------------------------
# 策略分发映射表
# ---------------------------------------------------------------------------

_STRATEGY_DISPATCH = {}  # 在类定义后填充


# ---------------------------------------------------------------------------
# 性能引擎
# ---------------------------------------------------------------------------

class PerformanceEngine:
    TICK_MS = 100

    def __init__(self, components: dict, scenario):
        """初始化性能引擎。

        Args:
            components: {name: Component | dict} 组件字典。
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
        # 断言触发事件列表
        self.assertion_events: list[SimEvent] = []

        for comp in self.components.values():
            comp.healthy_replicas = comp.replicas

    @staticmethod
    def _normalize_components(components: dict) -> dict:
        """将 components dict 中的值统一转为 Component 对象。

        兼容两种输入形式：
          - Component 对象（保持不变）
          - dict（用 **kwargs 构造 Component，处理 micro_skill 和 dependency_edges）
        """
        normalized = {}
        for name, comp in components.items():
            if isinstance(comp, Component):
                normalized[name] = comp
                continue

            # dict 形式
            if isinstance(comp, dict):
                data = dict(comp)
            else:
                # ComponentConfig 或其他带属性的对象
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
                    "micro_skill": getattr(comp, "micro_skill", None),
                }

            # 处理 micro_skill：dict → MicroSkill
            if "micro_skill" in data and isinstance(data["micro_skill"], dict):
                data["micro_skill"] = micro_skill_from_dict(data["micro_skill"])

            # 处理 dependency_edges：dict → DependencyEdge
            if "dependency_edges" in data and isinstance(data["dependency_edges"], list):
                converted = []
                for edge in data["dependency_edges"]:
                    if isinstance(edge, DependencyEdge):
                        converted.append(edge)
                    elif isinstance(edge, dict):
                        converted.append(edge_from_dict(edge))
                data["dependency_edges"] = converted

            kwargs = {k: v for k, v in data.items() if k in _COMPONENT_FIELDS}
            # type 字段可能是字符串，转为 ComponentType 枚举
            if isinstance(kwargs.get("type"), str):
                kwargs["type"] = ComponentType(kwargs["type"])
            normalized[name] = Component(**kwargs)
        return normalized

    # ------------------------------------------------------------------
    # 事件日志
    # ------------------------------------------------------------------

    def log_event(self, event_type, component, detail, request_id=None):
        self.events.append(SimEvent(
            tick=self.current_tick, event_type=event_type,
            component=component, detail=detail, request_id=request_id
        ))

    def _log_assertion(self, component, crash_risk, detail, request_id=None):
        """记录断言触发事件，同时加入 assertion_events 列表。"""
        event = SimEvent(
            tick=self.current_tick,
            event_type=EventType.ASSERTION_TRIGGERED,
            component=component,
            detail=f"断言触发: 崩溃风险={crash_risk}, {detail}",
            request_id=request_id,
        )
        self.events.append(event)
        self.assertion_events.append(event)

    # ------------------------------------------------------------------
    # 网关和请求生成
    # ------------------------------------------------------------------

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
        if self.scenario.phases:
            return self.scenario.phases[-1].rps
        return 0

    # ------------------------------------------------------------------
    # 故障注入
    # ------------------------------------------------------------------

    def apply_injects(self):
        """通用故障注入处理器，根据 scenario.inject 配置执行。"""
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

    # ------------------------------------------------------------------
    # 依赖边语义级联
    # ------------------------------------------------------------------

    def _check_dependency_edges(self, comp: Component) -> tuple[bool, str | None]:
        """基于 DependencyEdge 检查依赖链。

        Returns:
            (all_ok, failed_dep_name)
            - all_ok=True: 所有 sync 依赖健康，async 依赖写入成功
            - all_ok=False: 存在不健康的 sync 依赖目标
        """
        # 若组件有 dependency_edges，优先使用
        if comp.dependency_edges:
            for edge in comp.dependency_edges:
                target = self.components.get(edge.target)
                if is_async(edge):
                    # async 边：写入目标的 in_flight_async 即返回，不阻塞调用方
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
                            # 为 async 写入创建新请求
                            self.request_counter += 1
                            async_req = Request(
                                req_id=self.request_counter,
                                arrival_tick=self.current_tick,
                                target_component=target.name,
                                current_component=target.name,
                                remaining_timeout=target.timeout_ms // self.TICK_MS,
                            )
                            target.in_flight_async.append((async_req, ready_tick))
                else:
                    # sync 边：目标不健康 → 当前请求失败
                    if not target or target.healthy_replicas == 0 or target.circuit_open:
                        return (False, edge.target)
            return (True, None)

        # 回退到旧版 dependencies 字符串列表（兼容）
        for dep in comp.dependencies:
            dep_name, mode = parse_dep(dep)
            target = self.components.get(dep_name)
            if mode == "async" or (target is not None and target.type == ComponentType.MESSAGE_QUEUE):
                # async 依赖：写入即返回
                if target is not None:
                    if len(target.in_flight_async) >= target.queue_capacity:
                        target.dropped_messages += 1
                        self.log_event(
                            EventType.QUEUE_OVERFLOW, target.name,
                            f"{target.name} 异步队列已满，丢弃消息",
                        )
                    else:
                        ready_tick = self.current_tick + target.consumer_lag
                        self.request_counter += 1
                        async_req = Request(
                            req_id=self.request_counter,
                            arrival_tick=self.current_tick,
                            target_component=target.name,
                            current_component=target.name,
                            remaining_timeout=target.timeout_ms // self.TICK_MS,
                        )
                        target.in_flight_async.append((async_req, ready_tick))
            else:
                # sync 依赖
                if not target or target.healthy_replicas == 0 or target.circuit_open:
                    return (False, dep_name)
        return (True, None)

    # ------------------------------------------------------------------
    # 策略分发
    # ------------------------------------------------------------------

    def _dispatch_by_strategy(self, comp: Component, req: Request) -> bool:
        """根据 comp.micro_skill.strategy_template_id 分发到对应的处理函数。

        micro_skill 为 None 时默认 sync_direct。
        Returns:
            True 表示请求成功处理，False 表示请求失败。
        """
        template_id = comp.micro_skill.strategy_template_id if comp.micro_skill else "sync_direct"
        handler = _STRATEGY_DISPATCH.get(template_id, PerformanceEngine._process_sync_direct)
        return handler(self, comp, req)

    # ------------------------------------------------------------------
    # 6 个策略处理函数
    # ------------------------------------------------------------------

    def _process_sync_direct(self, comp: Component, req: Request) -> bool:
        """sync_direct：依赖全健康 → 成功；任一依赖不健康 → 立即失败。"""
        deps_ok, failed_dep = self._check_dependency_edges(comp)
        if deps_ok:
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
            return True
        else:
            comp.total_failed += 1
            comp.failure_count += 1
            self.log_event(
                EventType.REQUEST_FAIL, comp.name,
                f"{comp.name}: 依赖 {failed_dep} 不可用，请求 #{req.req_id} 立即失败",
                request_id=req.req_id
            )
            self.failed_requests.append(req)
            self._check_circuit_breaker(comp)
            return False

    def _process_retry_no_backoff(self, comp: Component, req: Request) -> bool:
        """retry_no_backoff：依赖不健康 → 立即重试（放回队列）；超过 max_retries → 失败。"""
        max_retries = comp.micro_skill.params.get("max_retries", 3) if comp.micro_skill else 3

        deps_ok, failed_dep = self._check_dependency_edges(comp)
        if deps_ok:
            comp.total_processed += 1
            comp.success_count += 1
            comp.failure_count = 0
            comp.consecutive_failures = 0
            req.path.append(comp.name)
            self.log_event(
                EventType.REQUEST_SUCCESS, comp.name,
                f"请求 #{req.req_id} 在 {comp.name} 处理成功",
                request_id=req.req_id
            )
            self.completed_requests.append(req)
            return True

        # 依赖不健康，重试
        if req.retry_attempt < max_retries:
            req.retry_attempt += 1
            comp.total_retried += 1
            comp.failure_count += 1
            comp.consecutive_failures += 1
            self.log_event(
                EventType.REQUEST_RETRY, comp.name,
                f"{comp.name}: 依赖 {failed_dep} 不可用，"
                f"请求 #{req.req_id} 第 {req.retry_attempt}/{max_retries} 次重试（无退避）",
                request_id=req.req_id
            )
            # 立即重试：放回队列（下一 tick 处理）
            comp.queue.append(req)
            self._check_circuit_breaker(comp)

            # 检查 retry_storm 断言：重试次数接近上限时触发
            if req.retry_attempt >= max_retries:
                self._log_assertion(
                    comp.name, "retry_storm",
                    f"{comp.name} 重试耗尽（{req.retry_attempt}/{max_retries}），可能引发重试风暴",
                    request_id=req.req_id
                )
            return False
        else:
            # 超过最大重试次数 → 失败
            comp.total_failed += 1
            comp.failure_count += 1
            comp.consecutive_failures += 1
            self.log_event(
                EventType.REQUEST_FAIL, comp.name,
                f"{comp.name}: 依赖 {failed_dep} 不可用，"
                f"请求 #{req.req_id} 重试 {req.retry_attempt} 次后失败",
                request_id=req.req_id
            )
            self.failed_requests.append(req)
            self._check_circuit_breaker(comp)
            return False

    def _process_retry_with_backoff(self, comp: Component, req: Request) -> bool:
        """retry_with_backoff：依赖不健康 → 设置 ready_tick 延迟重试；超过 max_retries → 失败。"""
        max_retries = comp.micro_skill.params.get("max_retries", 3) if comp.micro_skill else 3
        base_delay_ms = comp.micro_skill.params.get("base_delay_ms", 100) if comp.micro_skill else 100

        deps_ok, failed_dep = self._check_dependency_edges(comp)
        if deps_ok:
            comp.total_processed += 1
            comp.success_count += 1
            comp.failure_count = 0
            comp.consecutive_failures = 0
            req.path.append(comp.name)
            self.log_event(
                EventType.REQUEST_SUCCESS, comp.name,
                f"请求 #{req.req_id} 在 {comp.name} 处理成功",
                request_id=req.req_id
            )
            self.completed_requests.append(req)
            return True

        # 依赖不健康，指数退避重试
        if req.retry_attempt < max_retries:
            attempt = req.retry_attempt
            delay_ticks = max(1, (base_delay_ms * (2 ** attempt)) // self.TICK_MS)
            req.ready_tick = self.current_tick + delay_ticks
            req.retry_attempt += 1
            comp.total_retried += 1
            comp.failure_count += 1
            comp.consecutive_failures += 1
            self.log_event(
                EventType.STRATEGY_TRIGGERED, comp.name,
                f"{comp.name}: 依赖 {failed_dep} 不可用，"
                f"请求 #{req.req_id} 第 {req.retry_attempt}/{max_retries} 次退避重试"
                f"（延迟 {delay_ticks} ticks）",
                request_id=req.req_id
            )
            comp.queue.append(req)
            self._check_circuit_breaker(comp)
            return False
        else:
            comp.total_failed += 1
            comp.failure_count += 1
            comp.consecutive_failures += 1
            self.log_event(
                EventType.REQUEST_FAIL, comp.name,
                f"{comp.name}: 依赖 {failed_dep} 不可用，"
                f"请求 #{req.req_id} 退避重试 {req.retry_attempt} 次后失败",
                request_id=req.req_id
            )
            self.failed_requests.append(req)
            self._check_circuit_breaker(comp)
            return False

    def _process_async_fire_and_forget(self, comp: Component, req: Request) -> bool:
        """async_fire_and_forget：写入依赖的 async 队列即返回成功（不阻塞）。"""
        # 先处理 async 依赖写入
        deps_ok, _ = self._check_dependency_edges(comp)
        # async 策略下，写入即返回成功，即使依赖不健康
        comp.total_processed += 1
        comp.success_count += 1
        comp.failure_count = 0
        req.path.append(comp.name)
        self.log_event(
            EventType.REQUEST_SUCCESS, comp.name,
            f"请求 #{req.req_id} 在 {comp.name} 异步写入成功",
            request_id=req.req_id
        )
        self.completed_requests.append(req)
        return True

    def _process_degrade_fallback(self, comp: Component, req: Request) -> bool:
        """degrade_fallback：依赖不健康 → 检查 fallback_target；可用 → 成功；不可用 → 失败。"""
        fallback_target = comp.micro_skill.params.get("fallback_target", "") if comp.micro_skill else ""

        deps_ok, failed_dep = self._check_dependency_edges(comp)
        if deps_ok:
            comp.total_processed += 1
            comp.success_count += 1
            comp.failure_count = 0
            comp.consecutive_failures = 0
            req.path.append(comp.name)
            self.log_event(
                EventType.REQUEST_SUCCESS, comp.name,
                f"请求 #{req.req_id} 在 {comp.name} 处理成功",
                request_id=req.req_id
            )
            self.completed_requests.append(req)
            return True

        # 依赖不健康，检查 fallback
        if fallback_target:
            target = self.components.get(fallback_target)
            if target and target.healthy_replicas > 0 and not target.circuit_open:
                # 降级成功
                comp.total_processed += 1
                comp.success_count += 1
                comp.failure_count = 0
                req.path.append(comp.name)
                self.log_event(
                    EventType.STRATEGY_TRIGGERED, comp.name,
                    f"{comp.name}: 依赖 {failed_dep} 不可用，降级到 {fallback_target}，请求 #{req.req_id} 成功",
                    request_id=req.req_id
                )
                self.completed_requests.append(req)
                return True

        # fallback 不可用或不存在 → 失败，触发 cascading_failure 断言
        comp.total_failed += 1
        comp.failure_count += 1
        comp.consecutive_failures += 1
        self.log_event(
            EventType.REQUEST_FAIL, comp.name,
            f"{comp.name}: 依赖 {failed_dep} 不可用且无可用 fallback，请求 #{req.req_id} 失败",
            request_id=req.req_id
        )
        self.failed_requests.append(req)
        self._check_circuit_breaker(comp)

        # 触发 cascading_failure 断言
        self._log_assertion(
            comp.name, "cascading_failure",
            f"{comp.name} 依赖 {failed_dep} 不可用且 fallback {fallback_target} 不可用，可能级联故障",
            request_id=req.req_id
        )
        return False

    def _process_circuit_break(self, comp: Component, req: Request) -> bool:
        """circuit_break：连续失败达到 failure_threshold → 打开熔断器；熔断期间 fast fail。"""
        failure_threshold = comp.micro_skill.params.get("failure_threshold", 5) if comp.micro_skill else 5
        recovery_ticks = comp.micro_skill.params.get("recovery_ticks", 10) if comp.micro_skill else 10

        # 熔断器已打开 → fast fail
        if comp.circuit_open:
            comp.total_failed += 1
            comp.failure_count += 1
            self.log_event(
                EventType.REQUEST_FAIL, comp.name,
                f"{comp.name} 熔断器打开，请求 #{req.req_id} fast fail",
                request_id=req.req_id
            )
            self.failed_requests.append(req)
            return False

        deps_ok, failed_dep = self._check_dependency_edges(comp)
        if deps_ok:
            comp.total_processed += 1
            comp.success_count += 1
            comp.failure_count = 0
            comp.consecutive_failures = 0
            req.path.append(comp.name)
            self.log_event(
                EventType.REQUEST_SUCCESS, comp.name,
                f"请求 #{req.req_id} 在 {comp.name} 处理成功",
                request_id=req.req_id
            )
            self.completed_requests.append(req)
            return True

        # 依赖不健康 → 连续失败计数
        comp.consecutive_failures += 1
        comp.failure_count += 1

        # 检查是否达到熔断阈值
        if comp.consecutive_failures >= failure_threshold:
            comp.circuit_open = True
            comp.circuit_open_until = self.current_tick + recovery_ticks
            self.log_event(
                EventType.CIRCUIT_BREAKER_OPEN, comp.name,
                f"{comp.name} 熔断器触发！连续失败 {comp.consecutive_failures} 次，"
                f"暂停 {recovery_ticks * self.TICK_MS}ms"
            )
            comp.total_failed += 1
            self.failed_requests.append(req)
            self.log_event(
                EventType.REQUEST_FAIL, comp.name,
                f"{comp.name}: 依赖 {failed_dep} 不可用，请求 #{req.req_id} 失败（触发熔断）",
                request_id=req.req_id
            )
            return False

        # 未达熔断阈值，普通失败
        comp.total_failed += 1
        self.log_event(
            EventType.REQUEST_FAIL, comp.name,
            f"{comp.name}: 依赖 {failed_dep} 不可用，请求 #{req.req_id} 失败"
            f"（连续失败 {comp.consecutive_failures}/{failure_threshold}）",
            request_id=req.req_id
        )
        self.failed_requests.append(req)
        self._check_circuit_breaker(comp)
        return False

    # ------------------------------------------------------------------
    # 组件处理
    # ------------------------------------------------------------------

    def _process_component(self, comp: Component):
        """处理组件队列中的请求（策略模板分发版）。"""
        # 消息队列：每 tick 从 in_flight_async 取出就绪消息，分发给消费者
        if comp.type == ComponentType.MESSAGE_QUEUE:
            ready = []
            remaining = []
            for msg, ready_tick in comp.in_flight_async:
                if self.current_tick >= ready_tick:
                    ready.append(msg)
                else:
                    remaining.append((msg, ready_tick))
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
                    remaining.append((msg, self.current_tick))
            comp.in_flight_async = remaining

        # 定时任务：每 tick 检查触发条件
        if comp.type == ComponentType.SCHEDULED_TASK:
            cron_interval = comp.cron_interval
            jitter = comp.jitter
            if jitter > 0:
                effective_interval = cron_interval + (jitter * hash(comp.name)) % (jitter + 1)
            else:
                effective_interval = cron_interval
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
                    req.current_component = req.target_component
                    req.path.append(comp.name)
                    target.queue.append(req)
                    comp.total_processed += 1
                    comp.success_count += 1
                    comp.failure_count = 0
                else:
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
                # 服务/数据库/缓存：通过策略模板分发处理
                self._dispatch_by_strategy(comp, req)

        # 未处理的请求放回队列
        while comp.queue:
            new_queue.append(comp.queue.popleft())
        comp.queue = new_queue

    # ------------------------------------------------------------------
    # 熔断器检查
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # 目标选择
    # ------------------------------------------------------------------

    def _pick_target(self):
        services = [name for name, c in self.components.items()
                    if c.type == ComponentType.SERVICE]
        if not services:
            return list(self.components.keys())[-1]
        return services[self.request_counter % len(services)]

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------

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

        # 3. 超时检查
        for comp in self.components.values():
            still_queued = deque()
            while comp.queue:
                req = comp.queue.popleft()
                if req.ready_tick > self.current_tick:
                    still_queued.append(req)
                    continue
                req.remaining_timeout -= 1
                if req.remaining_timeout <= 0:
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
                comp.consecutive_failures = 0
                self.log_event(
                    EventType.CIRCUIT_BREAKER_CLOSE, comp.name,
                    f"{comp.name} 熔断器恢复"
                )

        # 5. 副本自动恢复
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

    def run(self, ticks=100):
        for _ in range(ticks):
            self.step()
        return self.get_report()

    # ------------------------------------------------------------------
    # 报告输出
    # ------------------------------------------------------------------

    def get_report(self):
        from modules.failure_analyzer import FailureAnalyzer

        total = len(self.completed_requests) + len(self.failed_requests)
        success_rate = (len(self.completed_requests) / total * 100) if total > 0 else 0

        analyzer = FailureAnalyzer()
        failure_modes = analyzer.analyze(self.components, self.events)

        component_stats = {}
        # 策略分派信息
        strategy_dispatch_info = {}

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

            # 策略分派信息
            strategy_dispatch_info[name] = (
                comp.micro_skill.strategy_template_id
                if comp.micro_skill
                else "sync_direct"
            )

        return {
            "scenario": self.scenario_name,
            "total_ticks": self.current_tick,
            "total_requests": total,
            "successful": len(self.completed_requests),
            "failed": len(self.failed_requests),
            "success_rate": round(success_rate, 1),
            "component_stats": component_stats,
            "failure_modes": failure_modes,
            "strategy_dispatch_info": strategy_dispatch_info,
            "assertion_events": [
                {"tick": e.tick, "type": e.event_type.value,
                 "component": e.component, "detail": e.detail}
                for e in self.assertion_events
            ],
            "key_events": [
                {"tick": e.tick, "type": e.event_type.value,
                 "component": e.component, "detail": e.detail}
                for e in self.events
                if e.event_type in (
                    EventType.COMPONENT_CRASH,
                    EventType.CIRCUIT_BREAKER_OPEN,
                    EventType.QUEUE_OVERFLOW,
                    EventType.REQUEST_TIMEOUT,
                    EventType.DEPENDENCY_FAIL,
                    EventType.STRATEGY_TRIGGERED,
                    EventType.ASSERTION_TRIGGERED,
                )
            ],
            "all_events_count": len(self.events),
        }


# ---------------------------------------------------------------------------
# 填充策略分发映射表
# ---------------------------------------------------------------------------

_STRATEGY_DISPATCH = {
    "sync_direct": PerformanceEngine._process_sync_direct,
    "retry_no_backoff": PerformanceEngine._process_retry_no_backoff,
    "retry_with_backoff": PerformanceEngine._process_retry_with_backoff,
    "async_fire_and_forget": PerformanceEngine._process_async_fire_and_forget,
    "degrade_fallback": PerformanceEngine._process_degrade_fallback,
    "circuit_break": PerformanceEngine._process_circuit_break,
}
