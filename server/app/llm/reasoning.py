"""Keep the model's reasoning ("thinking") from OpenAI-compatible servers.

vLLM, SGLang, OpenRouter, DeepSeek and friends return a model's thinking in a
non-standard ``reasoning_content`` / ``reasoning`` field (on the message, or on
each streamed delta). langchain-openai ignores unknown fields, so without this
the thinking is silently lost. We copy it into
``additional_kwargs["reasoning_content"]``, where ``llm.reasoning_of`` finds it
and streamed chunks concatenate it. It is never sent back to the provider.
"""

from __future__ import annotations

from typing import Any

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
