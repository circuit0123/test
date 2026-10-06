"""Unit tests for the pure scoring functions."""

from datetime import UTC, datetime, timedelta

import pytest

from app.matching.scoring import (
    Components,
    MemberProfile,
    NeedItem,
    OfferItem,
    ScoringParams,
    Weights,
    affinity,
    best_fit,
    combine_complementarity,
    cosine,
    load_penalty,
    need_offer_fit,
    rescale,
    score_pair,
    timing,
    total,
    trust_path,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)
P = ScoringParams()
# Unit vectors along different axes give exact, easy-to-read similarities.
X, Y = (1.0, 0.0), (0.0, 1.0)
XY = (0.7071, 0.7071)


def profile(id, *, needs=(), offers=(), traits=(), city="Testville", slots=3, pending=0, role="founder"):
    return MemberProfile(id=id, role=role, city=city, traits=frozenset(traits), needs=tuple(needs),
                         offers=tuple(offers), open_intro_slots=slots, pending_incoming=pending)


def test_cosine():
    assert cosine(X, X) == pytest.approx(1)
    assert cosine(X, Y) == pytest.approx(0)
    assert cosine(X, (0.0, 0.0)) == 0


def test_rescale_clamps_and_maps():
    assert rescale(0.55, 0.55, 0.85) == 0
    assert rescale(0.70, 0.55, 0.85) == pytest.approx(0.5)
    assert rescale(0.95, 0.55, 0.85) == 1
    assert rescale(0.10, 0.55, 0.85) == 0
    with pytest.raises(ValueError):
        rescale(0.5, 0.8, 0.8)


def test_fit_combines_capability_and_meaning():
    params = ScoringParams(sim_floor=0.0, sim_ceiling=1.0)  # so similarity == cosine here
    need = NeedItem("n", "seo", X)
    assert need_offer_fit(need, OfferItem("o", "seo", X), params).score == pytest.approx(1.0)
    assert need_offer_fit(need, OfferItem("o", "seo", Y), params).score == pytest.approx(0.6)
    assert need_offer_fit(need, OfferItem("o", "pr", X), params).score == pytest.approx(0.4)
    assert need_offer_fit(need, OfferItem("o", "pr", Y), params).score == pytest.approx(0.0)
    # Missing vectors: capability alone counts.
    assert need_offer_fit(need, OfferItem("o", "seo", None), params).score == pytest.approx(0.6)


def test_best_fit_picks_the_strongest_pair():
    params = ScoringParams(sim_floor=0.0, sim_ceiling=1.0)
    needs = [NeedItem("n1", "seo", X), NeedItem("n2", "pr", Y)]
    offers = [OfferItem("o1", "pr", Y), OfferItem("o2", "seo", Y)]
    fit = best_fit(needs, offers, params)
    assert (fit.need_id, fit.offer_id, fit.capability) == ("n2", "o1", "pr")
    assert best_fit([], offers, params) is None


def test_combine_complementarity_rewards_mutual_help():
    assert combine_complementarity(0.8, 0.0, 0.25) == pytest.approx(0.6)
    assert combine_complementarity(0.0, 0.8, 0.25) == pytest.approx(0.6)  # direction doesn't matter
    assert combine_complementarity(0.8, 0.8, 0.25) == pytest.approx(0.8)


def test_affinity():
    assert affinity(frozenset({"a", "b"}), frozenset({"b", "c"}), same_city=False) == pytest.approx(0.7 / 3)
    assert affinity(frozenset(), frozenset(), same_city=True) == pytest.approx(0.3)


def test_trust_path():
    assert trust_path(None, 0.0) == 0
    assert trust_path(1, 0.9) == pytest.approx(0.9)
    assert trust_path(2, 0.5) == pytest.approx(0.4)  # mutual connection, discounted


@pytest.mark.parametrize(("days", "expected"), [(None, 0.5), (-1, 0.0), (3, 1.0), (7, 1.0), (60, 0.5), (90, 0.5)])
def test_timing(days, expected):
    expires = None if days is None else NOW + timedelta(days=days)
    assert timing(expires, NOW) == pytest.approx(expected)


def test_timing_decreases_between_one_week_and_two_months():
    assert timing(NOW + timedelta(days=10), NOW) > timing(NOW + timedelta(days=40), NOW)


@pytest.mark.parametrize(("pending", "slots", "expected"), [(0, 3, 0), (3, 3, 1), (1, 4, 0.25), (9, 3, 1), (0, 0, 1)])
def test_load_penalty(pending, slots, expected):
    assert load_penalty(pending, slots) == pytest.approx(expected)


def test_total_uses_weights_and_subtracts_load():
    c = Components(complementarity=1, they_help_you=1, you_help_them=0, affinity=1, trust_path=1, timing=1,
                   load_penalty=1)
    assert total(c, Weights(1, 0, 0, 0, 0)) == 1
    assert total(c, Weights(0.5, 0.15, 0.15, 0.1, 0.2)) == pytest.approx(0.7)


def test_score_pair_both_directions_and_symmetry():
    params = ScoringParams(sim_floor=0.0, sim_ceiling=1.0)
    a = profile("a", needs=[NeedItem("n", "seo", X)], offers=[OfferItem("o", "pr", Y)])
    b = profile("b", needs=[NeedItem("m", "pr", Y)], offers=[OfferItem("p", "seo", X)])
    ab = score_pair(a, b, hops=None, path_strength=0, now=NOW, params=params)
    ba = score_pair(b, a, hops=None, path_strength=0, now=NOW, params=params)
    assert ab.components.they_help_you == pytest.approx(1) and ab.components.you_help_them == pytest.approx(1)
    assert ab.components.complementarity == pytest.approx(ba.components.complementarity)
    assert ab.details()["capability_they_help_with"] == "seo"
    assert ab.details()["capability_you_help_with"] == "pr"


def test_score_pair_one_way_help_and_busy_candidate():
    params = ScoringParams(sim_floor=0.0, sim_ceiling=1.0)
    a = profile("a", needs=[NeedItem("n", "seo", X)])
    free = profile("b", offers=[OfferItem("p", "seo", X)])
    busy = profile("c", offers=[OfferItem("p", "seo", X)], pending=3, slots=3)
    s_free = score_pair(a, free, hops=None, path_strength=0, now=NOW, params=params)
    s_busy = score_pair(a, busy, hops=None, path_strength=0, now=NOW, params=params)
    assert s_free.components.you_help_them == 0
    assert s_free.components.complementarity == pytest.approx(0.75)
    assert s_busy.score == pytest.approx(s_free.score - 0.2)


def test_connections_and_traits_raise_the_score():
    a = profile("a", needs=[NeedItem("n", "seo", X)], traits={"technical"})
    stranger = profile("b", offers=[OfferItem("p", "seo", X)], city="Elsewhere")
    friend = profile("c", offers=[OfferItem("p", "seo", X)], traits={"technical"})
    s1 = score_pair(a, stranger, hops=None, path_strength=0, now=NOW, params=P)
    s2 = score_pair(a, friend, hops=1, path_strength=0.9, now=NOW, params=P)
    assert s2.score > s1.score
    assert s2.shared_traits == {"technical"}


def test_no_needs_or_offers_means_no_complementarity():
    s = score_pair(profile("a"), profile("b"), hops=1, path_strength=1, now=NOW, params=P)
    assert s.components.complementarity == 0 and s.components.timing == 0
