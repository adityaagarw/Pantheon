"""Agents' thinking is captured and shown with the messages it led to."""

from __future__ import annotations

from langchain_core.messages import AIMessage, AIMessageChunk

from app.agents.llm import reasoning_of, split_think
from app.llm.reasoning import ReasoningChatOpenAI
from app.models import Message
from tests.conftest import wait_for
from tests.helpers import dm, make_agent, make_org, rows, script


def _model() -> ReasoningChatOpenAI:
    return ReasoningChatOpenAI(model="m", api_key="x", base_url="http://localhost:1/v1")


def test_preserve_thinking_sends_earlier_reasoning_back_only_when_set():
    from langchain_core.messages import HumanMessage

    history = [HumanMessage("Is 91 prime?"),
               AIMessage("No", additional_kwargs={"reasoning_content": "91 = 7 x 13"}),
               HumanMessage("And 97?")]
    off = _model()._get_request_payload(history)
    assert "extra_body" not in off and "reasoning_content" not in off["messages"][1]
    on = ReasoningChatOpenAI(model="m", api_key="x", base_url="http://localhost:1/v1",
                             preserve_thinking=True)._get_request_payload(history)
    assert on["extra_body"] == {"preserve_thinking": True}
    assert on["messages"][1] == {"role": "assistant", "content": "No",
                                 "reasoning_content": "91 = 7 x 13"}


async def test_preserve_thinking_reaches_the_server_in_the_request_body():
    import json

    import httpx
    from langchain_core.messages import HumanMessage

    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "c", "object": "chat.completion", "created": 0, "model": "m",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": "Yes"}}]})

    llm = ReasoningChatOpenAI(model="m", api_key="x", base_url="http://server/v1",
                              preserve_thinking=True,
                              http_async_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await llm.ainvoke([HumanMessage("Is 91 prime?"),
                       AIMessage("No", additional_kwargs={"reasoning_content": "7 x 13"}),
                       HumanMessage("And 97?")])
    assert sent[0]["preserve_thinking"] is True
    assert sent[0]["messages"][1]["reasoning_content"] == "7 x 13"


async def test_preserve_thinking_is_set_per_model(app_client):
    from app.llm.providers import resolve_binding

    r = await app_client.post("/api/v1/providers", json={
        "name": "Local", "type": "openai_compatible", "baseUrl": "http://localhost:1/v1",
        "models": [{"id": "qwen", "preserve_thinking": True}, {"id": "other"}]})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    assert (await resolve_binding({"provider_id": pid, "model": "qwen"})).chat.preserve_thinking
    assert not (await resolve_binding({"provider_id": pid, "model": "other"})).chat.preserve_thinking


def test_reasoning_field_is_kept_from_responses_and_streams():
    llm = _model()
    result = llm._create_chat_result({
        "id": "c", "object": "chat.completion", "created": 0, "model": "m",
        "choices": [{"index": 0, "finish_reason": "stop", "message": {
            "role": "assistant", "content": "No", "reasoning": "91 = 7 x 13"}}]})
    assert reasoning_of(result.generations[0].message) == "91 = 7 x 13"

    chunks = [
        {"choices": [{"index": 0, "delta": {"role": "assistant", "reasoning_content": "91 = "}}]},
        {"choices": [{"index": 0, "delta": {"reasoning_content": "7 x 13"}}]},
        {"choices": [{"index": 0, "delta": {"content": "No"}}]},
    ]
    acc = None
    for c in chunks:
        gen = llm._convert_chunk_to_generation_chunk(c, AIMessageChunk, None)
        acc = gen.message if acc is None else acc + gen.message
    assert acc.content == "No" and reasoning_of(acc) == "91 = 7 x 13"


def test_inline_think_tags_become_reasoning():
    assert split_think("<think>check the math</think>\n\nNo") == ("No", "check the math")
    assert split_think("plain") == ("plain", "")


async def test_messages_are_flagged_and_thinking_is_served(app_client):
    org = await make_org(app_client)
    ada = await make_agent(app_client, org["id"], "Ada")
    script(ada["id"], AIMessage(content="Sure, on it.",
                                additional_kwargs={"reasoning_content": "The user wants help."}))
    await dm(app_client, org["id"], "Ada", "Can you help?")

    async def reply():
        return await rows(Message, Message.sender_id == ada["id"])

    msgs = await wait_for(reply, msg="reply")
    assert msgs[0].meta.get("thinking") is True
    r = (await app_client.get(f"/api/v1/messages/{msgs[0].id}/thinking")).json()
    assert r["steps"][0]["reasoning"] == "The user wants help."


async def test_a_model_without_a_window_gets_200k_unless_its_server_serves_less(app_client,
                                                                               monkeypatch):
    from app.llm import providers

    r = await app_client.post("/api/v1/providers", json={
        "name": "Local", "type": "openai_compatible", "baseUrl": "http://localhost:1/v1",
        "models": [{"id": "big"}, {"id": "small"}, {"id": "set", "context_window": 64000}]})
    pid = r.json()["id"]
    served = {"big": 262144, "small": 32768, "set": 262144}

    async def advertised(provider, model_id):  # Radiance's /models entry
        return {"id": model_id, "max_model_len": served[model_id],
                "capabilities": {"image": True, "video": model_id == "big"}}

    monkeypatch.setattr(providers, "advertised", advertised)
    got = {m: await providers.resolve_binding({"provider_id": pid, "model": m}) for m in served}
    assert {m: r.context_window for m, r in got.items()} == {
        "big": 200_000, "small": 32768, "set": 64000}
    # What the model takes comes from the server too, unless set on the model or the agent.
    assert [(r.vision, r.video) for r in got.values()] == [(True, True), (True, False), (True, False)]
    off = await providers.resolve_binding({"provider_id": pid, "model": "big", "video": False})
    assert (off.vision, off.video) == (True, False)


def test_a_compaction_summary_is_asked_for_without_thinking():
    """With thinking on, a timed run's summaries of a few thousand tokens took 45 seconds
    each, most of it reasoning about what to keep."""
    from langchain_core.messages import HumanMessage

    from app.llm.effort import without_thinking

    chat = ReasoningChatOpenAI(model="m", api_key="x", base_url="http://localhost:1/v1",
                               reasoning_effort="xhigh", preserve_thinking=True)
    quick = without_thinking(chat, "openai_compatible")._get_request_payload([HumanMessage("sum up")])
    assert quick["reasoning_effort"] == "none"
    assert quick["extra_body"]["chat_template_kwargs"] == {"enable_thinking": False}
    assert chat.reasoning_effort == "xhigh"  # the agent's own model is untouched
