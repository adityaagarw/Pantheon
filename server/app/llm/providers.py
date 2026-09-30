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

from app.core.db import SessionLocal
from app.core.security import decrypt_secret
from app.llm import effort as effort_mod
from app.llm.mock import MockChatModel
from app.models import Agent, Org, Provider

PROVIDER_TYPES = ("openai", "openrouter", "openai_compatible", "anthropic", "ollama", "mock")
OPENROUTER_URL = "https://openrouter.ai/api/v1"
DEFAULT_CONTEXT = 32_000
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
    effort: str = ""  # off | low | medium | high | xhigh ("" = the provider's default)

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
    temperature = binding.get("temperature")
    max_tokens = binding.get("max_tokens")
    level = str(binding.get("reasoning_effort") or "")
    if not effort_mod.valid(level):
        level = ""
    level = effort_mod.effective(level, provider.id, model_id)
    chat = build_chat_model(
        provider, model_id, temperature=temperature, max_tokens=max_tokens,
        agent_id=agent_id, effort=level,
    )
    return ResolvedModel(
        chat=chat,
        provider_id=provider.id,
        provider_type=provider.type,
        model_id=model_id,
        context_window=int(binding.get("context_window") or meta.get("context_window")
                           or DEFAULT_CONTEXT),
        input_per_mtok=float(meta.get("input_per_mtok") or 0.0),
        output_per_mtok=float(meta.get("output_per_mtok") or 0.0),
        temperature=temperature,
        max_tokens=max_tokens,
        vision=bool(binding.get("vision") if binding.get("vision") is not None
                    else meta.get("vision")),
        effort=level,
    )


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
