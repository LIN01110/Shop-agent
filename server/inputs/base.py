"""
Purpose: 输入处理基础类型：ProcessedInput 数据类定义。
"""

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class ProcessedInput:
    text: str
    modality: str = "text"
    image_summary: str = ""
    visual_matches: list[dict] = field(default_factory=list)


class InputProcessor(Protocol):
    def process(self, raw: str, **kwargs) -> ProcessedInput:
        ...


class TextProcessor:
    def process(self, raw: str, **kwargs) -> ProcessedInput:
        return ProcessedInput(text=raw.strip(), modality="text")


class ASRProcessor:
    def process(self, raw: str) -> ProcessedInput:
        raise NotImplementedError("ASRProcessor is reserved for voice input.")


class VLMProcessor:
    def process(self, raw: str) -> ProcessedInput:
        raise NotImplementedError("VLMProcessor is reserved for image input.")
