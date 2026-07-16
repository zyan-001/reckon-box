"""
LLM config loader.

统一负责：
- 从项目根目录 `.env` 加载环境变量（仅在未设置时注入）
- 读取 OpenAI 兼容接口所需配置
"""

from __future__ import annotations

import os
from pathlib import Path

_DOTENV_LOADED = False


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _load_dotenv_once() -> None:
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return

    repo_root = Path(__file__).resolve().parents[2]
    dotenv_path = repo_root / ".env"
    if dotenv_path.exists():
        for raw in dotenv_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export ") :].strip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = _strip_quotes(value.strip())
            if key and key not in os.environ:
                os.environ[key] = value

    _DOTENV_LOADED = True


def get_llm_config() -> dict | None:
    """读取 LLM 配置（仅环境变量 + `.env`）。"""
    _load_dotenv_once()

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None

    model = (
        os.environ.get("OPENAI_MODEL")
        or os.environ.get("RECKONBOX_LLM_MODEL")
        or "deepseek-v4-flash"
    )
    base_url = os.environ.get("OPENAI_BASE_URL")
    return {"api_key": api_key, "base_url": base_url or None, "model": model}


def is_llm_configured() -> bool:
    return get_llm_config() is not None
