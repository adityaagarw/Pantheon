"""Recorded, retried LLM invocation.

Every call — turn step, compaction, meeting speech — goes through
``invoke``: it streams (so observers can watch agents think), retries
transient provider failures with backoff, and records the full request and
response in ``llm_calls`` whether it succeeds or not.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import httpx
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from app.core.db import SessionLocal
from app.events import bus
from app.llm import effort
from app.llm.providers import ResolvedModel
from app.models import LlmCall

log = logging.getLogger(__name__)
MAX_ATTEMPTS = 4
STREAM_FLUSH_SECONDS = 0.15


class LLMFailure(RuntimeError):
    def __init__(self, message: str, transient: bool) -> None:
        super().__init__(message)
        self.transient = transient


def is_transient(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, httpx.TimeoutException, httpx.NetworkError,
                        ConnectionError)):
        return True
    status = getattr(exc, "status_code", None) or getattr(
        getattr(exc, "response", None), "status_code", None)
    if isinstance(status, int):
        return status == 408 or status == 409 or status == 429 or status >= 500
    name = type(exc).__name__
    return name in {"APIConnectionError", "APITimeoutError", "RateLimitError",
                    "InternalServerError", "ServiceUnavailableError", "OverloadedError"}


def serialize_messages(messages: Sequence[BaseMessage]) -> list[dict[str, Any]]:
    out = []
    for m in messages:
        role = {"system": "system", "human": "user", "ai": "assistant", "tool": "tool"}.get(
            m.type, m.type)
        item: dict[str, Any] = {"role": role, "content": _text(m.content)}
        if isinstance(m, AIMessage) and m.tool_calls:
            item["tool_calls"] = [{"id": tc.get("id"), "name": tc["name"], "args": tc["args"]}
                                  for tc in m.tool_calls]
        if isinstance(m, ToolMessage):
            item["tool_call_id"] = m.tool_call_id
        out.append(item)
    return out


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif isinstance(block, dict) and block.get("type") in ("image_url", "image"):
                parts.append(" [image]")
        return "".join(parts)
    return str(content or "")


def text_of(msg: BaseMessage) -> str:
    return _text(msg.content)


def reasoning_of(msg: BaseMessage) -> str:
    kw = getattr(msg, "additional_kwargs", {}) or {}
    for key in ("reasoning_content", "reasoning"):
        val = kw.get(key)
        if isinstance(val, str) and val.strip():
            return val
        if isinstance(val, dict) and isinstance(val.get("summary"), list):
            return "\n".join(str(s.get("text", "")) for s in val["summary"] if isinstance(s, dict))
    if isinstance(msg.content, list):
        return "\n".join(
            b.get("thinking") or b.get("reasoning") or b.get("text", "")
            for b in msg.content
            if isinstance(b, dict) and b.get("type") in ("thinking", "reasoning")
        )
    return ""


THINK = re.compile(r"(?s)<think>(.*?)(?:</think>|$)")


def split_think(text: str) -> tuple[str, str]:
    """Some local models inline <think>…</think> into the content: (content, thinking)."""
    if "<think>" not in text:
        return text, ""
    thinking = "\n".join(m.strip() for m in THINK.findall(text) if m.strip())
    return THINK.sub("", text).strip(), thinking


def strip_think(text: str) -> str:
    return split_think(text)[0]


async def invoke(
    model: ResolvedModel,
    messages: list[BaseMessage],
    *,
    org_id: str,
    agent_id: str,
    turn_id: str | None,
    purpose: str,
    tools: list[dict[str, Any]] | None = None,
    stream: bool = True,
    on_attempt_error: Callable[[int, BaseException], Awaitable[None]] | None = None,
) -> tuple[AIMessage, str]:
    """Returns (message, llm_call_id). Raises ``LLMFailure`` after retries."""
    runnable: Any = model.chat.bind_tools(tools) if tools else model.chat
    request = {
        "messages": serialize_messages(messages),
        "tools": [t["function"]["name"] for t in tools or []],
        "model": model.model_id,
        "provider": model.provider_type,
    }
    last_exc: BaseException | None = None
    adapted = False
    for attempt in range(1, MAX_ATTEMPTS + 1):
        started = time.monotonic()
        try:
            msg = await _call(runnable, messages, stream, org_id, agent_id, turn_id)
            latency = int((time.monotonic() - started) * 1000)
            usage = msg.usage_metadata or {}
            tin, tout = int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))
            content, inline = split_think(text_of(msg))
            if content != text_of(msg):
                kwargs = dict(msg.additional_kwargs)
                if inline and not reasoning_of(msg):
                    kwargs["reasoning_content"] = inline
                msg = AIMessage(content=content, tool_calls=msg.tool_calls,
                                usage_metadata=msg.usage_metadata, id=msg.id,
                                additional_kwargs=kwargs)
            call_id = await _record(
                org_id, agent_id, turn_id, purpose, model, request,
                {"content": content, "reasoning": reasoning_of(msg),
                 "tool_calls": [{"id": tc.get("id"), "name": tc["name"], "args": tc["args"]}
                                for tc in msg.tool_calls]},
                tin, tout, latency, None,
            )
            await bus.publish("llm.call", {
                "id": call_id, "turnId": turn_id, "purpose": purpose, "model": model.model_id,
                "inputTokens": tin, "outputTokens": tout, "latencyMs": latency,
                "costUsd": model.cost(tin, tout), "toolCalls": len(msg.tool_calls),
            }, org_id=org_id, agent_id=agent_id)
            return msg, call_id
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - classify every provider failure
            last_exc = exc
            latency = int((time.monotonic() - started) * 1000)
            transient = is_transient(exc)
            await _record(org_id, agent_id, turn_id, purpose, model, request, {}, 0, 0, latency,
                          f"{type(exc).__name__}: {exc}"[:4000])
            log.warning("LLM call failed (attempt %s, transient=%s): %s", attempt, transient, exc)
            if not transient and not adapted:
                # A server that doesn't accept this effort level: switch to the closest
                # one it does (learned from its own error) and try again right away.
                fixed = effort.repair(model.chat, str(exc), model.provider_id, model.model_id,
                                      model.effort)
                if fixed is not None:
                    adapted = True
                    model.chat = fixed
                    runnable = model.chat.bind_tools(tools) if tools else model.chat
                    log.info("model rejected reasoning effort %r; retrying with %r",
                             model.effort, getattr(fixed, "reasoning_effort", None))
                    continue
            if on_attempt_error is not None:
                await on_attempt_error(attempt, exc)
            if not transient or attempt == MAX_ATTEMPTS:
                raise LLMFailure(f"{type(exc).__name__}: {exc}", transient) from exc
            await asyncio.sleep(min(30.0, 1.5 * 2 ** (attempt - 1)) + random.random())
    raise LLMFailure(str(last_exc), True)  # pragma: no cover


async def _call(runnable: Any, messages: list[BaseMessage], stream: bool, org_id: str,
                agent_id: str, turn_id: str | None) -> AIMessage:
    if not stream:
        result = await runnable.ainvoke(messages)
        return result if isinstance(result, AIMessage) else AIMessage(content=str(result))
    acc: AIMessageChunk | None = None
    buf, think, last_flush = "", "", time.monotonic()

    async def flush() -> None:
        nonlocal buf, think, last_flush
        if think:
            await bus.publish("agent.thinking", {"turnId": turn_id, "delta": think},
                              org_id=org_id, agent_id=agent_id, persist=False)
        if buf:
            await bus.publish("agent.stream", {"turnId": turn_id, "delta": buf},
                              org_id=org_id, agent_id=agent_id, persist=False)
        buf, think, last_flush = "", "", time.monotonic()

    async for chunk in runnable.astream(messages):
        acc = chunk if acc is None else acc + chunk
        buf += _text(chunk.content)
        rc = (chunk.additional_kwargs or {}).get("reasoning_content")
        if isinstance(rc, str):
            think += rc
        if (buf or think) and time.monotonic() - last_flush >= STREAM_FLUSH_SECONDS:
            await flush()
    await flush()
    if acc is None:
        return AIMessage(content="")
    return AIMessage(
        content=acc.content, tool_calls=acc.tool_calls, usage_metadata=acc.usage_metadata,
        additional_kwargs=acc.additional_kwargs, id=acc.id,
        response_metadata=acc.response_metadata,
    )


async def _record(org_id: str, agent_id: str, turn_id: str | None, purpose: str,
                  model: ResolvedModel, request: dict, response: dict, tin: int, tout: int,
                  latency: int, error: str | None) -> str:
    try:
        async with SessionLocal() as session:
            row = LlmCall(org_id=org_id, agent_id=agent_id, turn_id=turn_id, purpose=purpose,
                          provider=model.provider_type, model=model.model_id, request=request,
                          response=response, input_tokens=tin, output_tokens=tout,
                          cost_usd=model.cost(tin, tout), latency_ms=latency, error=error)
            session.add(row)
            await session.commit()
            return row.id
    except Exception:  # noqa: BLE001 - recording must never fail the call
        log.exception("failed to record llm call")
        return ""


def as_system(text: str) -> SystemMessage:
    return SystemMessage(content=text)


def as_user(text: str) -> HumanMessage:
    return HumanMessage(content=text)
