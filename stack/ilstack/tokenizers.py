"""Tokenizers: text and chat messages to token ids, and back. Standard
components; only the frontend uses them.
"""
from __future__ import annotations


class Tokenizer:
    def encode(self, text: str) -> list[int]:
        raise NotImplementedError

    def decode(self, tokens: list[int]) -> str:
        raise NotImplementedError

    def chat(self, messages: list[dict]) -> list[int]:
        """Token ids of a chat prompt, generation prompt included."""
        raise NotImplementedError


class MockTokenizer(Tokenizer):
    """One token per UTF-8 byte, offset so that 0 stays unused."""

    def encode(self, text: str) -> list[int]:
        return [b + 1 for b in text.encode()]

    def decode(self, tokens: list[int]) -> str:
        return bytes(max(0, t - 1) for t in tokens).decode(errors="replace")

    def chat(self, messages: list[dict]) -> list[int]:
        return self.encode("".join(f"<{m['role']}>{m['content']}" for m in messages) + "<assistant>")


class HfTokenizer(Tokenizer):
    def __init__(self, name: str):
        from transformers import AutoTokenizer
        self.tok = AutoTokenizer.from_pretrained(name)

    def encode(self, text: str) -> list[int]:
        return _ids(self.tok(text, add_special_tokens=True))

    def decode(self, tokens: list[int]) -> str:
        return self.tok.decode(tokens, skip_special_tokens=True)

    def chat(self, messages: list[dict]) -> list[int]:
        return _ids(self.tok.apply_chat_template(messages, add_generation_prompt=True, tokenize=True))


def _ids(encoded) -> list[int]:
    """transformers returns a list or a BatchEncoding depending on the version."""
    if isinstance(encoded, dict) or hasattr(encoded, "keys"):
        return list(encoded["input_ids"])
    return list(encoded)
