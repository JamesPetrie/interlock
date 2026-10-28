import httpx
from harness import MAC, Stack, run

from ilstack import packet as P
from ilstack.api import create_app
from ilstack.tokenizers import MockTokenizer
from ilstack.verifier import Opening, Verifier

PARAMS = P.Params(num_slot=8, bkt_num=10, bucket_ms=10)
AUTH = {"Authorization": "Bearer k1"}


def test_api_end_to_end():
    async def main():
        async with Stack(PARAMS) as s:
            app = create_app(s.fe, MockTokenizer(), "mock-model", {"k1"})
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
                assert (await c.get("/health")).json()["status"] == "ok"
                assert (await c.get("/v1/models")).status_code == 401
                assert (await c.get("/v1/models", headers={"Authorization": "Bearer nope"})).status_code == 401
                r = await c.get("/v1/models", headers=AUTH)
                assert r.status_code == 200 and r.json()["data"][0]["id"] == "mock-model"
                r = await c.post("/v1/chat/completions", headers=AUTH,
                                 json={"messages": [{"role": "user", "content": "abc"}], "max_tokens": 4})
                body = r.json()
                assert r.status_code == 200, body
                assert body["choices"][0]["message"]["content"] == ">tna" and body["choices"][0]["finish_reason"] == "length"
                assert body["usage"]["completion_tokens"] == 4 and body["model"] == "mock-model"
                r = await c.post("/v1/completions", headers=AUTH, json={"prompt": "hello", "max_tokens": 10})
                assert r.status_code == 200 and r.json()["choices"][0]["text"] == "olleh"
                # the verifier's side
                p = (await c.get("/verifier/params", headers=AUTH)).json()
                assert (p["num_slot"], p["bkt_num"], p["bucket_ms"], p["capacity"]) == (8, 10, 10, 8 * 1436)
                v = Verifier(MAC, s.params)
                nonce = v.new_nonce()
                r = await c.post("/verifier/nonce", headers=AUTH, json={"nonce": nonce.hex()})
                cert = P.Certificate.unpack(bytes.fromhex(r.json()["certificate"]))
                assert v.anchor(nonce, cert, 0, 0)["ok"]
                assert (await c.post("/verifier/nonce", headers=AUTH, json={"nonce": "zz"})).status_code == 400
                certs = (await c.get("/verifier/certificates", headers=AUTH)).json()["certificates"]
                assert cert.pack().hex() in certs
                later = (await c.get("/verifier/certificates", headers=AUTH, params={"after": cert.bkt_start})).json()
                assert all(P.Certificate.unpack(bytes.fromhex(h)).bkt_start > cert.bkt_start for h in later["certificates"])
                resp_bucket = next(b for b in range(cert.bkt_start + cert.bkt_num) if s.fe_log.bucket(P.OUTBOUND, b))
                r = await c.post("/verifier/open", headers=AUTH, json={"dir": P.OUTBOUND, "bucket": resp_bucket, "byte": 0})
                assert r.status_code == 200
                res = v.check(Opening.from_json(r.json()))
                assert res["verified"] and res["reference"].dir == P.INBOUND
                assert (await c.post("/verifier/open", headers=AUTH, json={"dir": P.OUTBOUND, "bucket": 10 ** 6, "byte": 0})).status_code == 404
                assert (await c.post("/verifier/open", headers=AUTH, json={"dir": 1, "bucket": 0, "byte": 0})).status_code == 400
                assert (await c.post("/verifier/open", json={"dir": 0, "bucket": 0, "byte": 0})).status_code == 401
    run(main(), timeout=20)
