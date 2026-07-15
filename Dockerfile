FROM python:3.12-slim

# 设置环境变量
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080

# 安装 uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# 复制依赖文件
COPY pyproject.toml uv.lock ./

# 使用 uv 安装依赖 (系统级安装，不需要虚拟环境)
RUN uv sync --frozen --no-cache

# 复制项目代码
COPY . .

# 暴露端口
EXPOSE $PORT

# 启动 Streamlit 应用，绑定到 0.0.0.0 和 Railway 提供的 PORT
CMD uv run streamlit run src/app.py --server.port=$PORT --server.address=0.0.0.0
