"""软件系统设计 - 预设学生设计模板

每个设计代表一个学生在"设计在线商城系统"作业中可能提交的方案。
设计中的缺陷是真实的——学生常见的认知盲区。

从 demo/student_designs.py 迁移，用 ComponentConfig 替代 demo 的 Component。
"""

from utils.design_spec import ComponentConfig, DesignSpec


def design_novice() -> DesignSpec:
    """初学者设计：典型的新手方案

    认知盲区：
    - 数据库只有1个副本（不知道SPOF概念）
    - 所有服务retry=3（不知道重试风暴）
    - 没有任何熔断器（不知道circuit breaker）
    - 网关没有限流（不知道rate limiting）
    - Payment Service只有1个副本（关键服务未冗余）
    """
    return DesignSpec(
        domain="software_systems",
        components=[
            ComponentConfig(
                name="api_gateway", type="gateway",
                replicas=2, cpu_per_replica=100, memory_limit=1024,
                queue_capacity=500, timeout_ms=2000, retry_count=0,
                has_circuit_breaker=False, has_rate_limiter=False, rate_limit=0,
                dependencies=["order_service", "product_service", "payment_service"],
            ),
            ComponentConfig(
                name="order_service", type="service",
                replicas=2, cpu_per_replica=50, memory_limit=512,
                queue_capacity=200, timeout_ms=1000, retry_count=3,
                has_circuit_breaker=False,
                dependencies=["product_service", "payment_service", "database"],
            ),
            ComponentConfig(
                name="product_service", type="service",
                replicas=2, cpu_per_replica=50, memory_limit=512,
                queue_capacity=200, timeout_ms=800, retry_count=3,
                has_circuit_breaker=False,
                dependencies=["database", "cache"],
            ),
            ComponentConfig(
                name="payment_service", type="service",
                replicas=1, cpu_per_replica=30, memory_limit=256,
                queue_capacity=100, timeout_ms=2000, retry_count=2,
                has_circuit_breaker=False,
                dependencies=["database"],
            ),
            ComponentConfig(
                name="database", type="database",
                replicas=1, cpu_per_replica=40, memory_limit=2048,
                queue_capacity=300, timeout_ms=3000, retry_count=0,
                has_circuit_breaker=False,
                dependencies=[],
            ),
            ComponentConfig(
                name="cache", type="cache",
                replicas=2, cpu_per_replica=80, memory_limit=1024,
                queue_capacity=500, timeout_ms=100, retry_count=0,
                has_circuit_breaker=False,
                dependencies=[],
            ),
        ],
    )


def design_intermediate() -> DesignSpec:
    """中级学生设计：了解一些模式但不够深入

    改进了：
    - 数据库2副本
    - 有熔断器（但配置不当）
    - 网关有限流

    仍有盲区：
    - 熔断器阈值太低，正常流量也会误触发
    - Payment Service retry=5，比新手还激进
    - 缓存和数据库共享连接池（未建模，但timeout设置不合理）
    """
    return DesignSpec(
        domain="software_systems",
        components=[
            ComponentConfig(
                name="api_gateway", type="gateway",
                replicas=2, cpu_per_replica=100, memory_limit=1024,
                queue_capacity=500, timeout_ms=2000, retry_count=0,
                has_circuit_breaker=True, has_rate_limiter=True, rate_limit=200,
                dependencies=["order_service", "product_service", "payment_service"],
            ),
            ComponentConfig(
                name="order_service", type="service",
                replicas=3, cpu_per_replica=50, memory_limit=512,
                queue_capacity=200, timeout_ms=1000, retry_count=2,
                has_circuit_breaker=True,
                dependencies=["product_service", "payment_service", "database"],
            ),
            ComponentConfig(
                name="product_service", type="service",
                replicas=3, cpu_per_replica=50, memory_limit=512,
                queue_capacity=200, timeout_ms=800, retry_count=1,
                has_circuit_breaker=True,
                dependencies=["database", "cache"],
            ),
            ComponentConfig(
                name="payment_service", type="service",
                replicas=2, cpu_per_replica=30, memory_limit=256,
                queue_capacity=100, timeout_ms=2000, retry_count=5,
                has_circuit_breaker=False,  # 问题：支付服务反而没熔断器
                dependencies=["database"],
            ),
            ComponentConfig(
                name="database", type="database",
                replicas=2, cpu_per_replica=40, memory_limit=2048,
                queue_capacity=300, timeout_ms=3000, retry_count=0,
                has_circuit_breaker=False,
                dependencies=[],
            ),
            ComponentConfig(
                name="cache", type="cache",
                replicas=2, cpu_per_replica=80, memory_limit=1024,
                queue_capacity=500, timeout_ms=100, retry_count=0,
                has_circuit_breaker=False,
                dependencies=[],
            ),
        ],
    )


def design_expert() -> DesignSpec:
    """专家级设计：基本健壮的方案

    仍有细微问题：
    - 数据库降级策略缺失
    - 缓存失效时会有thundering herd
    """
    return DesignSpec(
        domain="software_systems",
        components=[
            ComponentConfig(
                name="api_gateway", type="gateway",
                replicas=3, cpu_per_replica=100, memory_limit=1024,
                queue_capacity=1000, timeout_ms=2000, retry_count=0,
                has_circuit_breaker=True, has_rate_limiter=True, rate_limit=250,
                dependencies=["order_service", "product_service", "payment_service"],
            ),
            ComponentConfig(
                name="order_service", type="service",
                replicas=3, cpu_per_replica=50, memory_limit=512,
                queue_capacity=300, timeout_ms=1000, retry_count=1,
                has_circuit_breaker=True,
                dependencies=["product_service", "payment_service", "database", "cache"],
            ),
            ComponentConfig(
                name="product_service", type="service",
                replicas=3, cpu_per_replica=50, memory_limit=512,
                queue_capacity=300, timeout_ms=800, retry_count=1,
                has_circuit_breaker=True,
                dependencies=["database", "cache"],
            ),
            ComponentConfig(
                name="payment_service", type="service",
                replicas=3, cpu_per_replica=30, memory_limit=512,
                queue_capacity=200, timeout_ms=2000, retry_count=1,
                has_circuit_breaker=True,
                dependencies=["database"],
            ),
            ComponentConfig(
                name="database", type="database",
                replicas=2, cpu_per_replica=60, memory_limit=4096,
                queue_capacity=500, timeout_ms=3000, retry_count=0,
                has_circuit_breaker=False,
                dependencies=[],
            ),
            ComponentConfig(
                name="cache", type="cache",
                replicas=3, cpu_per_replica=100, memory_limit=2048,
                queue_capacity=1000, timeout_ms=100, retry_count=0,
                has_circuit_breaker=True,
                dependencies=[],
            ),
        ],
    )


# 模板名 → 构造函数
PRESETS = {
    "初学者模板": design_novice,
    "中级模板": design_intermediate,
    "专家模板": design_expert,
}
