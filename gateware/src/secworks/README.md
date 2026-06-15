# Vendored: secworks/sha256

SHA-256 core (BSD-2-Clause, Secworks Sweden AB).

- Source: https://github.com/secworks/sha256
- Commit: 837c5cc396f001d18f2c765721c585716eb439ae
- Files: sha256_core.v, sha256_w_mem.v, sha256_k_constants.v (the streaming
  block core; the memory-mapped sha256.v top wrapper is NOT vendored).
- sha256_model.py is their Python reference (kept for cross-checks).

Validated against NIST + hashlib fuzz by gateware/tb/test_sha256_core.py.
The HMAC FSM is built over this core (their hmac repo is non-functional).
