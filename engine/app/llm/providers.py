"""Provider registry: resolves an agent's model binding to a chat model.

Resolution order for an agent: its own ``model`` binding → the org's
``settings.default_model`` → the global default provider. Every resolved
model carries pricing and context-window metadata so the runtime can meter
cost and decide when to compact context.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
from langchain_core.language_models import BaseChatModel
from sqlalchemy import select

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.security import decrypt_secret
from app.llm import effort as effort_mod
from app.llm.mock import MockChatModel
from app.models import Agent, Org, Provider

PROVIDER_TYPES = ("openai", "openrouter", "openai_compatible", "anthropic", "ollama", "mock")
OPENROUTER_URL = "https://openrouter.ai/api/v1"
# The context window of a model nobody set one for (Settings → Providers → a model). It was
# 32K, so agents on a 262K local model compacted their history at ~19K tokens and trimmed
# tool output at ~5K, all the time. A server that says it serves less (vLLM's and
# Radiance's max_model_len) caps it.
DEFAULT_CONTEXT = 200_000
# (provider, model) -> (when, the model's entry in the server's /models)
_ADVERTISED: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
LLM_TIMEOUT_SECONDS = 300


class ModelConfigError(RuntimeError):
    """The agent's model binding cannot be resolved (not transient)."""


@dataclass
class ResolvedModel:
    chat: BaseChatModel
    provider_id: str
    provider_type: str
    model_id: str
    context_window: int
    input_per_mtok: float
    output_per_mtok: float
    temperature: float | None
    max_tokens: int | None
    vision: bool = False  # accepts images (screenshots from computer use / the browser)
    video: bool = False  # accepts video (video_url parts), e.g. Qwen3.8 on Radiance
    effort: str = ""  # off | low | medium | high | xhigh ("" = the provider's default)
    # Tokens of context at which an agent compacts (0: a share of the window). A local
    # server's KV cache is shared by every agent at once, so it can need far less than
    # the window: three agents at 100K each don't fit a 200K pool.
    compact_at: int = 0

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        return (input_tokens * self.input_per_mtok + output_tokens * self.output_per_mtok) / 1e6


def _model_meta(provider: Provider, model_id: str) -> dict[str, Any]:
    for m in provider.models or []:
        if isinstance(m, dict) and m.get("id") == model_id:
            return m
    return {}


def build_chat_model(
    provider: Provider,
    model_id: str,
    *,
    temperature: float | None = None,
    max_tokens: int | None = None,
    agent_id: str = "",
    extra: dict[str, Any] | None = None,
    effort: str = "",
) -> BaseChatModel:
    ptype = provider.type
    api_key = decrypt_secret(provider.api_key_enc) if provider.api_key_enc else None
    options = dict(provider.options or {})
    options.update(extra or {})
    if ptype not in ("openai", "openrouter", "openai_compatible", "ollama"):
        options.pop("preserve_thinking", None)  # only the OpenAI-style client knows it
    if ptype == "mock":
        return MockChatModel(agent_id=agent_id)
    if ptype in ("openai", "openrouter", "openai_compatible", "ollama"):
        from app.llm.reasoning import ReasoningChatOpenAI

        base_url = provider.base_url or {
            "openrouter": OPENROUTER_URL,
            "ollama": "http://localhost:11434/v1",
        }.get(ptype)
        kwargs: dict[str, Any] = {
            "model": model_id,
            "api_key": api_key or "not-required",
            "timeout": LLM_TIMEOUT_SECONDS,
            "max_retries": 0,  # retries are owned by the graph's RetryPolicy
            "stream_usage": True,
        }
        if base_url:
            kwargs["base_url"] = base_url
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        if ptype == "openrouter":
            kwargs.setdefault("default_headers", {
                "HTTP-Referer": "https://github.com/pantheon", "X-Title": "Pantheon",
            })
        thinking = effort_mod.openai_kwargs(ptype, effort)
        kwargs.update(thinking)
        kwargs.update(options)
        if "extra_body" in thinking:  # the provider's own extra_body must not erase ours
            kwargs["extra_body"] = {**options.get("extra_body", {}), **thinking["extra_body"]}
        return ReasoningChatOpenAI(**kwargs)
    if ptype == "anthropic":
        from langchain_anthropic import ChatAnthropic

        kwargs = {
            "model": model_id,
            "api_key": api_key,
            "timeout": LLM_TIMEOUT_SECONDS,
            "max_retries": 0,
            "max_tokens": max_tokens or 8192,
        }
        if kwargs["max_tokens"] > 21333:
            # The SDK refuses long non-streamed requests; streaming gives the same result.
            kwargs["streaming"] = True
        if temperature is not None:
            kwargs["temperature"] = temperature
        if provider.base_url:
            kwargs["base_url"] = provider.base_url
        thinking = effort_mod.anthropic_kwargs(effort, int(kwargs["max_tokens"]))
        if thinking:
            kwargs.pop("temperature", None)  # Claude requires the default with thinking on
        kwargs.update(thinking)
        kwargs.update(options)
        return ChatAnthropic(**kwargs)
    raise ModelConfigError(f"unsupported provider type '{ptype}'")


async def resolve_for_agent(agent: Agent, org: Org | None = None) -> ResolvedModel:
    binding: dict[str, Any] = dict((org.settings or {}).get("default_model") or {}) if org else {}
    binding.update({k: v for k, v in (agent.model or {}).items() if v not in (None, "")})
    return await resolve_binding(binding, agent_id=agent.id)


async def resolve_binding(binding: dict[str, Any], agent_id: str = "") -> ResolvedModel:
    async with SessionLocal() as session:
        provider: Provider | None = None
        if pid := binding.get("provider_id"):
            provider = await session.get(Provider, pid)
            if provider is None:
                raise ModelConfigError(f"provider '{pid}' does not exist")
        else:
            provider = (
                await session.execute(
                    select(Provider).order_by(Provider.is_default.desc(), Provider.created_at)
                )
            ).scalars().first()
            if provider is None:
                raise ModelConfigError("no LLM provider is configured")
    model_id = binding.get("model") or provider.default_model or next(
        (m.get("id") for m in provider.models or [] if isinstance(m, dict) and m.get("id")),
        None,
    )
    if not model_id:
        raise ModelConfigError(f"provider '{provider.name}' has no model selected")
    meta = _model_meta(provider, model_id)
    served = await advertised(provider, model_id)
    caps = served.get("capabilities") if isinstance(served.get("capabilities"), dict) else {}

    def can(key: str, cap: str) -> bool:
        # Set on the agent, else on the model (Settings → Providers), else what the server says.
        for v in (binding.get(key), meta.get(key)):
            if v is not None:
                return bool(v)
        return bool(caps.get(cap))

    temperature = binding.get("temperature")  # None: the provider's default
    max_tokens = binding.get("max_tokens") or settings.default_max_tokens or None
    level = str(binding.get("reasoning_effort") or settings.default_reasoning_effort or "")
    if not effort_mod.valid(level):
        level = ""
    level = effort_mod.effective(level, provider.id, model_id)
    # Earlier reasoning sent back (reasoning.py): the model's own setting, else the provider's.
    keep = meta.get("preserve_thinking")
    chat = build_chat_model(
        provider, model_id, temperature=temperature, max_tokens=max_tokens,
        agent_id=agent_id, effort=level,
        extra={"preserve_thinking": bool(keep)} if keep is not None else None,
    )
    return ResolvedModel(
        chat=chat,
        provider_id=provider.id,
        provider_type=provider.type,
        model_id=model_id,
        context_window=int(binding.get("context_window") or meta.get("context_window")
                           or min(DEFAULT_CONTEXT, await advertised_context(provider, model_id)
                                  or DEFAULT_CONTEXT)),
        input_per_mtok=float(meta.get("input_per_mtok") or 0.0),
        output_per_mtok=float(meta.get("output_per_mtok") or 0.0),
        temperature=temperature,
        max_tokens=max_tokens,
        vision=can("vision", "image"),
        video=can("video", "video"),
        effort=level,
        compact_at=int(binding.get("compact_at") or meta.get("compact_at") or 0),
    )


async def advertised(provider: Provider, model_id: str) -> dict[str, Any]:
    """The model's entry in a local server's /models (vLLM and SGLang give max_model_len;
    Radiance also gives capabilities: image, video, ...), asked at most every ten minutes."""
    import time

    if provider.type not in ("openai_compatible", "ollama") or not provider.base_url:
        return {}
    key = (provider.id, model_id)
    when, entry = _ADVERTISED.get(key, (0.0, {}))
    if time.monotonic() - when < 600:
        return entry
    entry = {}
    try:
        api_key = decrypt_secret(provider.api_key_enc) if provider.api_key_enc else None
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(provider.base_url.rstrip("/") + "/models",
                                    headers={"Authorization": f"Bearer {api_key}"} if api_key else {})
            for m in resp.json().get("data", []) if resp.is_success else []:
                if isinstance(m, dict) and m.get("id") == model_id:
                    entry = m
    except Exception:  # noqa: BLE001 - unknown: the defaults stand
        entry = {}
    _ADVERTISED[key] = (time.monotonic(), entry)
    return entry


async def advertised_context(provider: Provider, model_id: str) -> int | None:
    """The context length the server says it serves this model with."""
    length = (await advertised(provider, model_id)).get("max_model_len")
    return length if isinstance(length, int) else None


async def list_remote_models(provider: Provider) -> list[str]:
    """Ask the provider which models it serves (connection test)."""
    if provider.type == "mock":
        return ["mock"]
    api_key = decrypt_secret(provider.api_key_enc) if provider.api_key_enc else None
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    if provider.type == "anthropic":
        headers = {"x-api-key": api_key or "", "anthropic-version": "2023-06-01"}
        url = (provider.base_url or "https://api.anthropic.com").rstrip("/") + "/v1/models"
    else:
        base = provider.base_url or {
            "openai": "https://api.openai.com/v1",
            "openrouter": OPENROUTER_URL,
            "ollama": "http://localhost:11434/v1",
        }.get(provider.type, "")
        url = base.rstrip("/") + "/models"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    return sorted(str(m.get("id")) for m in data.get("data", []) if m.get("id"))
