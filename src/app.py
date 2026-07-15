"""
ReckonBox MVP - Streamlit 主入口
"""

import copy
import os
from enum import Enum

import streamlit as st

from modules.domain_pack import load_domain_pack
from modules.design_builder import render_sidebar, render_topology
from modules.simulation_engine import SandboxEngine
from modules.ai_tutor import generate_educational_feedback, is_llm_configured
from utils.design_spec import (
    DesignSpec,
    ComponentConfig,
    spec_to_engine_components,
    to_dict,
)
from utils.blackbox_spec import to_dict as blackbox_to_dict

DOMAIN_PACK_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "domain_packs",
    "software_systems.yaml",
)


def _apply_styles():
    st.markdown(
        """
        <style>
        .block-container { padding-top: 1.5rem; padding-bottom: 2rem; }
        .stTabs [data-baseweb="tab-list"] { gap: 1rem; }
        h1 { font-weight: 600; letter-spacing: -0.5px; }
        h2, h3, h4 { font-weight: 500; }
        [data-testid="stMetricValue"] { font-size: 1.6rem; font-weight: 600; }
        [data-testid="stMetricLabel"] { font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.5px; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _check_auth() -> bool:
    """Streamlit Cloud 原生认证。本地未配置时自动放行。"""
    try:
        user = st.experimental_user
        if getattr(user, "is_logged_in", False):
            return True
    except Exception:
        return True

    # 认证已配置但未登录
    if not hasattr(st, "login"):
        return True

    st.markdown("## 🔐 登录 ReckonBox")
    st.write("请登录后开始使用。")
    try:
        st.login()
    except Exception:
        return True
    return False


def _init_state():
    if "domain_pack" not in st.session_state:
        st.session_state["domain_pack"] = load_domain_pack(DOMAIN_PACK_PATH)
    if "design_spec" not in st.session_state:
        st.session_state["design_spec"] = DesignSpec(
            domain="software_systems", components=[]
        )
    if "last_report" not in st.session_state:
        st.session_state["last_report"] = None
    if "nlp_input_area" not in st.session_state:
        st.session_state["nlp_input_area"] = ""


def _run_scenario_simulation(current_spec, domain_pack):
    try:
        with st.spinner("正在运行压力测试..."):
            from modules.scenario_generator import generate_scenarios

            blackbox_spec = st.session_state.get("blackbox_spec")
            selected = st.session_state.get("selected_options", {})
            components_list = copy.deepcopy(to_dict(current_spec)["components"])
            scenarios = generate_scenarios(blackbox_spec, selected, components_list)

            reports = []
            for scenario in scenarios:
                engine_components = {
                    c["name"]: c
                    for c in copy.deepcopy(components_list)
                    if isinstance(c, dict) and "name" in c
                }
                engine = SandboxEngine(engine_components, scenario)
                reports.append(engine.run(ticks=100))

            st.session_state["scenario_reports"] = reports
            st.session_state["scenario_list"] = scenarios
    except Exception as exc:
        st.error(f"压力测试出错: {exc}")
        st.session_state.pop("scenario_reports", None)
        st.session_state.pop("scenario_list", None)

def _render_remediation_tabs(current_spec):
    try:
        from modules.remediation_guide import generate_guidance
        from utils.blackbox_spec import to_dict as blackbox_to_dict

        blackbox_dict = (
            blackbox_to_dict(st.session_state["blackbox_spec"])
            if "blackbox_spec" in st.session_state
            else None
        )
        scenario_list = st.session_state.get("scenario_list", [])

        for i, report in enumerate(st.session_state["scenario_reports"]):
            scenario_name = report.get("scenario", f"场景 {i + 1}")
            if i < len(scenario_list):
                scenario_name = scenario_list[i].name
            
            st.markdown(f"### 场景 {i + 1}: {scenario_name}")
            _render_metrics(report)
            
            remediation = generate_guidance(report, to_dict(current_spec), blackbox_dict)
            
            tab1, tab2, tab3 = st.tabs(["🚨 崩溃复盘", "💡 认知诊断", "🛠️ 改进建议"])
            
            with tab1:
                if remediation.crash_analysis:
                    for crash in remediation.crash_analysis:
                        st.error(
                            f"**[{crash['severity']}] {crash['failure_mode']} @ {crash['component']}**\n\n"
                            f"{crash['evidence']}"
                        )
                else:
                    st.success("系统运行平稳，未检测到崩溃。")
                    
            with tab2:
                for card_info in remediation.teaching_cards:
                    card = card_info.get("card", {})
                    title = card.get("title", card_info.get("failure_mode", "教学卡片"))
                    with st.expander(f"📖 {title}"):
                        st.write(card.get("explanation", ""))
                        if card.get("code_example"):
                            st.code(card["code_example"])
                        if card.get("related_concepts"):
                            st.caption("相关概念: " + ", ".join(card["related_concepts"]))
                            
            with tab3:
                if remediation.design_guidance:
                    for g in remediation.design_guidance:
                        st.info(f"❓ **思考题**：{g.get('question', '')}")
                    if st.button("🔄 重新设计", key=f"re_design_{i}"):
                        st.session_state.pop("scenario_reports", None)
                        st.rerun()
                else:
                    st.write("您的设计非常完美！")
                    
            st.divider()
    except Exception as exc:
        st.error(f"修复引导生成出错: {exc}")


def _render_components_list_inline(current_spec, domain_pack):
    """在主页面内联渲染组件列表"""
    if not current_spec.components:
        st.caption("暂无组件")

    for i, comp in enumerate(current_spec.components):
        name = getattr(comp, "name", "unknown")
        ctype = getattr(comp, "type", "unknown")
        if isinstance(ctype, Enum):
            ctype = ctype.value
        emoji = {"gateway": "🌐", "service": "⚙️", "database": "🗄️", "cache": "💾", "message_queue": "📬", "scheduled_task": "⏰"}.get(ctype, "📦")
        
        cols = st.columns([4, 1])
        cols[0].markdown(
            f"{emoji} **{name}** <span style='font-size:0.75rem;color:#666'>({ctype})</span>",
            unsafe_allow_html=True,
        )
        if cols[1].button("✕", key=f"del_inline_{i}"):
            current_spec.components.pop(i)
            st.session_state["design_spec"] = current_spec
            st.rerun()

    with st.expander("➕ 添加组件", expanded=False):
        new_type = st.selectbox(
            "类型",
            list(domain_pack.component_types.keys()),
            key="new_comp_type_inline",
            label_visibility="collapsed",
        )
        existing = {getattr(c, "name", "unknown") for c in current_spec.components}
        suffix = 1
        while f"{new_type}_{suffix}" in existing:
            suffix += 1
        new_name = st.text_input("名称", value=f"{new_type}_{suffix}", key="new_comp_name_inline")
        if st.button("添加", use_container_width=True, key="add_comp_inline"):
            if new_name in existing:
                st.error("名称已存在")
            else:
                from modules.design_builder import _default_component
                current_spec.components.append(_default_component(new_name, new_type, domain_pack))
                st.session_state["design_spec"] = current_spec
                st.rerun()

    return current_spec

def _render_component_editor_inline(current_spec, domain_pack):
    """在主页面内联渲染参数编辑器"""
    if not current_spec.components:
        return current_spec

    st.markdown("---")
    names = [getattr(c, "name", "unknown") for c in current_spec.components]

    selected_from_canvas = st.session_state.get("selected_component")
    if (
        selected_from_canvas in names
        and selected_from_canvas != st.session_state.get("selected_comp_inline")
    ):
        st.session_state["selected_comp_inline"] = selected_from_canvas

    selected = st.selectbox("选择组件进行配置", names, key="selected_comp_inline")
    st.session_state["selected_component"] = selected

    idx = names.index(selected)
    comp = current_spec.components[idx]
    
    from modules.design_builder import _ensure_config, _type, _pretty_label, _deps, parse_dep, validate_cycles
    comp = _ensure_config(comp)
    ctype = _type(comp)
    if hasattr(ctype, "value"):
        ctype = ctype.value
    type_spec = domain_pack.component_types.get(ctype)
    
    if type_spec:
        # 基础参数放在 expander 中
        with st.expander("⚙️ 基础参数配置", expanded=False):
            comp.name = st.text_input("名称", value=comp.name, key=f"name_inline_{idx}")
            params = list(type_spec.params.items())
            for i in range(0, len(params), 2):
                cols = st.columns(2)
                for col, (pname, pspec) in zip(cols, params[i : i + 2]):
                    with col:
                        current = getattr(comp, pname, pspec.default)
                        label = _pretty_label(pname, pspec.unit)
                        key = f"param_inline_{idx}_{pname}"

                        if pspec.type == "int":
                            value = st.slider(label, min_value=pspec.min, max_value=pspec.max, value=current, key=key)
                        elif pspec.type == "bool":
                            value = st.checkbox(label, value=current, key=key)
                        else:
                            value = st.text_input(label, value=str(current), key=key)
                        setattr(comp, pname, value)
                        
            # 依赖配置
            allowed = type_spec.can_depend_on if type_spec else []
            options = [getattr(other, "name", "unknown") for other in current_spec.components 
                      if getattr(other, "name", "unknown") != comp.name and 
                      (getattr(other, "type", "unknown").value if hasattr(getattr(other, "type", "unknown"), "value") else getattr(other, "type", "unknown")) in allowed]
            
            # 获取当前依赖（兼容 ComponentConfig 和 dict）
            current_deps = getattr(comp, "dependencies", [])
            if not isinstance(current_deps, list):
                current_deps = list(current_deps)
                
            selected_deps = st.multiselect("依赖", options=options, default=current_deps, key=f"deps_inline_{comp.name}")
            comp.dependencies = selected_deps
            has_cycle, cycle = validate_cycles(current_spec)
            if has_cycle:
                st.error(f"循环依赖：{' → '.join(cycle)}")
                comp.dependencies = current_deps

        # 微观策略（核心）直接暴露
        st.caption("📝 微观策略 (自然语言/伪代码)")
        comp.description = st.text_area(
            "策略描述",
            value=getattr(comp, "description", ""),
            key=f"desc_inline_{idx}",
            placeholder="例如：如果 DB 延迟 > 500ms，则降级读取缓存；失败则指数退避重试",
            height=100,
            label_visibility="collapsed"
        )
        
        # 临时：将用户的自然语言策略通过一个按钮“翻译”为结构化 strategies（这里先 mock，后续接 LLM）
        if st.button("✨ 解析策略", key=f"parse_strategy_{idx}"):
            st.info("策略已解析并绑定到引擎 (Mock)")
            # 这里可以调用一个 LLM parser 将 comp.description 转化为 comp.strategies

    current_spec.components[idx] = comp
    return current_spec


def _render_header():
    st.title("ReckonBox")
    st.caption("建构主义系统设计沙盒：设计 → 模拟 → 观察失败 → 获得启发")

def _render_metrics(report):
    cols = st.columns(4)
    metrics = [
        ("总请求", report["total_requests"]),
        ("成功", report["successful"]),
        ("失败", report["failed"]),
        ("成功率", f"{report['success_rate']}%"),
    ]
    for col, (label, value) in zip(cols, metrics):
        col.metric(label, value)


def _render_component_table(report):
    import pandas as pd

    rows = [
        {
            "组件": name,
            "类型": stats["type"],
            "副本": stats["replicas"],
            "处理": stats["total_processed"],
            "失败": stats["total_failed"],
            "重试": stats["total_retried"],
            "崩溃": stats["crash_count"],
            "熔断": "●" if stats["has_circuit_breaker"] else "○",
        }
        for name, stats in report["component_stats"].items()
    ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def _render_events(report):
    import pandas as pd

    if not report["key_events"]:
        return

    rows = [
        {"Tick": e["tick"], "组件": e["component"], "类型": e["type"], "详情": e["detail"]}
        for e in report["key_events"][:15]
    ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    if len(report["key_events"]) > 15:
        st.caption(f"共 {len(report['key_events'])} 个关键事件，仅展示前 15 个")


def _render_failure_modes(report):
    import pandas as pd

    if not report["failure_modes"]:
        st.success("当前场景未触发任何失败模式")
        return

    rows = [
        {
            "严重性": mode["severity"],
            "失败模式": mode["mode"],
            "组件": mode["component"],
            "证据": mode["evidence"],
        }
        for mode in report["failure_modes"]
    ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def _render_report(report):
    st.subheader("模拟结果")
    _render_metrics(report)
    st.write("")

    tab_stats, tab_events, tab_failures = st.tabs(["组件统计", "关键事件", "失败模式"])
    with tab_stats:
        _render_component_table(report)
    with tab_events:
        _render_events(report)
    with tab_failures:
        _render_failure_modes(report)


def _render_feedback(report, design_spec):
    st.subheader("AI 导师反馈")
    llm_ready = is_llm_configured()
    if llm_ready:
        st.caption("🤖 LLM 模式 · 调用失败时自动降级为模板")
    else:
        st.caption("📝 模板模式 · 配置 OPENAI_API_KEY 启用 LLM 反馈")
    with st.spinner("正在生成反馈..."):
        feedback = generate_educational_feedback(
            "自定义设计", report, to_dict(design_spec)
        )
    st.markdown(feedback)


def _render_design_review():
    """展示 LLM 架构审查结果（拓扑图之后、运行模拟之前）。"""
    if "design_issues" not in st.session_state:
        return
    issues = st.session_state["design_issues"]
    msg = st.session_state.get("design_review_msg", "")
    if not issues:
        st.info(msg)
        return
    st.subheader("🔍 架构审查结果")
    for issue in issues:
        severity = issue.get("severity", "low")
        line = f"[{issue['type']}] {issue['component']}: {issue['description']}"
        if severity == "high":
            st.error(line)
        elif severity == "medium":
            st.warning(line)
        else:
            st.info(line)
        if issue.get("suggestion"):
            st.caption(f"💡 {issue['suggestion']}")


def _render_scenario_flow(current_spec, domain_pack):

    # Step 1: NLP 场景输入
    st.subheader("Step 1 · 描述系统场景")
    
    st.caption("💡 不知道怎么写？试试预设场景：")
    cols = st.columns(3)
    if cols[0].button("🛒 电商秒杀系统", use_container_width=True):
        st.session_state["nlp_input_area"] = "设计一个 QPS 达 10w 的电商秒杀系统，读多写少，需要处理热点库存扣减，防止超卖。"
        st.rerun()
    if cols[1].button("💳 金融支付链路", use_container_width=True):
        st.session_state["nlp_input_area"] = "设计一个支付核心链路，QPS 5000，要求极高的资金一致性，不能丢失任何一笔订单，依赖多个外部银行接口，外部接口经常超时。"
        st.rerun()
    if cols[2].button("📡 车联网数据上报", use_container_width=True):
        st.session_state["nlp_input_area"] = "设计一个车联网数据上报系统，百万级设备每秒上报坐标，纯写入，允许极少量数据丢失，后端需要进行流式计算。"
        st.rerun()
    
    # 使用 st.chat_message 模拟对话流
    with st.chat_message("user"):
        nlp_input = st.text_area(
            "描述你的系统场景",
            key="nlp_input_area",
            placeholder="如：QPS达100w的订单系统，查多于写，设计热库订单系统",
            height=80,
            label_visibility="collapsed"
        )
        if st.button("解析场景", type="primary"):
            try:
                from modules.scenario_parser import parse_scenario
                with st.spinner("AI 正在解析场景边界..."):
                    spec, msg = parse_scenario(nlp_input)
                if spec:
                    st.session_state["blackbox_spec"] = spec
                    # 场景变更后重置下游状态
                    st.session_state.pop("design_variants", None)
                    st.session_state.pop("selected_options", None)
                    st.session_state.pop("param_suggestions", None)
                    st.session_state.pop("param_advice_msg", None)
                    st.session_state.pop("scenario_reports", None)
                    st.session_state.pop("scenario_list", None)
                    st.session_state["setup_step"] = 2
                else:
                    st.error(msg)
            except Exception as exc:
                st.error(f"场景解析出错: {exc}")

    # Step 2: 黑盒规格展示 + 变更点选择
    if "blackbox_spec" not in st.session_state:
        return

    spec = st.session_state["blackbox_spec"]
    
    with st.chat_message("assistant"):
        st.write("已为您解析场景边界：")
        st.write(
            "**功能点**:",
            ", ".join(spec.functional_points) if spec.functional_points else "（无）",
        )
        st.write("**性能边界**:", spec.performance_bounds or "（无）")
        if spec.exception_scenarios:
            st.write("**异常场景**:")
            for exc in spec.exception_scenarios:
                name = exc.get("name", "") if isinstance(exc, dict) else ""
                desc = exc.get("description", "") if isinstance(exc, dict) else ""
                st.write(f"  - {name}: {desc}")

        # 识别变更点
        if "design_variants" not in st.session_state or not st.session_state["design_variants"]:
            try:
                from modules.variant_identifier import identify_variants
                with st.spinner("AI 正在挖掘架构分歧点..."):
                    variants, msg = identify_variants(spec)
                st.session_state["design_variants"] = variants
                if not variants:
                    st.info(msg)
            except Exception as exc:
                st.session_state["design_variants"] = []
                st.error(f"变更点识别出错: {exc}")

        # 展示变更点选项
        if st.session_state.get("design_variants"):
            st.markdown("---")
            st.write("💡 **针对您的场景，存在以下关键的架构分歧点，请选择您的倾向：**")
            selected_options = {}
            for v in st.session_state["design_variants"]:
                option_labels = [f"{o['label']} - {o['description']}" for o in v.options]
                if not option_labels:
                    continue
                choice = st.radio(f"🎯 {v.name}", option_labels, key=f"variant_{v.id}")
                selected_options[v.id] = v.options[option_labels.index(choice)]["id"]
            st.session_state["selected_options"] = selected_options
            
            if st.button("➡️ 确认选择并进入沙盒设计", type="primary"):
                st.session_state["setup_step"] = 3
                st.rerun()

    if st.session_state.get("setup_step", 1) < 3:
        return

    st.divider()

    # Step 3: 系统设计（左右分栏）
    st.subheader("Step 3 · 沙盒设计与策略编排")
    
    col_left, col_right = st.columns([1, 2])
    
    with col_left:
        st.caption("微观策略配置")
        # 直接在左侧栏渲染组件配置
        current_spec = _render_components_list_inline(current_spec, domain_pack)
        current_spec = _render_component_editor_inline(current_spec, domain_pack)
        
        # 运行按钮放在左侧底部
        st.markdown("---")
        if st.button("🚀 开始混沌演练", type="primary", use_container_width=True):
            if not current_spec.components:
                st.error("请先添加至少一个组件")
            else:
                _run_scenario_simulation(current_spec, domain_pack)

    with col_right:
        st.caption("宏观拓扑预览")
        render_topology(current_spec)

    # Step 4 & 5: 演练结果与修复引导
    if "scenario_reports" in st.session_state:
        st.divider()
        st.subheader("Step 4 · 认知闭环")
        _render_remediation_tabs(current_spec)


def main():
    st.set_page_config(
        page_title="ReckonBox",
        page_icon="🎓",
        layout="wide",
        initial_sidebar_state="collapsed"
    )
    _apply_styles()

    if not _check_auth():
        return

    _init_state()

    domain_pack = st.session_state["domain_pack"]
    current_spec = st.session_state["design_spec"]

    _render_header()

    # LLM 不可用时的提示
    if not is_llm_configured():
        st.warning(
            "⚠️ 请配置 OPENAI_API_KEY 以解锁完整体验"
            "（NLP 场景输入、变更点识别、参数建议、教学卡片）"
        )

    _render_scenario_flow(current_spec, domain_pack)


if __name__ == "__main__":
    main()
