"""ModelPort：调用 MiniMax 改代码（本项目由 OpenAI 兼容端点实现）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, Sequence


class ModelError(Exception):
    """模型调用失败：鉴权、限流、空回复、超时都走这里，不静默降级。"""

    def __init__(self, message: str, *, code: str = "model_error", retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class ModelInfo:
    model: str
    base_url: str


@dataclass(frozen=True)
class PromptUnit:
    """一段提示。role 只有 system / user 两种，由 core 决定装什么。"""

    role: str
    text: str


@dataclass(frozen=True)
class ModelRequest:
    """一次完成的输入：提示单元 + 输出预算。

    输出预算是 token 数，不是 max_tokens 这个厂商参数名——签名不出现
    OpenAI 形态。
    """

    units: Sequence[PromptUnit]
    output_allowance: int = 32768


@dataclass(frozen=True)
class ModelReply:
    text: str
    finish_reason: str = ""
    usage: dict[str, object] = field(default_factory=dict)
    latency_ms: int = 0
    request_id: str = ""
    model: str = ""


class ModelPort(Protocol):
    """模型端口：一次请求换一次回复。"""

    def complete(self, request: ModelRequest) -> ModelReply:
        ...

    def info(self) -> ModelInfo:
        ...
