"""Reasoning effort: how much an agent's model thinks, including "not at all".

Pantheon's levels are provider-neutral: ``off``, ``low``, ``medium``, ``high``,
``xhigh``. Each provider spells them differently (and supports different
subsets), so this module translates:

* OpenAI: ``reasoning_effort`` (``off`` -> ``none``).
* OpenRouter: ``reasoning.effort`` in the request body.
* OpenAI-compatible servers (vLLM, llama.cpp, LM Studio, Ollama): ``reasoning_effort``,
  and for ``off`` also ``chat_template_kwargs.enable_thinking = false`` (the switch
  Qwen-style chat templates use).
* Anthropic: an extended-thinking token budget (``off`` = no thinking block).

Some servers accept only a subset ("Supported types are xhigh, medium and low").
When one rejects a level, the error's list is learned per model and the closest
supported level is used from then on.
"""

from __future__ import annotations

import re
from typing import Any

LEVELS = ("off", "low", "medium", "high", "xhigh")
RANK = {level: i for i, level in enumerate(LEVELS)}
ANTHROPIC_BUDGET = {"low": 2048, "medium": 8192, "high": 16384, "xhigh": 32768}
OPENAI_LIKE = ("openai", "openrouter", "openai_compatible", "ollama")

# (provider id, model id) -> levels the server said it accepts
_learned: dict[tuple[str, str], list[str]] = {}


def valid(level: Any) -> bool:
    return level in LEVELS


def nearest(level: str, supported: list[str]) -> str:
    """The supported level closest to ``level`` (ties go to more thinking)."""
    if level in supported or not supported:
        return level
    r = RANK.get(level, 2)
    return sorted(supported, key=lambda s: (abs(RANK.get(s, 2) - r), -RANK.get(s, 2)))[0]


def parse_supported(message: str) -> list[str] | None:
    """Levels named in a rejection like "Supported types are xhigh (default), medium, and low"."""
    m = re.search(r"reasoning[ _]effort", message, re.IGNORECASE)
    if not m:
        return None
    tail = message[m.end():]
    listing = re.search(r"(supported|valid|allowed|one of|must be)", tail, re.IGNORECASE)
    if not listing:
        return None
    sentence = tail[listing.end():].split(".")[0]
    levels: list[str] = []
    for word in re.split("[^a-z]+", sentence.lower()):
        level = {"none": "off", "minimal": "low"}.get(word, word)
        if level in RANK and level not in levels:
            levels.append(level)
    return levels or None


def learn(provider_id: str, model_id: str, supported: list[str]) -> None:
    _learned[(provider_id, model_id)] = supported


def learned(provider_id: str, model_id: str) -> list[str]:
    return _learned.get((provider_id, model_id), [])


def effective(level: str, provider_id: str, model_id: str) -> str:
    """The level to actually send: as chosen, or the closest one the server is known to accept."""
    supported = [s for s in learned(provider_id, model_id) if s != "off"]
    if not level or level == "off" or not supported:
        return level
    return nearest(level, supported)


def openai_kwargs(ptype: str, level: str) -> dict[str, Any]:
    """Extra ChatOpenAI arguments for a level ('' = provider default)."""
    if not level:
        return {}
    if ptype == "openrouter":
        return {"extra_body": {"reasoning": {"effort": "none" if level == "off" else level}}}
    out: dict[str, Any] = {"reasoning_effort": "none" if level == "off" else level}
    if level == "off" and ptype in ("openai_compatible", "ollama"):
        out["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}
    return out


def anthropic_kwargs(level: str, max_tokens: int) -> dict[str, Any]:
    """Extended thinking for Claude: a token budget (max_tokens must exceed it)."""
    if not level or level == "off":
        return {}
    budget = ANTHROPIC_BUDGET[level]
    return {"thinking": {"type": "enabled", "budget_tokens": budget},
            "max_tokens": max(max_tokens, budget + 4096)}


def repair(chat: Any, message: str, provider_id: str, model_id: str, level: str) -> Any | None:
    """A copy of ``chat`` that avoids the level the server just rejected, or None.

    If the error lists what it supports, switch to the closest of those (and
    remember it); otherwise drop the effort parameter altogether.
    """
    if not re.search(r"reasoning[ _]effort", message, re.IGNORECASE):
        return None
    supported = parse_supported(message)
    if level == "off":
        # The switch that matters for "off" is the chat-template flag; just drop the parameter.
        update: dict[str, Any] = {"reasoning_effort": None}
    elif supported:
        learn(provider_id, model_id, supported)
        update = {"reasoning_effort": nearest(level, [x for x in supported if x != "off"])}
    else:
        update = {"reasoning_effort": None}
    if getattr(chat, "reasoning_effort", None) == update["reasoning_effort"]:
        return None  # nothing to change: don't loop
    try:
        return chat.model_copy(update=update)
    except Exception:  # noqa: BLE001
        return None
