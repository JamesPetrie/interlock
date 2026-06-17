"""Prover compute node — runs the LLM workload.   [diagram: Prover compute]

Untrusted. Serves one op:  workload : request packet -> response packet.
(Recomputation — re-running the model to score a response into an unexplained-
information bound U — is a later step, added as recomputation or a ZKP; it is not
part of this node yet.) Set MOCK=1 to run without torch.
"""
import os

import wire
from common import serve, PORT_COMPUTE

MOCK = os.environ.get("MOCK") == "1"

if MOCK:
    def gen(prompt, n):
        return [3681, 338, 278, 7483, 310], "Paris is the capital of France."
else:
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    MODEL = os.environ.get("MODEL_DIR", "/home/claude/models/llama-2-7b-hf")
    _tok = AutoTokenizer.from_pretrained(MODEL)
    if _tok.pad_token is None:
        _tok.pad_token = _tok.eos_token
    _model = AutoModelForCausalLM.from_pretrained(MODEL, dtype=torch.float16).cuda().eval()

    @torch.no_grad()
    def gen(prompt, n):
        ids = _tok(prompt, return_tensors="pt").input_ids.cuda()
        out = _model.generate(ids, max_new_tokens=n, do_sample=False,
                              pad_token_id=_tok.eos_token_id)
        new = out[0, ids.shape[1]:].tolist()
        return new, _tok.decode(new)


def handler(req):
    if req["op"] == "workload":                       # request packet -> response packet
        p = wire.parse_packet("in", bytes.fromhex(req["request_packet"]))
        toks, text = gen(p["ciphertext"].decode(errors="replace"), req.get("max_new", 48))
        # response declares the bucket the interlock assigned it (design A)
        out = wire.output_packet(p["request_id"], req["resp_bucket"], wire.tokens_to_bytes(toks))
        return {"response_packet": out.hex(), "text": text}
    return {"error": "unknown op"}


if __name__ == "__main__":
    serve(PORT_COMPUTE, handler, "compute")
