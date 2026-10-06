"""Unit tests for ranking: thresholds, bridge reservation, diversity, caps."""

from collections import Counter

from app.matching.ranking import RankingParams, Scored, rank


def s(id, score, *, source="local", role="mentor", cap="seo", comp=0.8):
    return Scored(candidate_id=id, role=role, score=score, source=source, complementarity=comp, capability=cap)


def ids(result):
    return [r.candidate_id for r in result]


def test_drops_non_matches_and_numbers_ranks():
    out = rank([s("a", 0.5), s("b", 0.6, comp=0.1), s("c", -0.1), s("d", 0.7)],
               RankingParams(k=10, bridge_share=0, diversity_penalty=0, role_cap_share=1))
    assert ids(out) == ["d", "a"]
    assert [r.rank for r in out] == [1, 2]


def test_reserves_bridge_share():
    local = [s(f"l{i}", 0.9 - i * 0.01) for i in range(10)]
    bridge = [s(f"b{i}", 0.3, source="bridge", cap=f"c{i}") for i in range(5)]
    out = rank(local + bridge, RankingParams(k=8, bridge_share=0.25, diversity_penalty=0, role_cap_share=1))
    assert len(out) == 8
    assert sum(r.source == "bridge" for r in out) == 2  # 25% of 8, despite lower scores


def test_fills_with_local_when_too_few_bridge():
    local = [s(f"l{i}", 0.5) for i in range(10)]
    out = rank(local + [s("b0", 0.2, source="bridge")],
               RankingParams(k=8, bridge_share=0.5, diversity_penalty=0, role_cap_share=1))
    assert len(out) == 8 and sum(r.source == "bridge" for r in out) == 1


def test_diversity_prefers_a_different_capability():
    cands = [s("a", 0.60, cap="seo"), s("b", 0.59, cap="seo"), s("c", 0.57, cap="pr")]
    out = rank(cands, RankingParams(k=2, bridge_share=0, diversity_penalty=0.05, role_cap_share=1))
    assert set(ids(out)) == {"a", "c"}  # b would be a second "seo" result


def test_role_cap_limits_one_role_but_still_fills():
    cands = [s(f"m{i}", 0.9, role="mentor") for i in range(4)] + [s("f0", 0.1, role="founder")]
    out = rank(cands, RankingParams(k=4, bridge_share=0, diversity_penalty=0, role_cap_share=0.5))
    roles = Counter(r.role for r in out)
    assert roles["founder"] == 1 and len(out) == 4  # cap made room for the founder, then refilled


def test_exposure_cap_skips_overexposed_candidates():
    out = rank([s("popular", 0.9), s("other", 0.5)],
               RankingParams(k=5, bridge_share=0, role_cap_share=1, exposure_cap=3), Counter({"popular": 3}))
    assert ids(out) == ["other"]


def test_deterministic_on_ties():
    cands = [s("b", 0.5, cap="x"), s("a", 0.5, cap="y")]
    p = RankingParams(k=1, bridge_share=0, diversity_penalty=0, role_cap_share=1)
    assert ids(rank(cands, p)) == ids(rank(list(reversed(cands)), p)) == ["a"]
