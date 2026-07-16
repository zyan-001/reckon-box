"""测试公共夹具。

确保 ai_tutor 相关测试不因本机环境变量中配置了 LLM API Key
而走真实 LLM 调用路径。ai_tutor 的测试用例基于模板 fallback 输出做断言
（如「知识覆盖率」「思考题」「建议尝试更极端的场景」），若环境配置了 LLM
则会调用真实模型导致断言失败且非确定性。

此处统一 mock modules.ai_tutor.get_llm_config 返回 None，保证测试确定性与隔离性。

design_reviewer 的测试自行 patch modules.design_reviewer.get_llm_config，
二者命名空间独立，不受此 fixture 影响。
"""

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _isolate_llm_config():
    """全局隔离 LLM 配置，避免测试依赖真实 LLM 调用。

    - mock modules.ai_tutor.get_llm_config 返回 None
      → ai_tutor 测试走 _generate_with_template 路径（断言基于模板输出）
    - design_reviewer 测试自行 patch，会覆盖此 mock
    - 其它测试不涉及 get_llm_config，不受影响
    """
    with patch("modules.ai_tutor.get_llm_config", return_value=None):
        yield
