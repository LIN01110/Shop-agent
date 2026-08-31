"""Agent Harness 层：基于 DeepSeek Reasonix 三分区模型的上下文管理。"""

from .partition import ContextPartition, ImmutablePrefix, AppendOnlyLog, VolatileScratch
from .llm_client import HarnessLLMClient, DeepSeekBackend, LlamaCppBackend, MockBackend
from .harness import AgentHarness, TurnResult
from .guard import PrefixGuard
from .compactor import SessionCompactor, CompactionResult

__all__ = [
    "ContextPartition",
    "ImmutablePrefix",
    "AppendOnlyLog",
    "VolatileScratch",
    "HarnessLLMClient",
    "DeepSeekBackend",
    "LlamaCppBackend",
    "MockBackend",
    "AgentHarness",
    "TurnResult",
    "PrefixGuard",
    "SessionCompactor",
    "CompactionResult",
]
