# ReckonBox Backend

ReckonBox 是一个面向系统设计教学与评估的 Python 后端引擎集合，核心包括：

- 自然语言场景解析与架构变体识别
- MicroSkill 策略解析与参数校验
- 基于离散 tick 的性能模拟（`performance_engine`）
- 模拟后 LLM/规则混合审查与收敛检查

## 快速开始

```bash
uv sync
uv run pytest -q
```

## 环境变量

- `OPENAI_API_KEY`：启用 LLM 能力（可选）
- `OPENAI_BASE_URL`：可选自定义网关
- `RECKONBOX_LLM_MODEL`：可选模型名

未配置 API key 时，相关模块会自动降级到规则路径或模板路径。

## 项目结构

- `src/modules`：业务模块（parser/engine/review/convergence）
- `src/utils`：核心数据结构与转换工具
- `src/domain_packs`：场景配置
- `tests`：单元测试与集成测试