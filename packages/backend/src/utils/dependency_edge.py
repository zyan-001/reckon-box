"""
依赖边语义模块
==============
定义 DependencyEdge 数据结构，携带组件间通信语义（同步/异步、超时、重试策略）。

用于 ReckonBox 2.0 的组件依赖图，取代旧版仅用字符串 "a:async" 表示依赖的方式。
提供旧格式迁移函数，支持平滑升级。

纯数据结构模块，不依赖任何 LLM 或 Streamlit。
"""

from dataclasses import dataclass, asdict


@dataclass
class DependencyEdge:
    """依赖边，描述源组件到目标组件的通信关系。

    字段：
    - source: 源组件名
    - target: 目标组件名
    - communication_mode: 通信模式，"sync"（同步）或 "async"（异步）
    - timeout_ms: 超时阈值（毫秒），默认 1000
    - retry_strategy: 重试策略，None / "immediate" / "backoff"
    """
    source: str
    target: str
    communication_mode: str
    timeout_ms: int = 1000
    retry_strategy: str | None = None


# ---------------------------------------------------------------------------
# 序列化 / 反序列化
# ---------------------------------------------------------------------------

def to_dict(edge: DependencyEdge) -> dict:
    """将 DependencyEdge 序列化为 dict。"""
    return asdict(edge)


def from_dict(data: dict) -> DependencyEdge:
    """从 dict 反序列化为 DependencyEdge。"""
    return DependencyEdge(
        source=data["source"],
        target=data["target"],
        communication_mode=data["communication_mode"],
        timeout_ms=data.get("timeout_ms", 1000),
        retry_strategy=data.get("retry_strategy", None),
    )


# ---------------------------------------------------------------------------
# 旧格式迁移
# ---------------------------------------------------------------------------

def from_legacy(dep_str: str, source: str) -> DependencyEdge:
    """从旧格式字符串迁移转换为 DependencyEdge。

    支持的格式：
    - "a"          → target="a", communication_mode="sync"
    - "a:async"    → target="a", communication_mode="async"
    - "a:async:backoff" → target="a", communication_mode="async", retry_strategy="backoff"

    边界情况：
    - 空字符串 → target="", communication_mode="sync"
    - 只有冒号（如 ":"） → target="", communication_mode="sync"
    - 格式异常时，尽量提取有效部分，communication_mode 默认 "sync"

    Args:
        dep_str: 旧格式依赖字符串
        source: 源组件名

    Returns:
        转换后的 DependencyEdge
    """
    parts = dep_str.split(":")

    # 目标组件名：取第一段，去除空白
    target = parts[0].strip() if parts else ""

    # 通信模式：取第二段，默认 "sync"
    communication_mode = "sync"
    if len(parts) >= 2 and parts[1].strip():
        communication_mode = parts[1].strip()

    # 重试策略：取第三段，默认 None
    retry_strategy = None
    if len(parts) >= 3 and parts[2].strip():
        retry_strategy = parts[2].strip()

    return DependencyEdge(
        source=source,
        target=target,
        communication_mode=communication_mode,
        retry_strategy=retry_strategy,
    )


def edges_from_legacy_deps(source: str, deps: list[str]) -> list[DependencyEdge]:
    """批量将旧格式依赖列表转换为 DependencyEdge 列表。

    Args:
        source: 源组件名（所有边的源相同）
        deps: 旧格式依赖字符串列表

    Returns:
        DependencyEdge 列表
    """
    return [from_legacy(dep, source) for dep in deps]


# ---------------------------------------------------------------------------
# 便捷判断
# ---------------------------------------------------------------------------

def is_sync(edge: DependencyEdge) -> bool:
    """判断依赖边是否为同步通信。"""
    return edge.communication_mode == "sync"


def is_async(edge: DependencyEdge) -> bool:
    """判断依赖边是否为异步通信。"""
    return edge.communication_mode == "async"
