"""Deterministic fake embeddings for tests: no model download, instant, repeatable.

Each word is hashed to a few positions in the vector ("feature hashing"), so texts
that share words get similar vectors. Crude, but enough for tests to check that
"more overlap = more similar" logic works.
"""

import hashlib
import math
import re

from app.embeddings.base import EMBEDDING_DIM

_WORD = re.compile(r"[a-z0-9]+")


class FakeEmbeddingProvider:
    def __init__(self, dim: int = EMBEDDING_DIM) -> None:
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    def _embed_one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for word in _WORD.findall(text.lower()):
            digest = hashlib.sha256(word.encode()).digest()
            for i in range(0, 6, 2):  # 3 buckets per word, each with a +/- sign
                bucket = int.from_bytes(digest[i : i + 2], "big") % self.dim
                vec[bucket] += 1.0 if digest[i + 6] % 2 == 0 else -1.0
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0:
            vec[0], norm = 1.0, 1.0  # empty text: fixed unit vector
        return [v / norm for v in vec]
