"""Output-length limits the server rejects, and how to recover.

Agents ask for long outputs by default (``settings.default_max_tokens``). A server
whose model allows less says so in its error, in one of a few shapes:

* OpenAI: "max_tokens is too large: 131072. This model supports at most 16384
  completion tokens"
* Anthropic: "max_tokens: 131072 > 64000, which is the maximum allowed number of
  output tokens for claude-..."
* vLLM and friends: "This model's maximum context length is 32768 tokens. However, you
  requested 140000 tokens (8928 in the messages, 131072 in the completion)", or
  "... and your request has 8928 input tokens (131072 > 32768 - 8928)"

``repair`` returns a copy of the chat model asking for what fits, or None.
"""

from __future__ import annotations

import re
from typing import Any

MIN_OUTPUT = 1024
CONTEXT_MARGIN = 64


def _int(pattern: str, message: str) -> int | None:
    m = re.search(pattern, message, re.IGNORECASE)
    return int(m.group(1).replace(",", "")) if m else None


def fitting(message: str) -> int | None:
    """The largest output the error says would be accepted; 0 = drop the limit; None = not this."""
    if not re.search(r"max_tokens|max_completion_tokens|maximum context length|output tokens|"
                     r"completion tokens", message, re.IGNORECASE):
        return None
    if (n := _int(r"at most ([\d,]+) (?:completion|output) tokens", message)) is not None:
        return n
    if (n := _int(r"> ([\d,]+), which is the maximum", message)) is not None:
        return n
    context = _int(r"maximum context length is ([\d,]+)", message)
    used = (_int(r"([\d,]+) in the messages", message)
            or _int(r"([\d,]+) input tokens", message)
            or _int(r"request has ([\d,]+)", message))
    if context and used is not None:
        return max(MIN_OUTPUT, context - used - CONTEXT_MARGIN)
    if context:
        return 0  # we don't know how long the prompt is: let the server use what's left
    return None


def repair(chat: Any, message: str, provider_type: str) -> Any | None:
    n = fitting(message)
    current = getattr(chat, "max_tokens", None)
    if n is None or not current:
        return None
    if n == 0:
        if provider_type == "anthropic":
            n = 8192  # Claude needs a limit
        else:
            return _copy(chat, {"max_tokens": None})
    if n >= current:
        return None  # nothing to gain: don't loop
    update: dict[str, Any] = {"max_tokens": n}
    thinking = getattr(chat, "thinking", None)
    if isinstance(thinking, dict) and thinking.get("budget_tokens", 0) >= n - MIN_OUTPUT:
        update["thinking"] = {**thinking, "budget_tokens": max(MIN_OUTPUT, n // 2)}
    return _copy(chat, update)


def _copy(chat: Any, update: dict[str, Any]) -> Any | None:
    try:
        return chat.model_copy(update=update)
    except Exception:  # noqa: BLE001
        return None
