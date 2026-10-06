"""Match scoring: pure functions only (no database, no I/O, no clock).

    score = w1*complementarity + w2*affinity + w3*trust_path + w4*timing - w5*load_penalty

Every component is a number between 0 and 1:
- complementarity: how well one member's offers meet the other's needs, in BOTH
  directions ("they can help you" and "you can help them"), combined.
- affinity: shared traits, same city.
- trust_path: how strongly connected they already are (direct, or via one mutual).
- timing: how soon the relevant need expires (urgent needs rank higher).
- load_penalty: how busy the candidate already is with intro requests.

"Pure" means the output depends only on the inputs, which makes these functions
easy to unit-test and to reason about.
"""

from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

import numpy as np  # already installed with pgvector; fast vector maths

Vector = Sequence[float]


@dataclass(frozen=True)
class NeedItem:
    id: str
    capability: str
    vector: Vector | None
    expires_at: datetime | None = None


@dataclass(frozen=True)
class OfferItem:
    id: str
    capability: str
    vector: Vector | None


@dataclass(frozen=True)
class MemberProfile:
    """Everything about one member that scoring needs."""

    id: str
    role: str
    city: str | None
    traits: frozenset[str]
    needs: tuple[NeedItem, ...]
    offers: tuple[OfferItem, ...]
    open_intro_slots: int = 3
    pending_incoming: int = 0  # intro requests waiting for this member's answer
    open_to_cross_sector: bool = True


@dataclass(frozen=True)
class Weights:
    complementarity: float = 0.5
    affinity: float = 0.15
    trust_path: float = 0.15
    timing: float = 0.1
    load_penalty: float = 0.2


@dataclass(frozen=True)
class ScoringParams:
    weights: Weights = field(default_factory=Weights)
    capability_weight: float = 0.6
    sim_floor: float = 0.55
    sim_ceiling: float = 0.85
    mutual_weight: float = 0.25


@dataclass(frozen=True)
class Fit:
    """The best need->offer pairing found in one direction."""

    score: float
    need_id: str
    offer_id: str
    capability: str  # the need's capability
    similarity: float  # rescaled embedding similarity, 0..1
    expires_at: datetime | None


@dataclass(frozen=True)
class Components:
    complementarity: float
    they_help_you: float  # candidate's offers vs your needs
    you_help_them: float  # your offers vs candidate's needs
    affinity: float
    trust_path: float
    timing: float
    load_penalty: float

    def as_dict(self) -> dict[str, float]:
        return {k: round(v, 4) for k, v in asdict(self).items()}


# ---------------------------------------------------------------- building blocks

def cosine(a: Vector, b: Vector) -> float:
    va, vb = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    na, nb = float(np.linalg.norm(va)), float(np.linalg.norm(vb))
    return 0.0 if na == 0 or nb == 0 else float(va @ vb) / (na * nb)


def rescale(value: float, floor: float, ceiling: float) -> float:
    """Map [floor, ceiling] onto [0, 1], clamping outside it."""
    if ceiling <= floor:
        raise ValueError("ceiling must be above floor")
    return min(1.0, max(0.0, (value - floor) / (ceiling - floor)))


def need_offer_fit(need: NeedItem, offer: OfferItem, params: ScoringParams) -> Fit:
    same_capability = 1.0 if need.capability == offer.capability else 0.0
    if need.vector is None or offer.vector is None:
        similarity = 0.0
    else:
        similarity = rescale(cosine(need.vector, offer.vector), params.sim_floor, params.sim_ceiling)
    score = params.capability_weight * same_capability + (1 - params.capability_weight) * similarity
    return Fit(score=score, need_id=need.id, offer_id=offer.id, capability=need.capability,
               similarity=similarity, expires_at=need.expires_at)


def best_fit(needs: Sequence[NeedItem], offers: Sequence[OfferItem], params: ScoringParams) -> Fit | None:
    """The single best way these offers could meet these needs (None if either is empty)."""
    best: Fit | None = None
    for need in needs:
        for offer in offers:
            fit = need_offer_fit(need, offer, params)
            if best is None or fit.score > best.score:
                best = fit
    return best


def combine_complementarity(they_help_you: float, you_help_them: float, mutual_weight: float) -> float:
    """Mostly the stronger direction, with a bonus when help flows both ways."""
    strong, weak = max(they_help_you, you_help_them), min(they_help_you, you_help_them)
    return (1 - mutual_weight) * strong + mutual_weight * weak


def affinity(traits_a: frozenset[str], traits_b: frozenset[str], same_city: bool) -> float:
    union = traits_a | traits_b
    jaccard = len(traits_a & traits_b) / len(union) if union else 0.0
    return 0.7 * jaccard + 0.3 * (1.0 if same_city else 0.0)


def trust_path(hops: int | None, path_strength: float) -> float:
    """Direct connection: its strength. Via one mutual: product of both strengths, discounted."""
    if hops is None:
        return 0.0
    if hops == 1:
        return min(1.0, path_strength)
    if hops == 2:
        return min(1.0, 0.8 * path_strength)
    return 0.0


def timing(expires_at: datetime | None, now: datetime) -> float:
    """1.0 if the need expires within a week, falling to 0.5 at 60 days; 0.5 if open-ended."""
    if expires_at is None:
        return 0.5
    days_left = (expires_at - now).total_seconds() / 86400
    if days_left <= 0:
        return 0.0
    if days_left <= 7:
        return 1.0
    if days_left >= 60:
        return 0.5
    return 1.0 - 0.5 * (days_left - 7) / (60 - 7)


def load_penalty(pending_incoming: int, open_intro_slots: int) -> float:
    """0 when free, 1 when the candidate's intro slots are all taken (or they have none)."""
    if open_intro_slots <= 0:
        return 1.0
    return min(1.0, pending_incoming / open_intro_slots)


def total(c: Components, w: Weights) -> float:
    return (w.complementarity * c.complementarity + w.affinity * c.affinity + w.trust_path * c.trust_path
            + w.timing * c.timing - w.load_penalty * c.load_penalty)


# ---------------------------------------------------------------- the whole pair

@dataclass(frozen=True)
class PairScore:
    score: float
    components: Components
    they_help_you: Fit | None
    you_help_them: Fit | None
    shared_traits: frozenset[str]

    def details(self) -> dict[str, Any]:
        """Components plus what drove them, for storage and explanations."""
        return {
            **self.components.as_dict(),
            "capability_they_help_with": self.they_help_you.capability if self.they_help_you else None,
            "capability_you_help_with": self.you_help_them.capability if self.you_help_them else None,
            "shared_traits": sorted(self.shared_traits),
        }


def score_pair(
    me: MemberProfile, candidate: MemberProfile, *, hops: int | None, path_strength: float,
    now: datetime, params: ScoringParams,
) -> PairScore:
    they_help = best_fit(me.needs, candidate.offers, params)
    you_help = best_fit(candidate.needs, me.offers, params)
    th = they_help.score if they_help else 0.0
    yh = you_help.score if you_help else 0.0

    # Timing follows the need behind the stronger direction.
    driver = they_help if th >= yh else you_help
    same_city = bool(me.city and candidate.city and me.city.lower() == candidate.city.lower())
    components = Components(
        complementarity=combine_complementarity(th, yh, params.mutual_weight),
        they_help_you=th,
        you_help_them=yh,
        affinity=affinity(me.traits, candidate.traits, same_city),
        trust_path=trust_path(hops, path_strength),
        timing=timing(driver.expires_at, now) if driver else 0.0,
        load_penalty=load_penalty(candidate.pending_incoming, candidate.open_intro_slots),
    )
    return PairScore(score=total(components, params.weights), components=components, they_help_you=they_help,
                     you_help_them=you_help, shared_traits=me.traits & candidate.traits)
