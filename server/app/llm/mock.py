"""Deterministic chat model for development, demos and tests (no network).

Scripts are registered per agent id: ``set_script(agent_id, fn)`` where
``fn(messages, tools) -> AIMessage``. Without a script the model answers the
newest inbox item in plain text, which the runtime delivers as a reply.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool

Script = Callable[[list[BaseMessage], list[dict[str, Any]]], AIMessage]

_scripts: dict[str, Script] = {}


def set_script(agent_id: str, fn: Script | None) -> None:
    if fn is None:
        _scripts.pop(agent_id, None)
    else:
        _scripts[agent_id] = fn


def clear_scripts() -> None:
    _scripts.clear()


class MockChatModel(BaseChatModel):
    agent_id: str = ""

    @property
    def _llm_type(self) -> str:
        return "pantheon-mock"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any):  # type: ignore[override]
        return self.bind(tools=[convert_to_openai_tool(t) for t in tools], **kwargs)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        tools = kwargs.get("tools") or []
        script = _scripts.get(self.agent_id)
        if script is not None:
            msg = script(list(messages), tools)
        else:
            last = next((m for m in reversed(messages) if isinstance(m, HumanMessage)), None)
            text = str(last.content) if last else ""
            msg = AIMessage(content=f"(mock) Noted: {text.strip().splitlines()[-1][:200]}"
                            if text.strip() else "(mock) Nothing to do.")
        chars = sum(len(str(m.content)) for m in messages)
        msg.usage_metadata = {
            "input_tokens": chars // 4,
            "output_tokens": max(1, len(str(msg.content)) // 4),
            "total_tokens": chars // 4 + max(1, len(str(msg.content)) // 4),
        }
        return ChatResult(generations=[ChatGeneration(message=msg)])
