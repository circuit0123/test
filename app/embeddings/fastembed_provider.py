"""Real local embeddings with fastembed (ONNX, CPU only, no API calls).

The model (~70 MB) downloads from HuggingFace on first use and is cached.
"""

from app.embeddings.base import EMBEDDING_DIM

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"


class FastEmbedProvider:
    def __init__(self, model_name: str = DEFAULT_MODEL, cache_dir: str | None = None) -> None:
        from fastembed import TextEmbedding  # imported lazily: slow to import

        self.dim = EMBEDDING_DIM
        self.model_id = model_name
        self._model = TextEmbedding(model_name=model_name, cache_dir=cache_dir)

    def embed(self, texts: list[str]) -> list[list[float]]:
        # fastembed already returns normalised vectors for this model.
        return [vec.tolist() for vec in self._model.embed(texts)]
