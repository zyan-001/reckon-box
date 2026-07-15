# ReckonBox: 架构师的飞行模拟器

ReckonBox 是一款基于 Streamlit 构建的**建构主义软件架构教育沙盒 Web 应用**。它将复杂的分布式系统设计转化为可交互的推演游戏，让用户在对抗性压测中体验系统崩溃，从而实现架构认知的升级。

## 🌟 核心特性

- **🤖 AI 驱动的场景解析**：输入自然语言业务场景（如：“设计一个 QPS 达 100w 的订单系统”），AI 自动解析性能边界，并提取架构设计中的“分歧点”（如：强一致性 vs 最终一致性）。
- **🧠 自然语言驱动微观策略**：无需编写复杂代码，使用自然语言或伪代码（如：“如果 DB 延迟 > 500ms，则降级读取缓存”）定义组件策略，底层引擎自动执行。
- **🌪️ 对抗性混沌演练**：基于你的设计选项，系统刻意注入对抗性故障（如 DB 连接池耗尽），让失败在沙盒中自然涌现。
- **💡 “双维” AI 导师反馈**：结合“模拟后果”与“设计决策树”，AI 精准指出认知盲区和推理链断裂点（如：“你的重试策略放大了故障”），引导你在原设计上不断迭代。

## 🚀 快速开始

本项目使用 [uv](https://github.com/astral-sh/uv) 作为包管理器。

### 1. 安装依赖

```bash
# 安装 uv (如果尚未安装)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 创建虚拟环境并同步依赖
uv sync
```



### 2. 配置环境变量

复制 `.env.example` 到 `.env`，并填入你的 OpenAI API Key（用于 AI 导师和需求解析）：

```bash
cp .env.example .env
# 编辑 .env 文件
# OPENAI_API_KEY=sk-xxxxxx
```



### 3. 运行应用

```bash
uv run streamlit run src/app.py
```

打开浏览器访问 `http://localhost:8501` 即可体验。

## ☁️ 部署到 Railway

本项目已完美适配 [Railway](https://railway.app/)，支持一键 Docker 部署。

1. 在 Railway 中创建一个新项目，选择 **"Deploy from GitHub repo"**。
2. 选择你的 ReckonBox 仓库。
3. Railway 会自动识别项目根目录下的 `Dockerfile` 和 `railway.toml` 并开始构建。
4. **重要**：在 Railway 的 **Variables** 面板中，添加你的 `OPENAI_API_KEY`。
5. 构建完成后，在 **Settings -> Networking** 中点击 **Generate Domain**，即可获得公开访问链接！

*(注：Railway 会自动分配 `$PORT` 环境变量，Dockerfile 中已配置 Streamlit 监听该端口及 `0.0.0.0`)*

## 🧪 运行测试

本项目包含完整的单元测试，覆盖了核心的模拟引擎、策略执行器和领域解析器。

```bash
uv run pytest tests/
```



## 🏗️ 架构设计

ReckonBox 采用纯 Python 构建极简但绝对确定性的离散事件引擎，并采用 Streamlit 实现优雅的交互闭环。

- **前端**：Streamlit（负责 UI 交互、拓扑图渲染、状态管理）
- **后端引擎**：纯 Python 离散事件模拟引擎（`src/modules/simulation_engine.py`）
- **AI 模块**：基于 OpenAI API 的需求解析、决策树分析和教育反馈生成
- **数据流**：`Domain Pack (YAML) -> Design Spec (JSON) -> Simulation Engine -> Report (JSON) -> AI Tutor -> Markdown Feedback`



## 🤝 贡献指南

欢迎提交 Issue 和 Pull Request！在提交 PR 之前，请确保所有单元测试通过。

## 📄 许可证

MIT License