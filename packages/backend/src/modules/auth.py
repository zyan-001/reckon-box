"""认证模块：session管理、角色控制、tourist限额

角色：
  admin   - 完整访问，无次数限制
  tourist - 每session仅可调用LLM 3次（scenario/parse, skill/parse, simulation/review）

密码：从 .env 读取，和 LLM key 一样
  ADMIN_PASSWORD=xxx
  TOURIST_PASSWORD=xxx
"""

import secrets
import time
from dataclasses import dataclass, field


@dataclass
class Session:
    token: str
    role: str  # "admin" | "tourist"
    created_at: float = field(default_factory=time.time)
    # tourist 限额：LLM 调用次数
    llm_calls: int = 0
    llm_call_limit: int = 3


# 内存 session 存储 {token: Session}
_sessions: dict[str, Session] = {}

# 从环境读取密码（延迟加载）
_admin_password: str | None = None
_tourist_password: str | None = None


def _get_passwords():
    global _admin_password, _tourist_password
    if _admin_password is None:
        import os
        _admin_password = os.getenv("ADMIN_PASSWORD", "")
        _tourist_password = os.getenv("TOURIST_PASSWORD", "")
    return _admin_password, _tourist_password


def login(username: str, password: str) -> Session | None:
    """验证登录，返回 Session 或 None"""
    admin_pw, tourist_pw = _get_passwords()

    role = None
    if username == "admin" and admin_pw and password == admin_pw:
        role = "admin"
    elif username == "tourist" and tourist_pw and password == tourist_pw:
        role = "tourist"

    if role is None:
        return None

    token = secrets.token_hex(32)
    session = Session(token=token, role=role)
    _sessions[token] = session
    return session


def get_session(token: str) -> Session | None:
    """根据 token 获取 session"""
    return _sessions.get(token)


def logout(token: str) -> bool:
    """登出，删除 session"""
    if token in _sessions:
        del _sessions[token]
        return True
    return False


def consume_llm_call(session: Session) -> bool:
    """消耗一次 LLM 调用配额。返回 True 表示允许，False 表示已超限"""
    if session.role == "admin":
        return True
    if session.llm_calls >= session.llm_call_limit:
        return False
    session.llm_calls += 1
    return True


def remaining_llm_calls(session: Session) -> int:
    """返回剩余 LLM 调用次数"""
    if session.role == "admin":
        return -1  # 无限
    return max(0, session.llm_call_limit - session.llm_calls)


# 需要消耗 LLM 配额的端点
LLM_ENDPOINTS = {
    "/api/scenario/parse",
    "/api/skill/parse",
    "/api/simulation/review",
}
