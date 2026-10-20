"""Model defaults: the provider's temperature, long outputs and extra-high thinking."""

from __future__ import annotations

from langchain_core.messages import AIMessageChunk, HumanMessage

from app.agents import llm
from app.llm import limits
from app.llm.providers import ResolvedModel, build_chat_model, resolve_for_agent
from app.models import Agent, Org, Provider
from app.tools import meta
from tests.helpers import make_agent, make_org, rows

VLLM = ("This model's maximum context length is 32768 tokens. However, you requested 140000 "
        "tokens (8928 in the messages, 131072 in the completion).")


def test_rejected_output_limits_are_understood():
    assert limits.fitting("max_tokens is too large: 131072. This model supports at most 16384 "
                          "completion tokens, whereas you provided 131072.") == 16384
    assert limits.fitting("max_tokens: 131072 > 64000, which is the maximum allowed number of "
                          "output tokens for claude-opus") == 64000
    assert limits.fitting(VLLM) == 32768 - 8928 - limits.CONTEXT_MARGIN
    assert limits.fitting("'max_tokens' or 'max_completion_tokens' is too large: 131072. This "
                          "model's maximum context length is 262144 tokens and your request has "
                          "20000 input tokens (131072 > 262144 - 20000).") == 242144 - 64
    assert limits.fitting("This model's maximum context length is 32768 tokens.") == 0
    assert limits.fitting("Unexpected reasoning effort high") is None


def test_models_ask_for_what_fits(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    local = Provider(name="local", type="openai_compatible", base_url="http://x/v1", models=[],
                     options={})
    chat = build_chat_model(local, "qwen", max_tokens=131072, effort="xhigh")
    assert chat.max_tokens == 131072 and chat.temperature is None
    assert limits.repair(chat, VLLM, "openai_compatible").max_tokens == 23776
    unknown = limits.repair(chat, "This model's maximum context length is 32768 tokens.",
                            "openai_compatible")
    assert unknown.max_tokens is None  # let the server use what's left of the context
    assert limits.repair(chat, "the prompt is too long", "openai_compatible") is None

    claude = Provider(name="a", type="anthropic", models=[], options={})
    big = build_chat_model(claude, "claude-x", max_tokens=131072, effort="xhigh")
    assert big.streaming is True  # long outputs must stream
    fixed = limits.repair(big, "max_tokens: 131072 > 32000, which is the maximum allowed "
                               "number of output tokens", "anthropic")
    assert fixed.max_tokens == 32000 and fixed.thinking["budget_tokens"] == 16000


class _Rejects(Exception):
    status_code = 400


class _Stub:
    def __init__(self, max_tokens):
        self.max_tokens, self.reasoning_effort, self.calls = max_tokens, None, []

    def model_copy(self, update):
        clone = _Stub(update.get("max_tokens", self.max_tokens))
        clone.calls = self.calls
        return clone

    def bind_tools(self, tools):
        return self

    async def astream(self, messages):
        self.calls.append(self.max_tokens)
        if self.max_tokens and self.max_tokens > 23776:
            raise _Rejects(VLLM)
        yield AIMessageChunk(content="fine")


async def test_defaults_apply_and_a_too_long_limit_is_retried(app_client):
    org = await make_org(app_client)
    ada = await make_agent(app_client, org["id"], "Ada")
    agent = (await rows(Agent, Agent.id == ada["id"]))[0]
    resolved = await resolve_for_agent(agent, (await rows(Org, Org.id == org["id"]))[0])
    assert resolved.max_tokens == 131072 and resolved.effort == "xhigh"
    assert resolved.temperature is None  # the provider's own

    await app_client.patch(f"/api/v1/agents/{ada['id']}", json={"model": {
        "temperature": 0.4, "max_tokens": 2000, "reasoning_effort": "low"}})
    agent = (await rows(Agent, Agent.id == ada["id"]))[0]
    mine = await resolve_for_agent(agent)
    assert (mine.temperature, mine.max_tokens, mine.effort) == (0.4, 2000, "low")

    stub = _Stub(131072)
    model = ResolvedModel(chat=stub, provider_id="p", provider_type="openai_compatible",
                          model_id="q", context_window=32768, input_per_mtok=0,
                          output_per_mtok=0, temperature=None, max_tokens=131072, effort="")
    msg, _ = await llm.invoke(model, [HumanMessage(content="hi")], org_id=org["id"],
                              agent_id=ada["id"], turn_id=None, purpose="turn")
    assert msg.content == "fine" and stub.calls == [131072, 23776]


async def test_zeus_leaves_sampling_to_the_user(app_client):
    org = await make_org(app_client)
    await meta.create_agent({"org": org["id"], "name": "Bo", "role": "r", "persona": "p",
                             "model": {"temperature": 0.2, "max_tokens": 4096,
                                       "reasoning_effort": "high"}}, None)
    bo = next(a for a in await rows(Agent, Agent.org_id == org["id"]) if a.name == "Bo")
    assert bo.model == {"reasoning_effort": "high"}
    assert "temperature" not in meta.MODEL_SCHEMA["properties"]

    await app_client.patch(f"/api/v1/agents/{bo.id}", json={"model": {"temperature": 0.9}})
    await meta.update_agent({"org": org["id"], "agent": "Bo",
                             "model": {"reasoning_effort": "low", "temperature": 0}}, None)
    bo = (await rows(Agent, Agent.id == bo.id))[0]
    assert bo.model["temperature"] == 0.9 and bo.model["reasoning_effort"] == "low"
