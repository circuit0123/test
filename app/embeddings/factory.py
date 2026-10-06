from app.config import Settings
from app.embeddings.base import EmbeddingProvider


def get_embedding_provider(settings: Settings) -> EmbeddingProvider:
    if settings.embedding_provider == "fake":
        from app.embeddings.fake import FakeEmbeddingProvider

        return FakeEmbeddingProvider()
    from app.embeddings.fastembed_provider import FastEmbedProvider

    return FastEmbedProvider(settings.embedding_model, cache_dir=settings.embedding_cache_dir)
