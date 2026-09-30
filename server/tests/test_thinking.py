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
