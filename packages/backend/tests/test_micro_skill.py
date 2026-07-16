"""
MicroSkill 数据结构测试
======================
TDD 测试覆盖 micro_skill 模块的所有接口和边界条件。
"""

import json

import pytest

from utils.micro_skill import (
    MicroSkill,
    StepNode,
    default_skill,
    from_dict,
    from_json,
    resolve_template,
    step_node_from_dict,
    step_node_to_dict,
    to_dict,
    to_json,
)
from utils.strategy_templates import get_template


# ---------------------------------------------------------------------------
# StepNode 基础
# ---------------------------------------------------------------------------

class TestStepNode:
    """StepNode 步骤树节点相关测试。"""

    def test_create_step_node(self):
        """创建 StepNode 应正确设置所有字段。"""
        node = StepNode(id="s1", label="步骤1", description="执行第一步")
        assert node.id == "s1"
        assert node.label == "步骤1"
        assert node.description == "执行第一步"
        assert node.children == []

    def test_default_children_is_empty_list(self):
        """默认 children 应为空列表。"""
        node = StepNode(id="s1", label="步骤1", description="描述")
        assert node.children == []
        assert isinstance(node.children, list)

    def test_nested_children(self):
        """StepNode 应支持嵌套子节点。"""
        child1 = StepNode(id="c1", label="子步骤1", description="子步骤描述1")
        child2 = StepNode(id="c2", label="子步骤2", description="子步骤描述2")
        parent = StepNode(id="p1", label="父步骤", description="父步骤描述", children=[child1, child2])
        assert len(parent.children) == 2
        assert parent.children[0].id == "c1"
        assert parent.children[1].id == "c2"

    def test_deeply_nested_children(self):
        """StepNode 应支持深层嵌套。"""
        leaf = StepNode(id="leaf", label="叶节点", description="最深层")
        mid = StepNode(id="mid", label="中间节点", description="中间层", children=[leaf])
        root = StepNode(id="root", label="根节点", description="最浅层", children=[mid])
        assert root.children[0].children[0].id == "leaf"


# ---------------------------------------------------------------------------
# StepNode 序列化 / 反序列化
# ---------------------------------------------------------------------------

class TestStepNodeSerialization:
    """StepNode 序列化/反序列化相关测试。"""

    def test_step_node_to_dict_basic(self):
        """基本 StepNode 序列化为字典。"""
        node = StepNode(id="s1", label="步骤1", description="描述1")
        result = step_node_to_dict(node)
        assert result == {
            "id": "s1",
            "label": "步骤1",
            "description": "描述1",
            "children": [],
        }

    def test_step_node_from_dict_basic(self):
        """从字典反序列化基本 StepNode。"""
        data = {"id": "s1", "label": "步骤1", "description": "描述1", "children": []}
        node = step_node_from_dict(data)
        assert node.id == "s1"
        assert node.label == "步骤1"
        assert node.description == "描述1"
        assert node.children == []

    def test_step_node_roundtrip_flat(self):
        """扁平 StepNode 往返一致性。"""
        original = StepNode(id="s1", label="步骤1", description="描述1")
        restored = step_node_from_dict(step_node_to_dict(original))
        assert restored == original

    def test_step_node_roundtrip_nested(self):
        """嵌套 StepNode 往返一致性。"""
        child1 = StepNode(id="c1", label="子步骤1", description="子描述1")
        child2 = StepNode(id="c2", label="子步骤2", description="子描述2")
        original = StepNode(id="p1", label="父步骤", description="父描述", children=[child1, child2])
        restored = step_node_from_dict(step_node_to_dict(original))
        assert restored == original

    def test_step_node_roundtrip_deeply_nested(self):
        """深层嵌套 StepNode 往返一致性。"""
        leaf = StepNode(id="leaf", label="叶节点", description="最深层")
        mid = StepNode(id="mid", label="中间节点", description="中间层", children=[leaf])
        root = StepNode(id="root", label="根节点", description="最浅层", children=[mid])
        restored = step_node_from_dict(step_node_to_dict(root))
        assert restored == root


# ---------------------------------------------------------------------------
# MicroSkill 基础
# ---------------------------------------------------------------------------

class TestMicroSkill:
    """MicroSkill 行为单元相关测试。"""

    def test_create_micro_skill(self):
        """创建 MicroSkill 应正确设置所有字段。"""
        skill = MicroSkill(strategy_template_id="sync_direct")
        assert skill.strategy_template_id == "sync_direct"
        assert skill.params == {}
        assert skill.assertions == []
        assert skill.step_tree is None

    def test_micro_skill_with_params(self):
        """MicroSkill 应正确设置 params。"""
        skill = MicroSkill(
            strategy_template_id="sync_direct",
            params={"timeout_ms": 1000, "capacity_per_replica": 50},
        )
        assert skill.params == {"timeout_ms": 1000, "capacity_per_replica": 50}

    def test_micro_skill_with_step_tree(self):
        """MicroSkill 应正确设置 step_tree。"""
        tree = StepNode(id="root", label="开始", description="第一步")
        skill = MicroSkill(
            strategy_template_id="sync_direct",
            step_tree=tree,
        )
        assert skill.step_tree is not None
        assert skill.step_tree.id == "root"

    def test_micro_skill_defaults_are_independent(self):
        """不同 MicroSkill 实例的默认可变字段应独立。"""
        skill1 = MicroSkill(strategy_template_id="sync_direct")
        skill2 = MicroSkill(strategy_template_id="retry_no_backoff")
        skill1.params["key"] = "value"
        assert "key" not in skill2.params


# ---------------------------------------------------------------------------
# MicroSkill 序列化 / 反序列化
# ---------------------------------------------------------------------------

class TestMicroSkillSerialization:
    """MicroSkill 序列化/反序列化相关测试。"""

    def _make_skill_with_tree(self) -> MicroSkill:
        """构造含 step_tree 的 MicroSkill。"""
        tree = StepNode(
            id="root", label="调用", description="发起调用",
            children=[
                StepNode(id="c1", label="重试", description="执行重试"),
            ],
        )
        return MicroSkill(
            strategy_template_id="retry_no_backoff",
            params={"max_retries": 3, "capacity_per_replica": 50},
            assertions=[{"metric": "dependency_failed", "action": "retry_immediately"}],
            step_tree=tree,
        )

    def _make_skill_without_tree(self) -> MicroSkill:
        """构造不含 step_tree 的 MicroSkill。"""
        return MicroSkill(
            strategy_template_id="sync_direct",
            params={"timeout_ms": 1000, "capacity_per_replica": 50},
            assertions=[{"metric": "dependency_failed", "action": "fail_immediately"}],
        )

    def test_to_dict_with_step_tree(self):
        """含 step_tree 的 MicroSkill 序列化为字典。"""
        skill = self._make_skill_with_tree()
        result = to_dict(skill)
        assert result["strategy_template_id"] == "retry_no_backoff"
        assert result["params"] == {"max_retries": 3, "capacity_per_replica": 50}
        assert len(result["assertions"]) == 1
        assert result["step_tree"] is not None
        assert result["step_tree"]["id"] == "root"
        assert len(result["step_tree"]["children"]) == 1

    def test_to_dict_without_step_tree(self):
        """不含 step_tree 的 MicroSkill 序列化时 step_tree 为 None。"""
        skill = self._make_skill_without_tree()
        result = to_dict(skill)
        assert result["step_tree"] is None

    def test_from_dict_with_step_tree(self):
        """从字典反序列化含 step_tree 的 MicroSkill。"""
        skill = self._make_skill_with_tree()
        restored = from_dict(to_dict(skill))
        assert restored.strategy_template_id == skill.strategy_template_id
        assert restored.params == skill.params
        assert restored.assertions == skill.assertions
        assert restored.step_tree is not None
        assert restored.step_tree.id == "root"
        assert len(restored.step_tree.children) == 1

    def test_from_dict_without_step_tree(self):
        """从字典反序列化不含 step_tree 的 MicroSkill。"""
        skill = self._make_skill_without_tree()
        restored = from_dict(to_dict(skill))
        assert restored.step_tree is None

    def test_roundtrip_dict_with_tree(self):
        """含 step_tree 的 MicroSkill to_dict/from_dict 往返一致性。"""
        skill = self._make_skill_with_tree()
        restored = from_dict(to_dict(skill))
        assert restored == skill

    def test_roundtrip_dict_without_tree(self):
        """不含 step_tree 的 MicroSkill to_dict/from_dict 往返一致性。"""
        skill = self._make_skill_without_tree()
        restored = from_dict(to_dict(skill))
        assert restored == skill

    def test_roundtrip_json_with_tree(self):
        """含 step_tree 的 MicroSkill to_json/from_json 往返一致性。"""
        skill = self._make_skill_with_tree()
        restored = from_json(to_json(skill))
        assert restored == skill

    def test_roundtrip_json_without_tree(self):
        """不含 step_tree 的 MicroSkill to_json/from_json 往返一致性。"""
        skill = self._make_skill_without_tree()
        restored = from_json(to_json(skill))
        assert restored == skill

    def test_to_json_is_valid_json(self):
        """to_json 输出应为合法 JSON。"""
        skill = self._make_skill_with_tree()
        json_str = to_json(skill)
        parsed = json.loads(json_str)
        assert parsed["strategy_template_id"] == "retry_no_backoff"

    def test_to_json_preserves_chinese(self):
        """JSON 序列化应保留中文字符。"""
        tree = StepNode(id="root", label="发起调用", description="第一步操作")
        skill = MicroSkill(strategy_template_id="sync_direct", step_tree=tree)
        json_str = to_json(skill)
        assert "发起调用" in json_str
        assert "第一步操作" in json_str

    def test_roundtrip_minimal_skill(self):
        """最小 MicroSkill（仅 strategy_template_id）往返一致性。"""
        skill = MicroSkill(strategy_template_id="sync_direct")
        restored = from_dict(to_dict(skill))
        assert restored == skill


# ---------------------------------------------------------------------------
# resolve_template 模板绑定
# ---------------------------------------------------------------------------

class TestResolveTemplate:
    """resolve_template 模板绑定相关测试。"""

    def test_resolve_sync_direct(self):
        """成功绑定 sync_direct 模板。"""
        skill = MicroSkill(
            strategy_template_id="sync_direct",
            params={"timeout_ms": 1000, "capacity_per_replica": 50},
        )
        template = resolve_template(skill)
        assert template.id == "sync_direct"

    def test_resolve_retry_no_backoff(self):
        """成功绑定 retry_no_backoff 模板。"""
        skill = MicroSkill(
            strategy_template_id="retry_no_backoff",
            params={"max_retries": 3, "capacity_per_replica": 50},
        )
        template = resolve_template(skill)
        assert template.id == "retry_no_backoff"

    def test_resolve_nonexistent_template_raises(self):
        """模板不存在时应抛出 ValueError。"""
        skill = MicroSkill(strategy_template_id="nonexistent_template")
        with pytest.raises(ValueError, match="策略模板不存在"):
            resolve_template(skill)

    def test_resolve_invalid_params_raises(self):
        """params 校验失败时应抛出 ValueError。"""
        skill = MicroSkill(
            strategy_template_id="sync_direct",
            params={"timeout_ms": "wrong_type"},  # 类型错误
        )
        with pytest.raises(ValueError, match="参数校验失败"):
            resolve_template(skill)

    def test_resolve_missing_params_raises(self):
        """缺少必需参数时应抛出 ValueError。"""
        skill = MicroSkill(
            strategy_template_id="sync_direct",
            params={},  # 缺少所有参数
        )
        with pytest.raises(ValueError, match="参数校验失败"):
            resolve_template(skill)

    def test_resolve_returns_template_with_capacity(self):
        """绑定成功后应可正常调用 calculate_capacity。"""
        skill = MicroSkill(
            strategy_template_id="sync_direct",
            params={"timeout_ms": 1000, "capacity_per_replica": 50},
        )
        template = resolve_template(skill)
        capacity = template.calculate_capacity(skill.params, 3)
        assert capacity == 150


# ---------------------------------------------------------------------------
# default_skill 默认创建
# ---------------------------------------------------------------------------

class TestDefaultSkill:
    """default_skill 默认创建相关测试。"""

    def test_default_skill_sync_direct(self):
        """默认创建 sync_direct 的 MicroSkill。"""
        skill = default_skill()
        assert skill.strategy_template_id == "sync_direct"
        assert skill.params == {"timeout_ms": 0, "capacity_per_replica": 1}
        assert len(skill.assertions) > 0

    def test_default_skill_retry_no_backoff(self):
        """指定 retry_no_backoff 创建默认 MicroSkill。"""
        skill = default_skill("retry_no_backoff")
        assert skill.strategy_template_id == "retry_no_backoff"
        assert skill.params == {"max_retries": 0, "capacity_per_replica": 1}
        assert len(skill.assertions) == 2  # retry_immediately + retry_storm

    def test_default_skill_circuit_break(self):
        """指定 circuit_break 创建默认 MicroSkill。"""
        skill = default_skill("circuit_break")
        assert skill.strategy_template_id == "circuit_break"
        assert skill.params == {"failure_threshold": 0, "recovery_ticks": 0, "capacity_per_replica": 1}

    def test_default_skill_nonexistent_raises(self):
        """不存在的模板 ID 应抛出 ValueError。"""
        with pytest.raises(ValueError, match="策略模板不存在"):
            default_skill("nonexistent_template")

    def test_default_skill_no_step_tree(self):
        """默认创建的 MicroSkill 不含 step_tree。"""
        skill = default_skill()
        assert skill.step_tree is None

    def test_default_skill_assertions_copied_from_template(self):
        """默认 MicroSkill 的 assertions 应来自模板。"""
        template = get_template("sync_direct")
        skill = default_skill("sync_direct")
        assert skill.assertions == template.assertions

    def test_default_skill_params_pass_validation(self):
        """默认 MicroSkill 的 params 应通过 resolve_template 校验。"""
        skill = default_skill()
        template = resolve_template(skill)
        assert template.id == "sync_direct"

    def test_default_skill_other_template_params_pass_validation(self):
        """指定其他模板创建的默认 MicroSkill 也应通过 resolve_template 校验。"""
        for template_id in ["sync_direct", "retry_no_backoff", "retry_with_backoff",
                            "async_fire_and_forget", "degrade_fallback", "circuit_break"]:
            skill = default_skill(template_id)
            template = resolve_template(skill)
            assert template.id == template_id
