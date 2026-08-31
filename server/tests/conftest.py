"""
Purpose: pytest 全局配置：测试环境禁用 LLM、禁用 Ark Embedding。
"""

import os


os.environ["USE_VLM"] = "false"
os.environ["USE_LLM"] = "false"
os.environ["USE_ARK_EMBEDDING"] = "false"
os.environ["USE_VISUAL_EMBEDDING"] = "false"
