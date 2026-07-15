"""
策略执行引擎单元测试
====================
覆盖 4 种基础策略动作（degrade, circuit_break, retry_with_backoff, queue_buffer）的执行，
以及策略拦截器在依赖失败、队列溢出和超时场景下的触发逻辑。
"""

import pytest
from modules.simulation_engine import SandboxEngine, ComponentType, EventType
from utils.design_spec import ComponentConfig
from modules.domain_pack import Scenario, Phase

# 构造一个简单的测试场景，只有 1 个 phase，rps=1，持续 10 ticks
TEST_SCENARIO = Scenario(
    id="test_scenario",
    name="Test Scenario",
    description="For testing strategies",
    phases=[Phase(ticks=10, rps=1)],
    inject=[]
)

def create_engine(components_dict):
    """辅助函数：创建引擎并注入测试场景"""
    # 包装成 dict 格式，因为 SandboxEngine 接受 dict
    comps = {name: c for name, c in components_dict.items()}
    return SandboxEngine(components=comps, scenario=TEST_SCENARIO)

def test_degrade_strategy_success():
    """测试 degrade 策略：依赖失败时，成功降级到 fallback_target。"""
    strategies = [
        {
            "trigger": {"metric": "dependency_failed"},
            "action": "degrade",
            "target": "cache",
            "description": "降级到缓存"
        }
    ]
    components = {
        "gateway": ComponentConfig(name="gateway", type="gateway", dependencies=["svc"]),
        "svc": ComponentConfig(name="svc", type="service", dependencies=["db"], strategies=strategies),
        "db": ComponentConfig(name="db", type="database", replicas=0), # db 宕机，触发依赖失败
        "cache": ComponentConfig(name="cache", type="cache", replicas=1) # cache 健康，降级目标
    }
    engine = create_engine(components)
    report = engine.run(ticks=5)
    
    # 验证请求是否成功
    assert report["successful"] > 0
    assert report["failed"] == 0
    
    # 验证是否记录了 STRATEGY_TRIGGERED 事件
    strategy_events = [e for e in report["key_events"] if e["type"] == "strategy_triggered"]
    assert len(strategy_events) > 0
    event = strategy_events[0]
    assert event["component"] == "svc"
    assert "degrade" in event["detail"]
    assert "cache" in event["detail"]

def test_degrade_strategy_fallback_down():
    """测试 degrade 策略：fallback_target 也不可用时，回退到原有失败逻辑。"""
    strategies = [
        {
            "trigger": {"metric": "dependency_failed"},
            "action": "degrade",
            "target": "cache",
            "description": "降级到缓存"
        }
    ]
    components = {
        "gateway": ComponentConfig(name="gateway", type="gateway", dependencies=["svc"]),
        "svc": ComponentConfig(name="svc", type="service", dependencies=["db"], strategies=strategies),
        "db": ComponentConfig(name="db", type="database", replicas=0), # db 宕机
        "cache": ComponentConfig(name="cache", type="cache", replicas=0) # cache 也宕机
    }
    engine = create_engine(components)
    report = engine.run(ticks=5)
    
    # 验证请求是否失败
    assert report["successful"] == 0
    assert report["failed"] > 0
    
    # 验证没有记录 STRATEGY_TRIGGERED 事件（因为策略执行失败）
    strategy_events = [e for e in report["key_events"] if e["type"] == "strategy_triggered"]
    assert len(strategy_events) == 0

def test_circuit_break_strategy():
    """测试 circuit_break 策略：连续失败达到阈值时打开熔断器。"""
    strategies = [
        {
            "trigger": {"metric": "consecutive_failures", "threshold": 2},
            "action": "circuit_break",
            "params": {"recovery_ticks": 5},
            "description": "连续失败 2 次则熔断"
        }
    ]
    components = {
        "gateway": ComponentConfig(name="gateway", type="gateway", dependencies=["svc"]),
        "svc": ComponentConfig(name="svc", type="service", dependencies=["db"], strategies=strategies),
        "db": ComponentConfig(name="db", type="database", replicas=0) # db 宕机，引发 svc 失败
    }
    engine = create_engine(components)
    
    # 运行 2 ticks，产生 2 次失败，应该触发熔断
    engine.run(ticks=2)
    svc_comp = engine.components["svc"]
    assert svc_comp.circuit_open is True
    assert svc_comp.circuit_open_until == engine.current_tick + 5
    
    report = engine.get_report()
    strategy_events = [e for e in report["key_events"] if e["type"] == "strategy_triggered"]
    assert len(strategy_events) > 0
    assert "circuit_break" in strategy_events[0]["detail"]

def test_retry_with_backoff_strategy():
    """测试 retry_with_backoff 策略：计算退避延迟并重试。"""
    strategies = [
        {
            "trigger": {"metric": "dependency_failed"},
            "action": "retry_with_backoff",
            "params": {"max_retries": 2, "base_delay_ms": 100},
            "description": "指数退避重试"
        }
    ]
    components = {
        "gateway": ComponentConfig(name="gateway", type="gateway", dependencies=["svc"]),
        "svc": ComponentConfig(name="svc", type="service", dependencies=["db"], strategies=strategies),
        "db": ComponentConfig(name="db", type="database", replicas=0) # db 宕机
    }
    engine = create_engine(components)
    
    # 运行 1 tick，产生 1 个请求，遇到 db 宕机，应该触发重试
    engine.run(ticks=1)
    svc_comp = engine.components["svc"]
    
    # 请求应该被放回队列
    assert len(svc_comp.queue) == 1
    req = svc_comp.queue[0]
    assert req.retry_attempt == 1
    # base_delay_ms=100, TICK_MS=100, attempt=0 -> delay_ticks = max(1, 100 * 2^0 / 100) = 1
    # ready_tick = current_tick(1) + 1 = 2
    assert req.ready_tick == 2
    
    report = engine.get_report()
    strategy_events = [e for e in report["key_events"] if e["type"] == "strategy_triggered"]
    assert len(strategy_events) > 0
    assert "retry_with_backoff" in strategy_events[0]["detail"]

def test_queue_buffer_strategy_on_overflow():
    """测试 queue_buffer 策略：队列溢出时转入消息队列。"""
    strategies = [
        {
            "trigger": {"metric": "queue_full"},
            "action": "queue_buffer",
            "target": "mq",
            "description": "溢出转入 MQ"
        }
    ]
    components = {
        "gateway": ComponentConfig(name="gateway", type="gateway", queue_capacity=0, strategies=strategies, dependencies=["svc"]),
        "svc": ComponentConfig(name="svc", type="service"),
        "mq": ComponentConfig(name="mq", type="message_queue", queue_capacity=10)
    }
    engine = create_engine(components)
    
    # 运行 1 tick，产生 1 个请求，因为 gateway queue_capacity=0，直接溢出
    engine.run(ticks=1)
    mq_comp = engine.components["mq"]
    
    # 请求应该转入了 mq 的 in_flight_async
    assert len(mq_comp.in_flight_async) == 1
    
    report = engine.get_report()
    strategy_events = [e for e in report["key_events"] if e["type"] == "strategy_triggered"]
    assert len(strategy_events) > 0
    assert "queue_buffer" in strategy_events[0]["detail"]

    def test_timeout_degrade_strategy():
        """测试 timeout 触发器的 degrade 策略。"""
        strategies = [
            {
                "trigger": {"metric": "timeout"},
                "action": "degrade",
                "target": "cache",
                "description": "超时降级"
            }
        ]
        components = {
            "gateway": ComponentConfig(name="gateway", type="gateway", dependencies=["svc"], timeout_ms=100), # 超时时间 1 tick
            "svc": ComponentConfig(name="svc", type="service", cpu_per_replica=0, strategies=strategies, timeout_ms=100), # cpu=0 导致请求一直排队
            "cache": ComponentConfig(name="cache", type="cache", replicas=1)
        }
        engine = create_engine(components)
    
        # 运行 2 ticks，第 1 个 tick 请求进入 svc 队列，第 2 个 tick 超时检查触发
        engine.run(ticks=2)
    
        report = engine.get_report()
        # 因为配置了超时降级，请求应该被算作成功
        assert report["successful"] > 0
        assert report["failed"] == 0
    
        strategy_events = [e for e in report["key_events"] if e["type"] == "strategy_triggered"]
        assert len(strategy_events) > 0
        assert "timeout" in strategy_events[0]["detail"]
        assert "degrade" in strategy_events[0]["detail"]
