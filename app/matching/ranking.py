"""Turn scored candidates into one member's final, ordered list.

Rules, in order:
1. Drop non-matches (complementarity below the minimum, or a score <= 0), and
   anyone who already appears in too many other members' lists (exposure cap,
   so one popular mentor isn't flooded with intro requests).
2. Reserve a share of the slots (default 25%) for bridge matches, so
   cross-sector discoveries aren't crowded out by local ones.
3. Fill greedily by score, with a small penalty for repeating the same
   capability (diversity), and at most `role_cap_share` of results from one role.
"""

import math
from collections import Counter
from dataclasses import dataclass, field, replace
from typing import Any


@dataclass(frozen=True)
class Scored:
    candidate_id: str
    role: str
    score: float
    source: str  # "local" or "bridge"
    complementarity: float
    capability: str | None  # main capability the match is about (for diversity)
    details: dict[str, Any] = field(default_factory=dict)
    rank: int | None = None


@dataclass(frozen=True)
class RankingParams:
    k: int = 20
    bridge_share: float = 0.25
    diversity_penalty: float = 0.05
    role_cap_share: float = 0.5
    min_complementarity: float = 0.3
    exposure_cap: int | None = None


def rank(candidates: list[Scored], params: RankingParams, exposure: Counter | None = None) -> list[Scored]:
    eligible = [
        c for c in candidates
        if c.complementarity >= params.min_complementarity and c.score > 0
        and not (exposure is not None and params.exposure_cap is not None
                 and exposure[c.candidate_id] >= params.exposure_cap)
    ]
    role_cap = max(1, math.ceil(params.role_cap_share * params.k))
    bridge_slots = round(params.k * params.bridge_share)

    selected: list[Scored] = []
    _pick(selected, [c for c in eligible if c.source == "bridge"], bridge_slots, params, role_cap)
    _pick(selected, eligible, params.k, params, role_cap)
    # If the role cap left empty slots, fill them anyway: a full list beats a perfectly balanced one.
    _pick(selected, eligible, params.k, params, role_cap=None)

    ordered = sorted(selected, key=lambda c: (-c.score, c.candidate_id))
    return [replace(c, rank=i + 1) for i, c in enumerate(ordered)]


def _pick(selected: list[Scored], pool: list[Scored], until: int, params: RankingParams,
          role_cap: int | None) -> None:
    """Greedily add the best remaining candidate (diversity-adjusted) until `until` are selected."""
    chosen = {c.candidate_id for c in selected}
    remaining = [c for c in pool if c.candidate_id not in chosen]
    while len(selected) < until and remaining:
        caps = Counter(c.capability for c in selected)
        roles = Counter(c.role for c in selected)
        allowed = [c for c in remaining if role_cap is None or roles[c.role] < role_cap]
        if not allowed:
            return

        def adjusted(c: Scored) -> tuple[float, tuple[int, ...]]:
            return (c.score - params.diversity_penalty * caps[c.capability], _reverse(c.candidate_id))

        best = max(allowed, key=adjusted)
        selected.append(best)
        remaining.remove(best)


def _reverse(s: str) -> tuple[int, ...]:
    # Tie-break deterministically: on equal adjusted score, the smaller id wins under max().
    return tuple(-ord(ch) for ch in s)
