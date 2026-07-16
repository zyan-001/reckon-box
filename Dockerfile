FROM python:3.12-slim

WORKDIR /app

# 安装 uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# 复制后端项目文件
COPY packages/backend/pyproject.toml .
COPY packages/backend/uv.lock .

# 安装依赖
RUN uv sync --no-dev --frozen

# 复制源码
COPY packages/backend/src/ ./src/
COPY packages/backend/src/domain_packs/ ./src/domain_packs/

# 暴露端口
EXPOSE 80

# 启动
CMD ["uv", "run", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "80"]
