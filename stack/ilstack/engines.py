"""Inference engines: token ids and parameters in, token ids out.

`MockEngine` for tests and dry runs; `VllmEngine` wraps vLLM's asynchronous
engine, fed with token ids directly, since tokenization happens on the
frontend.
"""
from __future__ import annotations

import asyncio
import uuid

from .messages import Response


class Engine:
    async def generate(self, tokens: list[int], params: dict) -> Response:
        raise NotImplementedError


class MockEngine(Engine):
    """Answers with the prompt's tokens reversed, capped at max_tokens;
    `delay` seconds per request so concurrency is observable."""

    def __init__(self, delay: float = 0.0):
        self.delay = delay
        self.calls = 0

    async def generate(self, tokens: list[int], params: dict) -> Response:
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        n = int(params.get("max_tokens", 16))
        out = list(reversed(tokens))[:n]
        return Response(out, "length" if len(out) == n and len(tokens) > n else "stop")


class VllmEngine(Engine):
    def __init__(self, model: str, tensor_parallel_size: int = 1, **engine_kwargs):
        from vllm import AsyncEngineArgs
        from vllm.engine.async_llm_engine import AsyncLLMEngine
        self.engine = AsyncLLMEngine.from_engine_args(
            AsyncEngineArgs(model=model, tensor_parallel_size=tensor_parallel_size, **engine_kwargs))

    async def generate(self, tokens: list[int], params: dict) -> Response:
        from vllm import SamplingParams
        sp = SamplingParams(
            max_tokens=int(params.get("max_tokens", 256)),
            temperature=float(params.get("temperature", 1.0)),
            top_p=float(params.get("top_p", 1.0)),
            seed=params.get("seed"),
            stop=params.get("stop"),
        )
        final = None
        async for out in self.engine.generate({"prompt_token_ids": tokens}, sp, request_id=uuid.uuid4().hex):
            final = out
        if final is None or not final.outputs:
            return Response([], "error", "engine produced no output")
        o = final.outputs[0]
        return Response(list(o.token_ids), o.finish_reason or "stop")
