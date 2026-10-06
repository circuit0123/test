"""pairs.json must stay valid against the committed seed data."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_pairs_reference_seed_members_and_are_balanced():
    pairs = json.loads((ROOT / "tests/eval/pairs.json").read_text())
    members = {m["id"] for m in json.loads((ROOT / "seed/data/members.json").read_text())}
    assert 40 <= len(pairs) <= 60
    labels = [p["label"] for p in pairs]
    assert set(labels) == {"good", "bad"} and min(labels.count("good"), labels.count("bad")) >= 20
    for p in pairs:
        assert p["a"] in members and p["b"] in members and p["a"] != p["b"], "regenerate pairs after reseeding"
        assert p["note"]
    assert len({frozenset((p["a"], p["b"])) for p in pairs}) == len(pairs)
