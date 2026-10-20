"""Deterministic chat model for development, demos and tests (no network).

Scripts are registered per agent id: ``set_script(agent_id, fn)`` where
``fn(messages, tools) -> AIMessage``. Without a script the model answers the
newest inbox item in plain text, which the runtime delivers as a reply.
"""

from __future__ import annotations

import random
import re
from collections.abc import Callable, Sequence
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool

from app.core.config import settings

Script = Callable[[list[BaseMessage], list[dict[str, Any]]], AIMessage]

_scripts: dict[str, Script] = {}


def set_script(agent_id: str, fn: Script | None) -> None:
    if fn is None:
        _scripts.pop(agent_id, None)
    else:
        _scripts[agent_id] = fn


def clear_scripts() -> None:
    _scripts.clear()


DEMO_HOPS = 4
_HOP = re.compile(r"\[relay (\d+)\]")


def _demo(messages: list[BaseMessage], tools: list[dict[str, Any]]) -> AIMessage | None:
    """Demo mode (PANTHEON_MOCK_DEMO): an org that looks busy without a model. An agent
    that gets work passes it to a colleague with send_message, up to DEMO_HOPS relays,
    so the UI shows agents working and messages moving between them."""
    if isinstance(messages[-1], ToolMessage):
        return AIMessage(content="(demo) Passed it on.")
    names = {t.get("function", {}).get("name") for t in tools}
    last = next((str(m.content) for m in reversed(messages) if isinstance(m, HumanMessage)), "")
    if "finish_step" in names and "# Flow step:" in last:
        # A flow step: finish it with a made-up result naming what it built on.
        title = last.split("# Flow step:", 1)[1].splitlines()[0].strip()
        before = re.findall(r"^### (.+?) \(", last, re.MULTILINE)
        output = f"(demo) {title} done" + (f", building on {', '.join(before)}." if before else ".")
        return AIMessage(content="", tool_calls=[{
            "name": "finish_step", "id": f"demo_{random.getrandbits(40):x}", "args": {"output": output}}])
    if "send_message" not in names:
        return None
    system = next((str(m.content) for m in messages if isinstance(m, SystemMessage)), "")
    # The colleague list runs to the prompt's next heading.
    team = ""
    if "# Your colleagues" in system:
        team = system.split("# Your colleagues", 1)[1].split("\n#", 1)[0]
    names = re.findall(r"^- ([A-Za-z][^:#\n]*):", team, re.MULTILINE)  # not "- #channel …"
    hop = max((int(n) for n in _HOP.findall(last)), default=0)
    if not names or hop >= DEMO_HOPS:
        return None
    to = names[random.randrange(len(names))]
    topic = re.sub(r"\[relay \d+\]\s*", "", last.strip().splitlines()[-1])[:160]
    return AIMessage(content="", tool_calls=[{
        "name": "send_message", "id": f"demo_{random.getrandbits(40):x}",
        "args": {"to": to, "content": f"[relay {hop + 1}] {topic}"},
    }])


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
        elif settings.mock_demo and (demo := _demo(list(messages), tools)) is not None:
            msg = demo
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
