"""
DependencyEdge 依赖边语义模块测试
=================================
覆盖：创建与字段访问、序列化反序列化往返、旧格式迁移、批量转换、便捷判断、默认值、边界情况。
"""

import pytest

from src.utils.dependency_edge import (
    DependencyEdge,
    to_dict,
    from_dict,
    from_legacy,
    edges_from_legacy_deps,
    is_sync,
    is_async,
)


# ---------------------------------------------------------------------------
# 创建与字段访问
# ---------------------------------------------------------------------------

class TestDependencyEdgeCreation:
    """DependencyEdge 创建和字段访问。"""

    def test_创建基础边(self):
        edge = DependencyEdge(source="A", target="B", communication_mode="sync")
        assert edge.source == "A"
        assert edge.target == "B"
        assert edge.communication_mode == "sync"

    def test_创建异步边(self):
        edge = DependencyEdge(source="A", target="B", communication_mode="async")
        assert edge.communication_mode == "async"

    def test_带重试策略的边(self):
        edge = DependencyEdge(
            source="A", target="B",
            communication_mode="async",
            retry_strategy="backoff",
        )
        assert edge.retry_strategy == "backoff"

    def test_带自定义超时的边(self):
        edge = DependencyEdge(
            source="A", target="B",
            communication_mode="sync",
            timeout_ms=3000,
        )
        assert edge.timeout_ms == 3000


# ---------------------------------------------------------------------------
# 默认值
# ---------------------------------------------------------------------------

class TestDefaults:
    """默认值验证。"""

    def test_timeout_ms默认1000(self):
        edge = DependencyEdge(source="A", target="B", communication_mode="sync")
        assert edge.timeout_ms == 1000

    def test_retry_strategy默认None(self):
        edge = DependencyEdge(source="A", target="B", communication_mode="sync")
        assert edge.retry_strategy is None


# ---------------------------------------------------------------------------
# 序列化 / 反序列化往返
# ---------------------------------------------------------------------------

class TestSerialization:
    """to_dict / from_dict 序列化反序列化往返。"""

    def test_to_dict基础边(self):
        edge = DependencyEdge(source="A", target="B", communication_mode="sync")
        d = to_dict(edge)
        assert d == {
            "source": "A",
            "target": "B",
            "communication_mode": "sync",
            "timeout_ms": 1000,
            "retry_strategy": None,
        }

    def test_to_dict完整边(self):
        edge = DependencyEdge(
            source="X", target="Y",
            communication_mode="async",
            timeout_ms=5000,
            retry_strategy="immediate",
        )
        d = to_dict(edge)
        assert d["source"] == "X"
        assert d["target"] == "Y"
        assert d["communication_mode"] == "async"
        assert d["timeout_ms"] == 5000
        assert d["retry_strategy"] == "immediate"

    def test_from_dict基础边(self):
        data = {
            "source": "A",
            "target": "B",
            "communication_mode": "sync",
        }
        edge = from_dict(data)
        assert edge.source == "A"
        assert edge.target == "B"
        assert edge.communication_mode == "sync"
        assert edge.timeout_ms == 1000
        assert edge.retry_strategy is None

    def test_from_dict完整边(self):
        data = {
            "source": "X",
            "target": "Y",
            "communication_mode": "async",
            "timeout_ms": 2000,
            "retry_strategy": "backoff",
        }
        edge = from_dict(data)
        assert edge.timeout_ms == 2000
        assert edge.retry_strategy == "backoff"

    def test_序列化反序列化往返(self):
        """to_dict → from_dict 应得到等价对象。"""
        original = DependencyEdge(
            source="A", target="B",
            communication_mode="async",
            timeout_ms=2500,
            retry_strategy="immediate",
        )
        roundtrip = from_dict(to_dict(original))
        assert roundtrip == original

    def test_往返保留所有字段(self):
        original = DependencyEdge(
            source="C", target="D",
            communication_mode="sync",
            timeout_ms=500,
            retry_strategy=None,
        )
        roundtrip = from_dict(to_dict(original))
        assert roundtrip.source == original.source
        assert roundtrip.target == original.target
        assert roundtrip.communication_mode == original.communication_mode
        assert roundtrip.timeout_ms == original.timeout_ms
        assert roundtrip.retry_strategy == original.retry_strategy


# ---------------------------------------------------------------------------
# 旧格式迁移 from_legacy
# ---------------------------------------------------------------------------

class TestFromLegacy:
    """from_legacy 旧格式转换。"""

    def test_只有目标名默认sync(self):
        """'a' → target='a', communication_mode='sync'"""
        edge = from_legacy("a", source="src")
        assert edge.source == "src"
        assert edge.target == "a"
        assert edge.communication_mode == "sync"
        assert edge.retry_strategy is None

    def test_两段式async(self):
        """'a:async' → target='a', communication_mode='async'"""
        edge = from_legacy("a:async", source="src")
        assert edge.target == "a"
        assert edge.communication_mode == "async"
        assert edge.retry_strategy is None

    def test_三段式backoff(self):
        """'a:async:backoff' → target='a', communication_mode='async', retry_strategy='backoff'"""
        edge = from_legacy("a:async:backoff", source="src")
        assert edge.target == "a"
        assert edge.communication_mode == "async"
        assert edge.retry_strategy == "backoff"

    def test_三段式immediate(self):
        """'b:sync:immediate' → target='b', communication_mode='sync', retry_strategy='immediate'"""
        edge = from_legacy("b:sync:immediate", source="src")
        assert edge.target == "b"
        assert edge.communication_mode == "sync"
        assert edge.retry_strategy == "immediate"

    def test_源组件名正确传递(self):
        edge = from_legacy("db:async", source="api")
        assert edge.source == "api"

    def test_from_legacy默认timeout_ms(self):
        edge = from_legacy("a:async", source="src")
        assert edge.timeout_ms == 1000


# ---------------------------------------------------------------------------
# from_legacy 边界情况
# ---------------------------------------------------------------------------

class TestFromLegacyEdgeCases:
    """from_legacy 边界情况处理。"""

    def test_空字符串(self):
        """空字符串 → target='', communication_mode='sync'"""
        edge = from_legacy("", source="src")
        assert edge.target == ""
        assert edge.communication_mode == "sync"
        assert edge.retry_strategy is None

    def test_只有冒号(self):
        """只有冒号 ':' → target='', communication_mode='sync'"""
        edge = from_legacy(":", source="src")
        assert edge.target == ""
        assert edge.communication_mode == "sync"

    def test_双冒号(self):
        """'::' → target='', communication_mode='sync', retry_strategy=None"""
        edge = from_legacy("::", source="src")
        assert edge.target == ""
        assert edge.communication_mode == "sync"
        assert edge.retry_strategy is None

    def test_目标名带空格(self):
        """前后空格应被去除"""
        edge = from_legacy(" redis : async ", source="src")
        assert edge.target == "redis"
        assert edge.communication_mode == "async"

    def test_多余段忽略(self):
        """四段式只取前三段"""
        edge = from_legacy("a:async:backoff:extra", source="src")
        assert edge.target == "a"
        assert edge.communication_mode == "async"
        assert edge.retry_strategy == "backoff"


# ---------------------------------------------------------------------------
# 批量转换 edges_from_legacy_deps
# ---------------------------------------------------------------------------

class TestEdgesFromLegacyDeps:
    """edges_from_legacy_deps 批量转换。"""

    def test_空列表(self):
        result = edges_from_legacy_deps("src", [])
        assert result == []

    def test_单元素(self):
        result = edges_from_legacy_deps("src", ["a"])
        assert len(result) == 1
        assert result[0].target == "a"
        assert result[0].source == "src"

    def test_多元素混合格式(self):
        deps = ["a", "b:async", "c:async:backoff"]
        result = edges_from_legacy_deps("api", deps)
        assert len(result) == 3

        assert result[0].target == "a"
        assert result[0].communication_mode == "sync"

        assert result[1].target == "b"
        assert result[1].communication_mode == "async"

        assert result[2].target == "c"
        assert result[2].communication_mode == "async"
        assert result[2].retry_strategy == "backoff"

    def test_所有边源相同(self):
        result = edges_from_legacy_deps("gateway", ["a", "b", "c"])
        for edge in result:
            assert edge.source == "gateway"


# ---------------------------------------------------------------------------
# 便捷判断 is_sync / is_async
# ---------------------------------------------------------------------------

class TestConvenienceMethods:
    """is_sync / is_async 便捷判断。"""

    def test_is_sync对同步边返回True(self):
        edge = DependencyEdge(source="A", target="B", communication_mode="sync")
        assert is_sync(edge) is True

    def test_is_sync对异步边返回False(self):
        edge = DependencyEdge(source="A", target="B", communication_mode="async")
        assert is_sync(edge) is False

    def test_is_async对异步边返回True(self):
        edge = DependencyEdge(source="A", target="B", communication_mode="async")
        assert is_async(edge) is True

    def test_is_async对同步边返回False(self):
        edge = DependencyEdge(source="A", target="B", communication_mode="sync")
        assert is_async(edge) is False

    def test_is_sync与is_async互斥(self):
        sync_edge = DependencyEdge(source="A", target="B", communication_mode="sync")
        async_edge = DependencyEdge(source="A", target="B", communication_mode="async")
        assert is_sync(sync_edge) != is_async(sync_edge)
        assert is_sync(async_edge) != is_async(async_edge)
