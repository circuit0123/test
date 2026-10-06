import math

from app.embeddings.fake import FakeEmbeddingProvider


def _cos(a, b):
    return sum(x * y for x, y in zip(a, b, strict=True))


def test_fake_embeddings_are_deterministic_normalised_and_384d():
    p = FakeEmbeddingProvider()
    a1, a2 = p.embed(["growth marketing for apps", "growth marketing for apps"])
    assert a1 == a2
    assert len(a1) == 384
    assert math.isclose(math.sqrt(sum(v * v for v in a1)), 1.0)


def test_fake_embeddings_overlap_means_similar():
    p = FakeEmbeddingProvider()
    need, close, far = p.embed([
        "need help with growth marketing",
        "I offer growth marketing help",
        "patent filing for biotech labs",
    ])
    assert _cos(need, close) > _cos(need, far)


def test_fake_embeddings_handle_empty_text():
    (v,) = FakeEmbeddingProvider().embed([""])
    assert math.isclose(sum(x * x for x in v), 1.0)
