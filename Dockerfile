FROM python:3.12-slim

WORKDIR /app

# 安装 uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# 复制整个后端项目
COPY packages/backend/ .

# 安装依赖
RUN uv sync --no-dev --frozen

# 暴露端口
EXPOSE 80

# 启动
CMD ["uv", "run", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "80"]
