"""The HTTP face of the frontend: an OpenAI-style API for clients and a
small verifier API for the challenge flow.

Standard parts: FastAPI routing, bearer-token API keys, a concurrency cap,
tokenization off the event loop. The verifier endpoints hand out what the
protocol says the prover supplies: its parameters, the certificates it
stored, the certificate echoing a nonce, and openings for challenges.
"""
from __future__ import annotations

import asyncio
import time
import uuid

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel

from . import packet as P
from .frontend import Frontend
from .tokenizers import Tokenizer
from .verifier import build_opening


class ChatRequest(BaseModel):
    model: str | None = None
    messages: list[dict]
    max_tokens: int = 256
    temperature: float = 1.0
    top_p: float = 1.0
    seed: int | None = None
    stop: list[str] | str | None = None


class CompletionRequest(BaseModel):
    model: str | None = None
    prompt: str
    max_tokens: int = 256
    temperature: float = 1.0
    top_p: float = 1.0
    seed: int | None = None
    stop: list[str] | str | None = None


class NonceRequest(BaseModel):
    nonce: str                      # 16 bytes, hex


class OpenRequest(BaseModel):
    dir: int
    bucket: int
    byte: int


def create_app(frontend: Frontend, tokenizer: Tokenizer, model_name: str,
               api_keys: set[str] | None = None, concurrency: int = 32) -> FastAPI:
    app = FastAPI(title="interlock frontend")
    limit = asyncio.Semaphore(concurrency)
    params = frontend.params

    async def auth(request: Request) -> None:
        if not api_keys:
            return
        token = request.headers.get("authorization", "")
        if not token.startswith("Bearer ") or token[7:] not in api_keys:
            raise HTTPException(401, "invalid API key")

    def gen_params(r) -> dict:
        p = {"max_tokens": r.max_tokens, "temperature": r.temperature, "top_p": r.top_p}
        if r.seed is not None:
            p["seed"] = r.seed
        if r.stop:
            p["stop"] = r.stop if isinstance(r.stop, list) else [r.stop]
        return p

    async def run(tokens: list[int], p: dict):
        async with limit:
            resp = await frontend.complete(tokens, p)
        if resp.finish_reason == "error":
            raise HTTPException(502, resp.error or "compute error")
        text = await asyncio.to_thread(tokenizer.decode, resp.tokens)
        return resp, text

    @app.get("/health")
    async def health():
        d = frontend.dispatcher
        return {"status": "ok", "frontend": vars(frontend.stats), "dispatcher": vars(d.stats),
                "clock": d.clock_stats(), "builder": d.builder_stats(), "assembler": vars(frontend.assembler.stats),
                "certificates": len(frontend.log.certificates)}

    @app.get("/v1/models", dependencies=[Depends(auth)])
    async def models():
        return {"object": "list", "data": [{"id": model_name, "object": "model", "owned_by": "interlock"}]}

    @app.post("/v1/chat/completions", dependencies=[Depends(auth)])
    async def chat(r: ChatRequest):
        prompt = await asyncio.to_thread(tokenizer.chat, r.messages)
        resp, text = await run(prompt, gen_params(r))
        return {"id": f"chatcmpl-{uuid.uuid4().hex[:12]}", "object": "chat.completion", "created": int(time.time()),
                "model": model_name,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                             "finish_reason": resp.finish_reason}],
                "usage": _usage(prompt, resp.tokens)}

    @app.post("/v1/completions", dependencies=[Depends(auth)])
    async def completions(r: CompletionRequest):
        prompt = await asyncio.to_thread(tokenizer.encode, r.prompt)
        resp, text = await run(prompt, gen_params(r))
        return {"id": f"cmpl-{uuid.uuid4().hex[:12]}", "object": "text_completion", "created": int(time.time()),
                "model": model_name, "choices": [{"index": 0, "text": text, "finish_reason": resp.finish_reason}],
                "usage": _usage(prompt, resp.tokens)}

    # --- the verifier's side ------------------------------------------------

    @app.get("/verifier/params", dependencies=[Depends(auth)])
    async def verifier_params():
        return {"num_slot": params.num_slot, "bkt_num": params.bkt_num, "bucket_ms": params.bucket_ms,
                "device": params.device, "s_max": params.s_max, "token_unit": params.token_unit,
                "capacity": params.capacity, "version": P.VERSION}

    @app.get("/verifier/certificates", dependencies=[Depends(auth)])
    async def certificates(after: int = -1, limit: int = 1000):
        certs = [c for c in frontend.log.certificates if c.bkt_start > after][:limit]
        return {"certificates": [c.pack().hex() for c in certs]}

    @app.post("/verifier/nonce", dependencies=[Depends(auth)])
    async def nonce(r: NonceRequest):
        try:
            n = bytes.fromhex(r.nonce)
        except ValueError:
            n = b""
        if len(n) != 16:
            raise HTTPException(400, "nonce must be 16 bytes, hex")
        try:
            cert = await frontend.nonce(n, timeout=2 * params.bkt_num * params.bucket_ms / 1000.0 + 5)
        except asyncio.TimeoutError:
            raise HTTPException(504, "no certificate echoed the nonce in time")
        return {"certificate": cert.pack().hex()}

    @app.post("/verifier/open", dependencies=[Depends(auth)])
    async def open_(r: OpenRequest):
        if r.dir not in P.DIRECTIONS:
            raise HTTPException(400, "dir must be 0x0000 or 0xFFFF")
        op = await asyncio.to_thread(build_opening, params, frontend.log, r.dir, r.bucket, r.byte)
        if op is None:
            raise HTTPException(404, "no certificate covers that bucket")
        return op.to_json()

    return app


def _usage(prompt: list[int], out: list[int]) -> dict:
    return {"prompt_tokens": len(prompt), "completion_tokens": len(out), "total_tokens": len(prompt) + len(out)}
