# ---- 前端构建 ----
FROM node:20-alpine AS frontend-build
WORKDIR /build
COPY packages/frontend/package.json packages/frontend/package-lock.json ./
RUN npm ci
COPY packages/frontend/ .
RUN npm run build

# ---- 后端 + 前端产物 ----
FROM python:3.12-slim

WORKDIR /app

# 安装 uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# 复制后端项目文件
COPY packages/backend/pyproject.toml packages/backend/uv.lock ./
COPY packages/backend/src/ ./src/

# 复制前端构建产物
COPY --from=frontend-build /build/dist ./src/static/

# 安装依赖（Docker 环境用官方 PyPI，不走清华镜像）
RUN uv sync --no-dev --index-strategy unsafe-best-match

# 暴露端口
EXPOSE 80

# 启动（后端同时 serve 前端静态文件）
CMD ["uv", "run", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "80"]
