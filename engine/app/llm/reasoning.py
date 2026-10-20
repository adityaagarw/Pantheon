"""Keep the model's reasoning ("thinking") from OpenAI-compatible servers.

vLLM, SGLang, OpenRouter, DeepSeek and friends return a model's thinking in a
non-standard ``reasoning_content`` / ``reasoning`` field (on the message, or on
each streamed delta). langchain-openai ignores unknown fields, so without this
the thinking is silently lost. We copy it into
``additional_kwargs["reasoning_content"]``, where ``llm.reasoning_of`` finds it
and streamed chunks concatenate it.

With the provider option ``preserve_thinking`` (Qwen3.6 and later recommend it for agents),
each earlier assistant message goes back with its reasoning, and the chat template is told
to keep it (llama.cpp's and Radiance's top-level ``preserve_thinking``). Otherwise it is
never sent back.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage
from langchain_openai import ChatOpenAI

KEYS = ("reasoning_content", "reasoning")


def _reasoning(d: Any) -> str:
    if not isinstance(d, dict):
        return ""
    for k in KEYS:
        v = d.get(k)
        if isinstance(v, str) and v:
            return v
    return ""


class ReasoningChatOpenAI(ChatOpenAI):
    preserve_thinking: bool = False

    def _get_request_payload(self, input_, *, stop=None, **kwargs):  # type: ignore[override]
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        if not self.preserve_thinking or "messages" not in payload:
            return payload
        sent = self._convert_input(input_).to_messages()
        for msg, out in zip(sent, payload["messages"], strict=False):  # one dict per message
            if isinstance(msg, AIMessage) and out.get("role") == "assistant" and \
                    (text := _reasoning(msg.additional_kwargs)):
                out["reasoning_content"] = text
        # A top-level field of the request body; the OpenAI client takes those only as extra_body.
        payload["extra_body"] = {**(payload.get("extra_body") or {}), "preserve_thinking": True}
        return payload

    def _convert_chunk_to_generation_chunk(self, chunk, default_chunk_class, base_generation_info):  # type: ignore[override]  # noqa: E501
        gen = super()._convert_chunk_to_generation_chunk(chunk, default_chunk_class,
                                                         base_generation_info)
        if gen is None:
            return gen
        choices = chunk.get("choices") or chunk.get("chunk", {}).get("choices") or []
        if choices and (text := _reasoning(choices[0].get("delta"))):
            gen.message.additional_kwargs["reasoning_content"] = text
        return gen

    def _create_chat_result(self, response, generation_info=None):  # type: ignore[override]
        result = super()._create_chat_result(response, generation_info)
        data = response if isinstance(response, dict) else response.model_dump()
        for gen, choice in zip(result.generations, data.get("choices") or [], strict=False):
            if text := _reasoning(choice.get("message")):
                gen.message.additional_kwargs["reasoning_content"] = text
        return result
