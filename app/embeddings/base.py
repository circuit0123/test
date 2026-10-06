"""The EmbeddingProvider interface.

An embedding turns text into a list of numbers (a vector) such that texts with
similar meaning end up close together. We compare vectors by cosine similarity.
Code depends on this interface, never on a specific library, so tests can use a
fast fake and production can use a real model.
"""

from typing import Protocol

EMBEDDING_DIM = 384


class EmbeddingProvider(Protocol):
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one L2-normalised vector of length `dim` per input text."""
        ...
