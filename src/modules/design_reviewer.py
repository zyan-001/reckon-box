"""
Design Reviewer (LLM 设计阶段评估)
==================================
在模拟运行之前，由 LLM 对学生提交的设计方案做静态评估。

与 ai_tutor.py 的区别：
  ai_tutor 分析「模拟结果」→ 设计导致的后果
  design_reviewer 分析「设计本身」→ 设计阶段的逻辑/安全/降级缺陷

LLM 调用策略与 ai_tutor.py 一致：
  复用 get_llm_config() 读取配置（环境变量 > st.secrets）。
  LLM 不可用或调用失败时返回空 issues 列表 + 提示消息。

配置来源：
  OPENAI_API_KEY  / st.secrets["openai_api_key"]   — API 密钥（必需）
  OPENAI_BASE_URL / st.secrets["openai_base_url"]  — 自定义 API 端点（可选）
  RECKONBOX_LLM_MODEL / st.secrets["llm_model"]    — 模型名（默认 gpt-4o-mini）
"""

import json
import re
import sys
from dataclasses import asdict, is_dataclass

from modules.ai_tutor import get_llm_config


# ---------------------------------------------------------------------------
# 内部辅助函数
# ---------------------------------------------------------------------------

def _component_to_dict(comp) -> dict:
    """将单个组件统一转为 dict（支持 dict 和 dataclass 实例）。

    design_spec 中的 components 可能是 dict 或 ComponentConfig，
    这里统一成 dict 便于 JSON 序列化。
    """
    if isinstance(comp, dict):
        return dict(comp)
    if is_dataclass(comp) and not isinstance(comp, type):
        return asdict(comp)
    return {}


def _extract_json(text: str) -> list[dict]:
    """从 LLM 返回的文本中提取 JSON 数组。

    处理以下情况：
      - 纯 JSON 数组
      - ```json ... ``` 包裹
      - ``` ... ``` 包裹（无语言标识）
      - JSON 前后包含解释文字

    返回 list[dict]。无法提取时抛出 ValueError。
    """
    # 优先提取 ```json ... ``` 或 ``` ... ``` 代码块
    match = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.DOTALL)
    if match:
        text = match.group(1)

    text = text.strip()

    # 尝试直接解析
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        pass

    # 兜底：截取第一个 [ 到最后一个 ] 的子串
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            data = json.loads(text[start:end + 1])
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass

    raise ValueError("无法从 LLM 返回内容中提取 JSON")


# ---------------------------------------------------------------------------
# prompt 构建
# ---------------------------------------------------------------------------

def build_review_prompt(design_spec: dict) -> str:
    """构建给 LLM 的设计审查提示词。

    prompt 包含：
      - 完整拓扑 JSON（组件名、类型、参数、依赖关系）
      - 每个组件的 description 文本（如果有）
      - 要求 LLM 检查的 5 类问题
      - 要求 LLM 返回 JSON 数组格式
    """
    components = design_spec.get("components", [])

    # 构建拓扑 JSON 与组件描述
    topology = []
    descriptions = []
    for comp in components:
        comp_dict = _component_to_dict(comp)
        topology.append(comp_dict)
        desc = comp_dict.get("description")
        name = comp_dict.get("name", "")
        if desc:
            descriptions.append(f"- {name}: {desc}")

    topology_json = json.dumps(topology, ensure_ascii=False, indent=2)

    prompt = f"""你是一位资深系统架构师，正在审查学生提交的系统设计方案。
请基于设计拓扑和组件描述，找出设计方案中存在的问题。

## 设计拓扑 JSON
```json
{topology_json}
```
"""
    if descriptions:
        prompt += "\n## 组件描述\n"
        prompt += "\n".join(descriptions) + "\n"

    prompt += """
## 你需要检查以下 5 类问题

1. **描述与拓扑一致性**（type=missing_dependency）
   组件描述中提到的依赖关系是否在拓扑 dependencies 中实际存在。
   例如描述提到「调用支付服务」但拓扑中没有 payment_service 依赖。

2. **跨组件逻辑合理性**（type=logic_issue）
   组件之间的协作逻辑是否符合业务惯例。
   例如「先扣库存再创建订单」与「先创建订单再扣库存」的顺序差异。

3. **缺失环节**（type=missing_component）
   描述或拓扑中暗示但未实现的组件。
   例如描述提到支付但没有支付相关组件，或服务依赖了不存在的组件。

4. **安全反模式**（type=anti_pattern）
   无补偿机制的部分失败、无熔断的级联调用、无幂等的重试等。

5. **降级策略完整性**（type=missing_degradation）
   依赖故障时的行为是否描述。例如下游服务挂掉时是否有兜底逻辑。

## 输出格式

请返回严格的 JSON 数组，每个元素是一个 issue，结构如下：

```json
[
  {
    "type": "missing_dependency" | "logic_issue" | "missing_component" | "anti_pattern" | "missing_degradation",
    "severity": "high" | "medium" | "low",
    "component": "组件名",
    "description": "问题描述",
    "suggestion": "改进建议"
  }
]
```

要求：
- 只返回 JSON 数组，不要附加其他解释文字
- 如果设计没有问题，返回空数组 `[]`
- severity 用于标注问题严重程度
- component 字段填入相关组件名；如果是跨组件问题，填入主要相关组件名
"""
    return prompt


# ---------------------------------------------------------------------------
# 主接口
# ---------------------------------------------------------------------------

def review_design(design_spec: dict) -> tuple[list[dict], str]:
    """审查设计方案，返回 (issues 列表, 状态消息)。

    参数：
        design_spec: DesignSpec 的 dict 表示，包含 domain 和 components。
                     components 中的元素可以是 dict 或 ComponentConfig。

    返回：
        tuple (issues, message)
        - issues: list[dict]，每个 issue 包含
                  type/severity/component/description/suggestion
        - message: str，状态消息
          - 成功: "LLM 评估完成，发现 N 个问题"
          - 未配置 LLM: "未配置 LLM，跳过架构评估"
          - 调用失败: "LLM 调用失败: {error}"
          - 解析失败: "LLM 返回格式无法解析"
    """
    config = get_llm_config()
    if config is None:
        return ([], "未配置 LLM，跳过架构评估")

    try:
        from openai import OpenAI
    except ImportError:
        return ([], "LLM 调用失败: openai 包未安装")

    prompt = build_review_prompt(design_spec)

    try:
        client = OpenAI(
            api_key=config["api_key"],
            base_url=config.get("base_url"),
        )
        response = client.chat.completions.create(
            model=config.get("model", "gpt-4o-mini"),
            messages=[{"role": "user", "content": prompt}],
        )
        content = response.choices[0].message.content
    except Exception as e:
        print(f"[ReckonBox] design review LLM call failed ({e})",
              file=sys.stderr)
        return ([], f"LLM 调用失败: {e}")

    try:
        issues = _extract_json(content)
    except ValueError:
        return ([], "LLM 返回格式无法解析")

    return (issues, f"LLM 评估完成，发现 {len(issues)} 个问题")
