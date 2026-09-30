"""Reasoning effort (including off and xhigh) and per-agent voice choices."""

from __future__ import annotations

import httpx
import pytest
from langchain_core.messages import AIMessageChunk, HumanMessage

from app.agents import llm
from app.llm import effort
from app.llm.providers import ResolvedModel, build_chat_model
from app.models import Provider
from tests.helpers import make_agent, make_org

VLLM_REJECTION = ("Unexpected reasoning effort high. "
                  "Supported types are xhigh (default), medium, and low.")


def test_levels_translate_per_provider():
    assert effort.LEVELS == ("off", "low", "medium", "high", "xhigh")
    assert effort.openai_kwargs("openai", "xhigh") == {"reasoning_effort": "xhigh"}
    assert effort.openai_kwargs("openai", "off") == {"reasoning_effort": "none"}
    assert effort.openai_kwargs("openai", "") == {}
    assert effort.openai_kwargs("openrouter", "xhigh") == {
        "extra_body": {"reasoning": {"effort": "xhigh"}}}
    local_off = effort.openai_kwargs("openai_compatible", "off")
    assert local_off == {"reasoning_effort": "none",
                         "extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}
    assert effort.anthropic_kwargs("off", 8192) == {}
    high = effort.anthropic_kwargs("high", 8192)
    assert high["thinking"] == {"type": "enabled", "budget_tokens": 16384}
    assert high["max_tokens"] > 16384  # Claude requires room beyond the thinking budget


def test_a_rejection_teaches_the_supported_levels():
    assert effort.parse_supported(VLLM_REJECTION) == ["xhigh", "medium", "low"]
    assert effort.parse_supported("reasoning_effort must be one of: none, low, high") == [
        "off", "low", "high"]
    assert effort.parse_supported("something else entirely") is None
    assert effort.nearest("high", ["xhigh", "medium", "low"]) == "xhigh"  # ties: more thinking
    assert effort.nearest("low", ["xhigh", "medium", "low"]) == "low"
    effort.learn("prov", "qwen", ["xhigh", "medium", "low"])
    assert effort.effective("high", "prov", "qwen") == "xhigh"
    assert effort.effective("off", "prov", "qwen") == "off"  # off is never rewritten
    assert effort.effective("high", "prov", "other-model") == "high"


def test_models_are_built_with_the_right_thinking_switches(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    local = Provider(name="local", type="openai_compatible", base_url="http://x/v1",
                     models=[], options={})
    off = build_chat_model(local, "qwen", effort="off")
    assert off.reasoning_effort == "none"
    assert off.extra_body == {"chat_template_kwargs": {"enable_thinking": False}}
    assert build_chat_model(local, "qwen", effort="xhigh").reasoning_effort == "xhigh"
    assert build_chat_model(local, "qwen").reasoning_effort is None

    # A provider's own extra_body survives alongside ours.
    custom = Provider(name="c", type="openai_compatible", base_url="http://x/v1", models=[],
                      options={"extra_body": {"top_k": 20}})
    body = build_chat_model(custom, "qwen", effort="off").extra_body
    assert body["top_k"] == 20 and body["chat_template_kwargs"] == {"enable_thinking": False}

    claude = Provider(name="a", type="anthropic", models=[], options={})
    thinking = build_chat_model(claude, "claude-sonnet-5", temperature=0.3, max_tokens=4000,
                                effort="medium")
    assert thinking.thinking == {"type": "enabled", "budget_tokens": 8192}
    assert thinking.max_tokens > 8192 and thinking.temperature is None
    plain = build_chat_model(claude, "claude-sonnet-5", temperature=0.3, effort="off")
    assert plain.thinking is None and plain.temperature == 0.3


class _Rejects(Exception):
    status_code = 400


class _StubChat:
    """A chat model that, like your vLLM, refuses reasoning_effort='high'."""

    def __init__(self, level: str | None) -> None:
        self.reasoning_effort = level
        self.calls: list[str | None] = []

    def model_copy(self, update):
        clone = _StubChat(update["reasoning_effort"])
        clone.calls = self.calls
        return clone

    def bind_tools(self, tools):
        return self

    async def astream(self, messages):
        self.calls.append(self.reasoning_effort)
        if self.reasoning_effort == "high":
            raise _Rejects(VLLM_REJECTION)
        yield AIMessageChunk(content="fine")


async def test_a_rejected_level_is_replaced_and_the_call_succeeds(app_client):
    org = await make_org(app_client)
    agent = await make_agent(app_client, org["id"], "Ada")
    stub = _StubChat("high")
    model = ResolvedModel(chat=stub, provider_id="prov2", provider_type="openai_compatible",
                          model_id="qwen-x", context_window=8000, input_per_mtok=0,
                          output_per_mtok=0, temperature=None, max_tokens=None, effort="high")
    msg, _ = await llm.invoke(model, [HumanMessage(content="hi")], org_id=org["id"],
                              agent_id=agent["id"], turn_id=None, purpose="turn")
    assert msg.content == "fine"
    assert stub.calls == ["high", "xhigh"]  # rejected once, then the closest supported level
    assert effort.learned("prov2", "qwen-x") == ["xhigh", "medium", "low"]

    off = ResolvedModel(chat=_StubChat("high"), provider_id="p3", provider_type="openai",
                        model_id="m", context_window=8000, input_per_mtok=0, output_per_mtok=0,
                        temperature=None, max_tokens=None, effort="high")
    off.chat.reasoning_effort = "high"

    class Unrelated(_StubChat):
        async def astream(self, messages):
            raise _Rejects("the prompt is too long")
            yield  # pragma: no cover

    broken = ResolvedModel(chat=Unrelated("high"), provider_id="p4", provider_type="openai",
                           model_id="m", context_window=8000, input_per_mtok=0,
                           output_per_mtok=0, temperature=None, max_tokens=None, effort="high")
    with pytest.raises(llm.LLMFailure):  # only effort errors are auto-corrected
        await llm.invoke(broken, [HumanMessage(content="hi")], org_id=org["id"],
                         agent_id=agent["id"], turn_id=None, purpose="turn")


async def test_agents_accept_the_new_levels_and_reject_nonsense(app_client):
    org = await make_org(app_client)
    ada = await make_agent(app_client, org["id"], "Ada")
    for level in ("off", "xhigh"):
        r = await app_client.patch(f"/api/v1/agents/{ada['id']}",
                                   json={"model": {"reasoning_effort": level}})
        assert r.status_code == 200 and r.json()["model"]["reasoning_effort"] == level
    bad = await app_client.patch(f"/api/v1/agents/{ada['id']}",
                                 json={"model": {"reasoning_effort": "ludicrous"}})
    assert bad.status_code == 400 and "xhigh" in bad.json()["detail"]


async def test_the_voice_picker_lists_the_speech_models_voices(app_client, monkeypatch):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/v1/audio/voices":
            return httpx.Response(200, json={"voices": ["alba", "george"]})
        return httpx.Response(404)

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda *a, **k: real(transport=httpx.MockTransport(handler)))
    await app_client.put("/api/v1/voice/config", json={"ttsModel": "pocket-tts",
                                                       "defaultVoice": "alba"})
    got = (await app_client.get("/api/v1/voice/voices")).json()
    assert got == {"voices": ["alba", "george"], "default": "alba", "model": "pocket-tts"}
    assert seen[0].url.params["model"] == "pocket-tts"

    org = await make_org(app_client)
    ada = await make_agent(app_client, org["id"], "Ada", avatar={"voice": "george"})
    assert ada["avatar"]["voice"] == "george"

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no audio server")

    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda *a, **k: real(transport=httpx.MockTransport(down)))
    offline = (await app_client.get("/api/v1/voice/voices")).json()
    assert offline["voices"] == [] and "ConnectError" in offline["error"]


async def test_voice_works_without_a_speech_server(app_client, monkeypatch):
    from app.api import voice

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no audio server")

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda *a, **k: real(transport=httpx.MockTransport(down)))
    voice._reach.clear()
    assert (await app_client.get("/api/v1/voice/status")).json() == {
        "server": False, "browserTts": True, "browserStt": False, "language": ""}

    org = await make_org(app_client)
    ada = await make_agent(app_client, org["id"], "Ada", avatar={"voice": "george"})
    r = await app_client.post("/api/v1/voice/speak", json={"text": "hi", "agentId": ada["id"]})
    assert r.status_code == 503  # the page reads it with the browser's voices instead
    assert r.json()["browser"] is True and r.json()["voice"] == "george"

    # A browser voice never goes to a server.
    r = await app_client.post("/api/v1/voice/speak", json={"text": "hi", "voice": "browser:Samantha"})
    assert r.status_code == 409 and r.json()["voice"] == "browser:Samantha"

    r = await app_client.post("/api/v1/voice/transcribe",
                              files={"file": ("a.wav", b"RIFF....", "audio/wav")})
    assert r.status_code == 502


async def test_hosted_speech_apis_get_the_api_key(app_client, monkeypatch):
    from app.api import voice

    seen: list[httpx.Request] = []

    def openai(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.headers.get("authorization") != "Bearer sk-test-voice":
            return httpx.Response(401)
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "gpt-4o-mini-tts"}]})
        if request.url.path == "/v1/audio/speech":
            return httpx.Response(200, content=b"RIFFwav", headers={"content-type": "audio/wav"})
        return httpx.Response(404)

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real(
        transport=httpx.MockTransport(openai), headers=k.get("headers")))
    cfg = (await app_client.put("/api/v1/voice/config", json={
        "baseUrl": "https://api.openai.com", "ttsModel": "gpt-4o-mini-tts",
        "defaultVoice": "alloy", "apiKey": "sk-test-voice"})).json()
    assert cfg["hasApiKey"] is True
    assert "apiKey" not in cfg and "apiKeyEnc" not in cfg  # never sent back
    assert "apiKeyEnc" not in (await app_client.get("/api/v1/voice/config")).json()

    assert (await app_client.get("/api/v1/voice/status")).json()["server"] is True
    r = await app_client.post("/api/v1/voice/speak", json={"text": "hello"})
    assert r.status_code == 200 and r.content == b"RIFFwav"
    spoken = [q for q in seen if q.url.path == "/v1/audio/speech"][0]
    assert b'"voice":"alloy"' in spoken.content.replace(b" ", b"")
    # OpenAI can't list its voices, so Pantheon offers the known ones.
    assert "alloy" in (await app_client.get("/api/v1/voice/voices")).json()["voices"]

    cleared = (await app_client.put("/api/v1/voice/config", json={"apiKey": ""})).json()
    assert cleared["hasApiKey"] is False
    voice._reach.clear()
    assert (await app_client.get("/api/v1/voice/status")).json()["server"] is False
