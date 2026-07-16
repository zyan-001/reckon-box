"""ReckonBox 后端 FastAPI 应用

完整 API 端点：
  GET  /api/health              健康检查
  GET  /api/templates           策略模板列表
  GET  /api/domain-packs        领域包 + 组件类型 + 场景
  POST /api/scenario/parse      NLP 解析为 BlackBoxSpec
  POST /api/skill/parse         NL+步骤树 → MicroSkill
  POST /api/simulation/run      运行模拟
  POST /api/simulation/convergence  收敛检查
  POST /api/simulation/review   LLM 审查
"""

import sys
import os

# 确保 src 目录在 Python 路径中，使 from modules/ from utils/ 可用
_src_dir = os.path.dirname(os.path.abspath(__file__))
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(
    title="ReckonBox",
    description="建构主义教育沙盒 - 学生通过配置系统设计参数观察失败涌现",
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Pydantic 请求/响应模型
# ---------------------------------------------------------------------------

class ScenarioParseRequest(BaseModel):
    nlp_input: str

class SkillParseRequest(BaseModel):
    nl_description: str
    step_tree: dict | None = None

class SimulationRunRequest(BaseModel):
    components: dict
    scenario_id: str | None = None
    custom_scenario: dict | None = None

class ConvergenceCheckRequest(BaseModel):
    report: dict
    blackbox_spec: dict | None = None

class ReviewRequest(BaseModel):
    report: dict
    micro_skills: dict | None = None
    blackbox_spec: dict | None = None

class LoginRequest(BaseModel):
    username: str
    password: str


# ---------------------------------------------------------------------------
# 领域包缓存
# ---------------------------------------------------------------------------

_domain_pack_cache = None

def _get_domain_pack():
    """懒加载领域包"""
    global _domain_pack_cache
    if _domain_pack_cache is not None:
        return _domain_pack_cache
    from modules.domain_pack import load_domain_pack
    import os
    yaml_path = os.path.join(
        os.path.dirname(__file__), "domain_packs", "software_systems.yaml"
    )
    _domain_pack_cache = load_domain_pack(yaml_path)
    return _domain_pack_cache


# ---------------------------------------------------------------------------
# API 端点
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 认证中间件
# ---------------------------------------------------------------------------

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """认证中间件：检查 session token，tourist 限额控制"""
    # 跳过非 API 路径和公开端点
    path = request.url.path
    public_paths = {"/api/health", "/api/auth/login", "/api/auth/status"}
    if not path.startswith("/api/") or path in public_paths:
        return await call_next(request)

    # 读取 token（Cookie 或 Authorization header）
    token = request.cookies.get("session_token")
    if not token:
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]

    if not token:
        return Response(
            content='{"detail":"未登录"}',
            status_code=401,
            media_type="application/json",
        )

    from modules.auth import get_session, consume_llm_call, LLM_ENDPOINTS, remaining_llm_calls
    session = get_session(token)
    if session is None:
        return Response(
            content='{"detail":"会话已过期"}',
            status_code=401,
            media_type="application/json",
        )

    # LLM 限额检查
    if path in LLM_ENDPOINTS:
        if not consume_llm_call(session):
            remaining = remaining_llm_calls(session)
            return Response(
                content=f'{{"detail":"LLM调用次数已用完","remaining":{remaining}}}',
                status_code=429,
                media_type="application/json",
            )

    # 注入 session 信息到 request state
    request.state.session = session
    response = await call_next(request)
    # 在响应头中返回剩余次数
    remaining = remaining_llm_calls(session)
    response.headers["X-LLM-Remaining"] = str(remaining)
    return response


# ---------------------------------------------------------------------------
# 认证 API
# ---------------------------------------------------------------------------

@app.post("/api/auth/login")
async def auth_login(req: LoginRequest):
    """登录，返回 session token"""
    from modules.auth import login as do_login
    session = do_login(req.username, req.password)
    if session is None:
        raise HTTPException(401, "用户名或密码错误")

    response = {
        "ok": True,
        "token": session.token,
        "role": session.role,
        "remaining": 3 if session.role == "tourist" else -1,
    }
    from fastapi.responses import JSONResponse
    resp = JSONResponse(content=response)
    resp.set_cookie(
        key="session_token",
        value=session.token,
        httponly=True,
        max_age=86400,  # 24h
        samesite="lax",
    )
    return resp


@app.post("/api/auth/logout")
async def auth_logout(request: Request):
    """登出"""
    from modules.auth import logout
    token = request.cookies.get("session_token") or ""
    logout(token)
    from fastapi.responses import JSONResponse
    resp = JSONResponse(content={"ok": True})
    resp.delete_cookie("session_token")
    return resp


@app.get("/api/auth/status")
async def auth_status(request: Request):
    """检查当前登录状态"""
    from modules.auth import get_session, remaining_llm_calls
    token = request.cookies.get("session_token")
    if not token:
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]

    if not token:
        return {"authenticated": False}

    session = get_session(token)
    if session is None:
        return {"authenticated": False}

    return {
        "authenticated": True,
        "role": session.role,
        "remaining": remaining_llm_calls(session),
        "limit": session.llm_call_limit if session.role == "tourist" else -1,
    }


@app.get("/api/health")
async def health_check():
    return {"status": "ok", "version": "0.2.0"}


@app.get("/api/templates")
async def list_strategy_templates():
    """列出所有可用的策略模板"""
    from utils.strategy_templates import get_all_templates
    templates = get_all_templates()
    return {
        name: {
            "id": t.id,
            "description": t.description,
            "params_schema": {k: v.__name__ for k, v in t.params_schema.items()},
        }
        for name, t in templates.items()
    }


@app.get("/api/domain-packs")
async def get_domain_packs():
    """返回领域包信息：组件类型、参数规格、依赖规则、可用场景"""
    pack = _get_domain_pack()

    # 组件类型及参数
    component_types = {}
    for type_id, spec in pack.get_component_types().items():
        params = {}
        for param_name, param_spec in spec.params.items():
            p = {"type": param_spec.type, "default": param_spec.default}
            if param_spec.min is not None:
                p["min"] = param_spec.min
            if param_spec.max is not None:
                p["max"] = param_spec.max
            if param_spec.unit:
                p["unit"] = param_spec.unit
            params[param_name] = p
        component_types[type_id] = {
            "display_name": spec.display_name,
            "params": params,
            "can_depend_on": spec.can_depend_on,
        }

    # 场景列表
    scenarios = []
    for s in pack.get_scenarios():
        scenarios.append({
            "id": s.id,
            "name": s.name,
            "description": s.description,
            "phases": [{"ticks": p.ticks, "rps": p.rps} for p in s.phases],
            "inject": [
                {"target": i.target, "action": i.action,
                 "at_tick": i.at_tick, "start_tick": i.start_tick,
                 "interval": i.interval, "amount": i.amount}
                for i in s.inject
            ],
        })

    return {
        "meta": pack.meta,
        "component_types": component_types,
        "dependency_rules": pack.dependency_rules,
        "scenarios": scenarios,
        "failure_taxonomy": pack.get_failure_taxonomy(),
        "concepts": pack.get_concepts(),
        "teaching_cards": pack.get_teaching_cards(),
    }


@app.post("/api/scenario/parse")
async def parse_scenario(req: ScenarioParseRequest):
    """将用户自然语言场景解析为 BlackBoxSpec"""
    from modules.scenario_parser import parse_scenario
    from utils.blackbox_spec import to_dict

    spec, msg = parse_scenario(req.nlp_input)
    if spec is None:
        return {"ok": False, "error": msg, "spec": None}

    return {"ok": True, "error": None, "spec": to_dict(spec)}


@app.post("/api/skill/parse")
async def parse_skill(req: SkillParseRequest):
    """将自然语言+步骤树解析为 MicroSkill"""
    from modules.skill_parser import parse_skill
    from utils.micro_skill import StepNode, step_node_from_dict, to_dict as ms_to_dict

    step_tree = None
    if req.step_tree is not None:
        try:
            step_tree = step_node_from_dict(req.step_tree)
        except Exception:
            step_tree = None

    skill, msg = parse_skill(req.nl_description, step_tree)
    if skill is None:
        return {"ok": False, "error": msg, "skill": None}

    return {"ok": True, "error": None, "skill": ms_to_dict(skill)}


@app.post("/api/simulation/run")
async def run_simulation(req: SimulationRunRequest):
    """运行性能引擎模拟"""
    from modules.performance_engine import PerformanceEngine
    from modules.domain_pack import Scenario, Phase, Inject
    from utils.micro_skill import from_dict as ms_from_dict, MicroSkill
    from utils.dependency_edge import DependencyEdge, from_dict as edge_from_dict

    # 确定场景
    scenario = None
    if req.custom_scenario:
        # 自定义场景
        phases = [Phase(ticks=p["ticks"], rps=p["rps"])
                  for p in req.custom_scenario.get("phases", [])]
        injects = []
        for i in req.custom_scenario.get("inject", []):
            injects.append(Inject(
                target=i["target"], action=i["action"],
                at_tick=i.get("at_tick"), start_tick=i.get("start_tick"),
                interval=i.get("interval"), amount=i.get("amount"),
            ))
        scenario = Scenario(
            id="custom", name="自定义场景",
            description="用户自定义场景", phases=phases, inject=injects,
        )
    elif req.scenario_id:
        # 从领域包查找场景
        pack = _get_domain_pack()
        scenario = pack.get_scenario(req.scenario_id)
        if scenario is None:
            raise HTTPException(400, f"场景 '{req.scenario_id}' 不存在")
    else:
        raise HTTPException(400, "需要提供 scenario_id 或 custom_scenario")

    # 预处理组件：将 micro_skill dict → MicroSkill 对象
    processed_components = {}
    for name, comp_data in req.components.items():
        comp = dict(comp_data)  # 浅拷贝
        # 处理 micro_skill
        if "micro_skill" in comp and isinstance(comp["micro_skill"], dict):
            comp["micro_skill"] = ms_from_dict(comp["micro_skill"])
        # 处理 dependency_edges
        if "dependency_edges" in comp:
            comp["dependency_edges"] = [
                edge_from_dict(e) if isinstance(e, dict) else e
                for e in comp["dependency_edges"]
            ]
        processed_components[name] = comp

    # 运行引擎
    try:
        engine = PerformanceEngine(processed_components, scenario)
        engine.run()
        report = engine.get_report()
        return {"ok": True, "report": report}
    except Exception as e:
        raise HTTPException(500, f"模拟运行失败: {e}")


@app.post("/api/simulation/convergence")
async def check_convergence(req: ConvergenceCheckRequest):
    """检查模拟结果是否收敛"""
    from modules.convergence_checker import check_convergence
    from utils.blackbox_spec import BlackBoxSpec, from_dict as spec_from_dict

    blackbox_spec = None
    if req.blackbox_spec is not None:
        blackbox_spec = spec_from_dict(req.blackbox_spec)

    result = check_convergence(req.report, blackbox_spec)
    return {
        "converged": result.converged,
        "gaps": [
            {"metric": g.metric, "target": g.target,
             "actual": g.actual, "passed": g.passed, "detail": g.detail}
            for g in result.gaps
        ],
        "summary": result.summary,
    }


@app.post("/api/simulation/review")
async def review_simulation(req: ReviewRequest):
    """LLM 审查模拟结果"""
    from modules.llm_review_engine import review
    from utils.micro_skill import from_dict as ms_from_dict
    from utils.blackbox_spec import BlackBoxSpec, from_dict as spec_from_dict

    micro_skills = None
    if req.micro_skills:
        micro_skills = {}
        for name, skill_data in req.micro_skills.items():
            if isinstance(skill_data, dict):
                micro_skills[name] = ms_from_dict(skill_data)

    blackbox_spec = None
    if req.blackbox_spec:
        blackbox_spec = spec_from_dict(req.blackbox_spec)

    result = review(req.report, micro_skills, blackbox_spec)
    return {
        "issues": [
            {"type": i.type, "severity": i.severity, "component": i.component,
             "description": i.description, "suggestion": i.suggestion,
             "is_guidance": i.is_guidance}
            for i in result.issues
        ],
        "knowledge_cards": result.knowledge_cards,
        "summary": result.summary,
    }
