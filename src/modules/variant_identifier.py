"""
Variant Identifier (LLM 设计变更点识别)
======================================
基于 BlackBoxSpec 识别影响架构设计的变更点（design variants）。

变更点分两种影响类型：
  - parameter_mapping: 选中某 option 后，按 parameter_mappings 修改组件参数
    例：锁竞争 → timeout_ms *= 2、有效吞吐降低
  - llm_context: 仅作为 LLM 上下文提示，不直接改参数
    例：数据一致性要求 → 影响 LLM 的分析和引导

LLM 调用策略与 design_reviewer.py 一致：
  复用 get_llm_config() 读取配置（环境变量 > st.secrets）。
  LLM 不可用或调用失败时返回空 variants 列表 + 提示消息。

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
from utils.blackbox_spec import BlackBoxSpec, DesignVariant


# ---------------------------------------------------------------------------
# 内部辅助函数
# ---------------------------------------------------------------------------

def _spec_to_dict(blackbox_spec) -> dict:
    """将 BlackBoxSpec 或 dict 统一转为可序列化的 dict。

    支持 BlackBoxSpec 实例与 dict 表示；其它类型返回空 dict，保证容错。
    """
    if isinstance(blackbox_spec, dict):
        return blackbox_spec
    if isinstance(blackbox_spec, BlackBoxSpec):
        return {
            "scenario_description": blackbox_spec.scenario_description,
            "functional_points": list(blackbox_spec.functional_points),
            "performance_bounds": dict(blackbox_spec.performance_bounds),
            "exception_scenarios": list(blackbox_spec.exception_scenarios),
            # design_variants 不传给 LLM，由本模块产出
            "design_variants": [],
        }
    if is_dataclass(blackbox_spec) and not isinstance(blackbox_spec, type):
        return asdict(blackbox_spec)
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


def _variant_from_dict(data: dict) -> DesignVariant:
    """将 dict 转为 DesignVariant，字段缺失时使用默认值。

    保证 LLM 返回缺字段时也能正常构建对象。
    """
    if not isinstance(data, dict):
        return DesignVariant(
            id="", name="", description="",
            options=[], impact_type="llm_context", parameter_mappings=[],
        )

    impact_type = data.get("impact_type", "llm_context")
    # 归一化 impact_type，只允许两种合法值
    if impact_type not in ("parameter_mapping", "llm_context"):
        impact_type = "llm_context"

    options = data.get("options", []) or []
    if not isinstance(options, list):
        options = []

    parameter_mappings = data.get("parameter_mappings", []) or []
    if not isinstance(parameter_mappings, list):
        parameter_mappings = []
    # llm_context 类型不依赖 parameter_mappings，强制清空
    if impact_type == "llm_context":
        parameter_mappings = []

    return DesignVariant(
        id=data.get("id", ""),
        name=data.get("name", ""),
        description=data.get("description", ""),
        options=options,
        impact_type=impact_type,
        parameter_mappings=parameter_mappings,
    )


# ---------------------------------------------------------------------------
# prompt 构建
# ---------------------------------------------------------------------------

def build_identify_prompt(blackbox_spec) -> str:
    """构建给 LLM 的设计变更点识别提示词。

    prompt 包含：
      - BlackBoxSpec 的功能点、性能边界、异常场景
      - 要求 LLM 识别影响架构设计的变更点
      - 每个变更点的字段说明（id/name/description/options/impact_type/parameter_mappings）
      - impact_type 两种类型的说明与示例
      - 要求 LLM 返回 JSON 数组格式
    """
    spec_dict = _spec_to_dict(blackbox_spec)

    functional_points = spec_dict.get("functional_points", []) or []
    performance_bounds = spec_dict.get("performance_bounds", {}) or {}
    exception_scenarios = spec_dict.get("exception_scenarios", []) or []
    scenario_description = spec_dict.get("scenario_description", "")

    fp_json = json.dumps(functional_points, ensure_ascii=False, indent=2)
    pb_json = json.dumps(performance_bounds, ensure_ascii=False, indent=2)
    es_json = json.dumps(exception_scenarios, ensure_ascii=False, indent=2)

    prompt = f"""你是一位资深系统架构师，正在基于用户场景的黑盒规格识别影响架构设计的变更点。

## 用户场景
{scenario_description}

## 功能点
```json
{fp_json}
```

## 性能边界
```json
{pb_json}
```

## 异常场景
```json
{es_json}
```

## 你的任务
请基于上述黑盒规格，识别出影响架构设计的变更点（design variants）。
变更点是学生在设计系统时需要做出选择的设计维度，例如：
- 锁竞争场景
- 数据一致性要求
- 缓存策略
- 读写比例
- 数据规模
- 一致性 vs 可用性权衡

## 变更点结构
每个变更点必须包含以下字段：
- id: 唯一标识（如 "lock_contention"），使用英文 snake_case
- name: 中文名（如 "锁竞争场景"）
- description: 变更点描述，说明该维度的设计含义
- options: 选项列表，每个选项为 {{"id", "label", "description"}}
- impact_type: 变更点的影响类型，取值 "parameter_mapping" 或 "llm_context"
- parameter_mappings: 仅当 impact_type=parameter_mapping 时需要提供，结构如下：
  [
    {{
      "option_id": "high",
      "component_type": "database",
      "param": "timeout_ms",
      "multiplier": 2,
      "offset": 0
    }}
  ]

## impact_type 两种类型的说明

### 1. parameter_mapping（参数映射型）
该变更点会**直接影响引擎模拟参数**。选中某 option 后，按 parameter_mappings 中的规则修改组件参数：
  `param = param * multiplier + offset`

示例 — 锁竞争场景：
```json
{{
  "id": "lock_contention",
  "name": "锁竞争场景",
  "description": "并发场景下数据库行锁/表锁的竞争程度，影响事务等待时间",
  "options": [
    {{"id": "low", "label": "低锁竞争", "description": "竞争少，事务快速完成"}},
    {{"id": "high", "label": "高锁竞争", "description": "竞争激烈，事务等待时间翻倍"}}
  ],
  "impact_type": "parameter_mapping",
  "parameter_mappings": [
    {{"option_id": "high", "component_type": "database", "param": "timeout_ms", "multiplier": 2, "offset": 0}}
  ]
}}
```

### 2. llm_context（LLM 上下文型）
该变更点**仅影响 LLM 的分析和引导**，不影响引擎参数。parameter_mappings 应为空数组 `[]`。

示例 — 数据一致性要求：
```json
{{
  "id": "consistency_requirement",
  "name": "数据一致性要求",
  "description": "系统对数据一致性的要求程度，影响 LLM 在分析与引导时关注的角度",
  "options": [
    {{"id": "strong", "label": "强一致性", "description": "所有读操作必须读到最新写入"}},
    {{"id": "eventual", "label": "最终一致性", "description": "允许短暂不一致，追求更高可用性"}}
  ],
  "impact_type": "llm_context",
  "parameter_mappings": []
}}
```

## 输出格式

请返回严格的 JSON 数组，每个元素是一个变更点，结构如上所示。

要求：
- 只返回 JSON 数组，不要附加其他解释文字
- 每个变更点的 impact_type 必须是 "parameter_mapping" 或 "llm_context" 之一
- impact_type=parameter_mapping 时，必须提供 parameter_mappings，且每个映射包含 option_id/component_type/param/multiplier/offset
- impact_type=llm_context 时，parameter_mappings 必须为空数组 `[]`
- 通常识别 3-6 个变更点即可
"""
    return prompt


# ---------------------------------------------------------------------------
# 主接口
# ---------------------------------------------------------------------------

def identify_variants(blackbox_spec) -> tuple[list[DesignVariant], str]:
    """识别设计变更点。

    Args:
        blackbox_spec: BlackBoxSpec 对象或其 dict 表示

    Returns:
        (variants 列表, 状态消息)
        - 成功: (variants, "LLM 识别完成，共 N 个变更点")
        - 未配置 LLM: ([], "请配置 LLM")
        - 调用失败: ([], f"LLM 调用失败: {error}")
        - 解析失败: ([], "LLM 返回格式无法解析")
    """
    config = get_llm_config()
    if config is None:
        return ([], "请配置 LLM")

    try:
        from openai import OpenAI
    except ImportError:
        return ([], "LLM 调用失败: openai 包未安装")

    prompt = build_identify_prompt(blackbox_spec)

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
        print(f"[ReckonBox] variant identify LLM call failed ({e})",
              file=sys.stderr)
        return ([], f"LLM 调用失败: {e}")

    try:
        raw_variants = _extract_json(content)
    except ValueError:
        return ([], "LLM 返回格式无法解析")

    variants = [_variant_from_dict(item) for item in raw_variants]
    return (variants, f"LLM 识别完成，共 {len(variants)} 个变更点")
