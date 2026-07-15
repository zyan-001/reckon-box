"""
Module 2 - Streamlit Design Builder UI
"""

import copy

import streamlit as st
import graphviz

from dataclasses import fields
from utils.design_spec import (
    DesignSpec,
    ComponentConfig,
    to_json,
    from_json,
    validate_cycles,
    parse_dep,
)
from modules.domain_pack import DomainPack
from domain_packs.software_systems_presets import PRESETS


TYPE_EMOJI = {
    "gateway": "🌐",
    "service": "⚙️",
    "database": "🗄️",
    "cache": "💾",
    "message_queue": "📬",
    "scheduled_task": "⏰",
}
TYPE_COLOR = {
    "gateway": "#D6EAF8",
    "service": "#D5F5E3",
    "database": "#FCF3CF",
    "cache": "#FADBD8",
    "message_queue": "#E8DAEF",
    "scheduled_task": "#FAE5D3",
}
# streamlit-flow 节点配色（按类型：蓝/绿/黄/红/紫/橙）
FLOW_NODE_COLOR = {
    "gateway": "#2196F3",
    "service": "#4CAF50",
    "database": "#FFC107",
    "cache": "#F44336",
    "message_queue": "#9C27B0",
    "scheduled_task": "#FF9800",
}


def render_sidebar(domain_pack: DomainPack, current_spec: DesignSpec) -> DesignSpec:
    """渲染左侧设计面板，返回最新的 DesignSpec。"""
    st.sidebar.title("ReckonBox")

    current_spec = _render_templates(current_spec, domain_pack)
    current_spec = _render_components_list(current_spec, domain_pack)
    current_spec = _render_component_editor(current_spec, domain_pack)
    current_spec = _render_io(current_spec)
    _render_scenario_selector(domain_pack)

    st.session_state["design_spec"] = current_spec
    return current_spec


def _render_templates(current_spec, domain_pack):
    """模板选择。"""
    template = st.sidebar.selectbox(
        "模板",
        ["空白开始", "初学者模板", "中级模板", "专家模板"],
        key="template_selector",
    )

    cols = st.sidebar.columns([1, 1])
    if cols[0].button("加载", use_container_width=True):
        if template == "空白开始":
            current_spec = _empty_spec(domain_pack)
        else:
            current_spec = PRESETS[template]()
        st.session_state["design_spec"] = current_spec
        st.rerun()

    return current_spec


def _render_components_list(current_spec, domain_pack):
    """组件列表：增删。"""
    st.sidebar.markdown("---")
    st.sidebar.subheader("组件")

    if not current_spec.components:
        st.sidebar.caption("暂无组件")

    for i, comp in enumerate(current_spec.components):
        name = _name(comp)
        ctype = _type(comp)
        emoji = TYPE_EMOJI.get(ctype, "📦")
        cols = st.sidebar.columns([4, 1])
        cols[0].markdown(
            f"{emoji} **{name}**  <br>"
            f"<span style='font-size:0.75rem;color:#666'>{ctype}</span>",
            unsafe_allow_html=True,
        )
        if cols[1].button("✕", key=f"del_{i}"):
            current_spec.components.pop(i)
            st.session_state["design_spec"] = current_spec
            st.rerun()

    with st.sidebar.expander("➕ 添加组件", expanded=False):
        new_type = st.selectbox(
            "类型",
            list(domain_pack.component_types.keys()),
            key="new_comp_type",
            label_visibility="collapsed",
        )
        existing = {_name(c) for c in current_spec.components}
        suffix = 1
        while f"{new_type}_{suffix}" in existing:
            suffix += 1
        new_name = st.text_input("名称", value=f"{new_type}_{suffix}", key="new_comp_name")
        if st.button("添加", use_container_width=True):
            if new_name in existing:
                st.error("名称已存在")
            else:
                current_spec.components.append(_default_component(new_name, new_type, domain_pack))
                st.session_state["design_spec"] = current_spec
                st.rerun()

    return current_spec


def _render_component_editor(current_spec, domain_pack):
    """参数与依赖编辑。"""
    if not current_spec.components:
        return current_spec

    st.sidebar.markdown("---")
    st.sidebar.subheader("参数")

    names = [_name(c) for c in current_spec.components]

    # 节点选中联动：画布点击写入 selected_component，与 selectbox 不同步时切换
    selected_from_canvas = st.session_state.get("selected_component")
    if (
        selected_from_canvas in names
        and selected_from_canvas != st.session_state.get("selected_comp")
    ):
        st.session_state["selected_comp"] = selected_from_canvas

    selected = st.sidebar.selectbox("选择组件", names, key="selected_comp")
    # 反向同步：selectbox 当前值回写，避免下次覆盖用户手动选择
    st.session_state["selected_component"] = selected

    idx = names.index(selected)
    comp = current_spec.components[idx]
    comp = _ensure_config(comp)

    comp = _render_params(comp, domain_pack, idx)
    comp = _render_dependencies(comp, current_spec, domain_pack)
    current_spec.components[idx] = comp

    return current_spec


def _render_params(comp, domain_pack, idx):
    """根据 DomainPack 自动生成参数控件，紧凑两列布局。"""
    ctype = _type(comp)
    type_spec = domain_pack.component_types.get(ctype)
    if not type_spec:
        return comp

    comp = _ensure_config(comp)

    # 名称单独一行
    comp.name = st.sidebar.text_input("名称", value=comp.name, key=f"name_{idx}")

    # 参数两列：sidebar 内使用 st.sidebar.columns
    params = list(type_spec.params.items())
    for i in range(0, len(params), 2):
        cols = st.sidebar.columns(2)
        for col, (pname, pspec) in zip(cols, params[i : i + 2]):
            with col:
                current = getattr(comp, pname, pspec.default)
                label = _pretty_label(pname, pspec.unit)
                key = f"param_{idx}_{pname}"

                if pspec.type == "int":
                    value = st.sidebar.slider(
                        label,
                        min_value=pspec.min,
                        max_value=pspec.max,
                        value=current,
                        key=key,
                    )
                elif pspec.type == "bool":
                    value = st.sidebar.checkbox(label, value=current, key=key)
                else:
                    value = st.sidebar.text_input(label, value=str(current), key=key)

                setattr(comp, pname, value)

    # 组件描述：用自然语言或伪代码描述此组件的行为
    comp.description = st.sidebar.text_area(
        "组件描述",
        value=getattr(comp, "description", ""),
        key=f"desc_{idx}",
        help="用自然语言或伪代码描述此组件的行为",
    )

    return comp


def _render_dependencies(comp, spec, domain_pack):
    """依赖选择 + 循环检测。"""
    comp = _ensure_config(comp)
    ctype = _type(comp)
    type_spec = domain_pack.component_types.get(ctype)
    allowed = type_spec.can_depend_on if type_spec else []

    options = [
        _name(other)
        for other in spec.components
        if _name(other) != comp.name and _type(other) in allowed
    ]

    current = list(comp.dependencies)
    selected = st.sidebar.multiselect(
        "依赖",
        options=options,
        default=current,
        key=f"deps_{comp.name}",
    )

    comp.dependencies = selected
    has_cycle, cycle = validate_cycles(spec)
    if has_cycle:
        st.sidebar.error(f"循环依赖：{' → '.join(cycle)}")
        comp.dependencies = current

    return comp


def _render_io(current_spec):
    """导入/导出。"""
    st.sidebar.markdown("---")

    cols = st.sidebar.columns(2)
    cols[0].download_button(
        "导出",
        data=to_json(current_spec).encode("utf-8"),
        file_name="design.json",
        mime="application/json",
        use_container_width=True,
    )

    uploaded = cols[1].file_uploader(
        "导入", type=["json"], label_visibility="collapsed"
    )
    if uploaded is not None:
        try:
            content = uploaded.read().decode("utf-8")
            current_spec = from_json(content)
            st.session_state["design_spec"] = current_spec
            st.sidebar.success("已导入")
        except Exception as exc:
            st.sidebar.error(f"导入失败：{exc}")

    return current_spec


def _render_scenario_selector(domain_pack):
    """场景选择，结果写入 session_state。"""
    st.sidebar.markdown("---")
    scenarios = domain_pack.get_scenarios()
    options = {s.id: s.name for s in scenarios}
    selected = st.sidebar.selectbox(
        "场景",
        options=list(options.keys()),
        format_func=lambda x: options[x],
        key="scenario_selector",
    )
    st.session_state["scenario_id"] = selected


def render_topology(spec):
    """在主面板渲染组件拓扑图。

    优先尝试 streamlit-flow 可编辑画布；导入失败时 fallback 到 graphviz 静态图。
    """
    try:
        _render_flow_canvas(spec)
    except ImportError:
        _render_graphviz_topology(spec)


def _render_flow_canvas(spec):
    """使用 streamlit-flow 渲染可编辑拓扑画布。

    - 节点：每个组件一个节点，按类型着色
      (gateway=蓝/service=绿/database=黄/cache=红/message_queue=紫/scheduled_task=橙)
    - 边：每个依赖一条边，sync 实线、async 虚线
    - 节点点击 → 更新 st.session_state["selected_component"]
    - 拖线创建依赖 → 调用 validate_cycles 检测，通过则更新 dependencies
    - 删除边 → 移除对应依赖
    - 使用 st.session_state["flow_nodes"] / ["flow_edges"] 维护画布状态

    若 streamlit-flow 不可用，render_topology 会捕获 ImportError 并 fallback。
    """
    # 在函数内部导入，使包不可用时能被 render_topology 的 except 捕获
    from streamlit_flow import streamlit_flow  # noqa: F401
    from streamlit_flow.elements import FlowNode, FlowEdge

    if not spec.components:
        st.info("暂无组件")
        return

    # ---- 从 spec 构建节点（保留用户拖动后的位置）----
    cached_positions = st.session_state.get("flow_nodes", {}) or {}
    nodes = []
    for i, comp in enumerate(spec.components):
        name = _name(comp)
        ctype = _type(comp)
        pos = cached_positions.get(name)
        if pos is None:
            pos = {"x": (i % 4) * 220, "y": (i // 4) * 140}
        nodes.append(
            FlowNode(
                id=name,
                data={"content": f"{TYPE_EMOJI.get(ctype, '📦')} {name}\n({ctype})"},
                position=pos,
                style={"backgroundColor": FLOW_NODE_COLOR.get(ctype, "#888888")},
            )
        )

    # ---- 从 spec.dependencies 构建边（sync 实线 / async 虚线）----
    edges = []
    for comp in spec.components:
        src = _name(comp)
        for dep in _deps(comp):
            tgt, mode = parse_dep(dep)
            if any(_name(c) == tgt for c in spec.components):
                edges.append(
                    FlowEdge(
                        id=f"{src}->{tgt}",
                        source=src,
                        target=tgt,
                        animated=(mode == "async"),
                        label="async" if mode == "async" else "",
                    )
                )

    st.session_state["flow_nodes"] = {n.id: n.position for n in nodes}
    st.session_state["flow_edges"] = [
        {"id": e.id, "source": e.source, "target": e.target} for e in edges
    ]

    result = streamlit_flow(
        key="topology_canvas",
        nodes=nodes,
        edges=edges,
        fit_view=True,
        show_controls=True,
        allow_edge_interaction=True,
        allow_node_interaction=True,
        height=420,
    )
    result = result or {}

    # ---- 节点点击：更新选中组件 ----
    selected_nodes = result.get("selected_nodes", []) or []
    if selected_nodes:
        first = selected_nodes[0]
        nid = first.get("id") if isinstance(first, dict) else getattr(first, "id", None)
        if nid:
            st.session_state["selected_component"] = nid

    # ---- 边变化：拖线新增 / 删除边 → 同步 dependencies（含循环检测）----
    _sync_edges_to_dependencies(spec, edges, result.get("edges", edges))

    # ---- 保留最新节点位置 ----
    updated_nodes = result.get("nodes", nodes)
    st.session_state["flow_nodes"] = {
        (n.get("id") if isinstance(n, dict) else getattr(n, "id", None)): (
            n.get("position") if isinstance(n, dict) else getattr(n, "position", {"x": 0, "y": 0})
        )
        for n in updated_nodes
    }


def _render_graphviz_topology(spec):
    """graphviz 静态拓扑图（streamlit-flow 不可用时的 fallback）。"""
    with st.container():
        if not spec.components:
            st.info("暂无组件")
            return

        dot = graphviz.Digraph()
        dot.attr(rankdir="LR", bgcolor="transparent")

        for comp in spec.components:
            name = _name(comp)
            ctype = _type(comp)
            dot.node(
                name,
                label=f"{TYPE_EMOJI.get(ctype, '')} {name}\n({ctype})",
                shape="box",
                style="rounded,filled",
                fillcolor=TYPE_COLOR.get(ctype, "#FFFFFF"),
                fontname="Segoe UI",
                fontsize="11",
            )

        for comp in spec.components:
            src = _name(comp)
            for dep in _deps(comp):
                if any(_name(c) == dep for c in spec.components):
                    dot.edge(src, dep, color="#666666", arrowsize="0.8")

        st.graphviz_chart(dot)


def _sync_edges_to_dependencies(spec, prev_edges, curr_edges):
    """根据画布边变化同步 dependencies。

    - 新增边：默认 sync 依赖，调用 validate_cycles 检测，循环则撤销并报错
    - 删除边：移除对应依赖（兼容 sync/async 标注）
    """
    def _endpoints(edge):
        if isinstance(edge, dict):
            return (edge.get("source"), edge.get("target"))
        return (getattr(edge, "source", None), getattr(edge, "target", None))

    prev_set = {_endpoints(e) for e in prev_edges}
    curr_set = {_endpoints(e) for e in curr_edges}
    added = curr_set - prev_set
    removed = prev_set - curr_set

    if not added and not removed:
        return

    target_spec = st.session_state.get("design_spec", spec)
    name_to_comp = {_name(c): c for c in target_spec.components}

    # 删除依赖
    for src, tgt in removed:
        comp = name_to_comp.get(src)
        if comp is None or tgt is None:
            continue
        deps = list(_deps(comp))
        new_deps = [d for d in deps if parse_dep(d)[0] != tgt]
        _set_deps(comp, new_deps)

    # 新增依赖（循环检测）
    for src, tgt in added:
        if src is None or tgt is None or src == tgt:
            continue
        comp = name_to_comp.get(src)
        if comp is None:
            continue
        deps = list(_deps(comp))
        if tgt in {parse_dep(d)[0] for d in deps}:
            continue
        deps.append(tgt)  # 拖线创建的依赖默认 sync
        _set_deps(comp, deps)
        has_cycle, cycle = validate_cycles(target_spec)
        if has_cycle:
            st.error(f"循环依赖：{' → '.join(cycle)}，连线未创建")
            deps.remove(tgt)
            _set_deps(comp, deps)

    st.session_state["design_spec"] = target_spec


def _set_deps(comp, deps):
    """统一写入依赖列表（兼容 ComponentConfig 与 dict）。"""
    if isinstance(comp, ComponentConfig):
        comp.dependencies = deps
    elif isinstance(comp, dict):
        comp["dependencies"] = deps


def _empty_spec(domain_pack):
    return DesignSpec(
        domain="software_systems",
        components=[_default_component("api_gateway", "gateway", domain_pack)],
    )


def _default_component(name, comp_type, domain_pack):
    type_spec = domain_pack.component_types.get(comp_type)
    if not type_spec:
        return ComponentConfig(name=name, type=comp_type)
    kwargs = {"name": name, "type": comp_type}
    
    # 过滤掉 ComponentConfig 中不存在的参数
    valid_keys = {f.name for f in fields(ComponentConfig)}
    
    for pname, pspec in type_spec.params.items():
        if pname in valid_keys:
            kwargs[pname] = pspec.default
            
    return ComponentConfig(**kwargs)


def _ensure_config(comp):
    if isinstance(comp, ComponentConfig):
        return comp
    if isinstance(comp, dict):
        return ComponentConfig(**comp)
    return comp


def _name(comp):
    if isinstance(comp, ComponentConfig):
        return comp.name
    return comp.get("name", "unknown")


def _type(comp):
    if isinstance(comp, ComponentConfig):
        return comp.type
    return comp.get("type", "unknown")


def _deps(comp):
    if isinstance(comp, ComponentConfig):
        return comp.dependencies
    return comp.get("dependencies", [])


def _pretty_label(name, unit=None):
    label = name.replace("_", " ").title()
    if unit:
        label += f" ({unit})"
    return label
