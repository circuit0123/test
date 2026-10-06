"""Compute, store, cache and serve matches.

Flow for one member: candidates -> score each pair -> rank -> reasons ->
store in match_results (source of truth) -> cache in Redis (fast reads).
The background job runs this for every member every few hours; GET /matches
reads Redis, falls back to match_results, and computes on the spot if a member
has never been matched (e.g. they just joined).
"""

import json
import time
import uuid
from collections import Counter
from datetime import UTC, datetime

import structlog
from redis.asyncio import Redis
from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import MatchingSettings
from app.db.models import Capability, MatchResult, Member, Trait
from app.matching.candidates import EXCLUDED_ROLES, feedback_exclusions, find_candidates, load_profiles
from app.matching.ranking import RankingParams, Scored, rank
from app.matching.reasons import build_reason
from app.matching.scoring import MemberProfile, ScoringParams, Weights, score_pair
from app.schemas.matches import MatchesResponse, MatchOut, MatchRow, RecomputeResult
from app.schemas.members import MemberSummary
from app.services import events

log = structlog.get_logger("circuit.matching")


def cache_key(member_id: uuid.UUID) -> str:
    return f"matches:v1:{member_id}"


def scoring_params(m: MatchingSettings) -> ScoringParams:
    return ScoringParams(
        weights=Weights(complementarity=m.w_complementarity, affinity=m.w_affinity, trust_path=m.w_trust_path,
                        timing=m.w_timing, load_penalty=m.w_load_penalty),
        capability_weight=m.capability_weight, sim_floor=m.sim_floor, sim_ceiling=m.sim_ceiling,
        mutual_weight=m.mutual_weight,
    )


def ranking_params(m: MatchingSettings) -> RankingParams:
    return RankingParams(k=m.results_per_member, bridge_share=m.bridge_share, diversity_penalty=m.diversity_penalty,
                         role_cap_share=m.role_cap_share, min_complementarity=m.min_complementarity,
                         exposure_cap=m.max_appearances_per_candidate)


class Vocabulary:
    """Capability and trait display names, for reasons."""

    def __init__(self, capabilities: dict[str, str], traits: dict[str, str]) -> None:
        self.capabilities, self.traits = capabilities, traits

    @classmethod
    async def load(cls, session: AsyncSession) -> "Vocabulary":
        caps = dict((await session.execute(select(Capability.slug, Capability.name))).all())
        traits = dict((await session.execute(select(Trait.slug, Trait.name))).all())
        return cls(caps, traits)


async def compute_for_member(
    session: AsyncSession, member_id: uuid.UUID, settings: MatchingSettings, *,
    profiles: dict[uuid.UUID, MemberProfile] | None = None, vocab: Vocabulary | None = None,
    exposure: Counter | None = None, now: datetime | None = None,
) -> list[MatchRow]:
    now = now or datetime.now(UTC)
    vocab = vocab or await Vocabulary.load(session)
    profiles = profiles if profiles is not None else {}
    if member_id not in profiles:
        profiles.update(await load_profiles(session, {member_id}))
    me = profiles.get(member_id)
    if me is None:
        return []

    found = await find_candidates(session, me, settings.bridge_neighbours_per_need)
    skip = await feedback_exclusions(session, member_id, hide_declined_days=settings.hide_declined_days,
                                     hide_dismissed_days=settings.hide_dismissed_days)
    found.local -= skip
    found.bridge -= skip
    missing = (found.local | found.bridge) - profiles.keys()
    if missing:
        profiles.update(await load_profiles(session, missing))

    params = scoring_params(settings)
    scored: list[Scored] = []
    for cid in found.local | found.bridge:
        candidate = profiles.get(cid)
        if candidate is None:
            continue
        path = found.paths.get(cid)
        pair = score_pair(me, candidate, hops=path.hops if path else None,
                          path_strength=path.strength if path else 0.0, now=now, params=params)
        details = {**pair.details(), "hops": path.hops if path else None}
        main_cap = details["capability_they_help_with"] if pair.components.they_help_you >= pair.components.you_help_them \
            else details["capability_you_help_with"]
        scored.append(Scored(candidate_id=str(cid), role=candidate.role, score=pair.score,
                             source="local" if cid in found.local else "bridge",
                             complementarity=pair.components.complementarity, capability=main_cap, details=details))

    ranked = rank(scored, ranking_params(settings), exposure)
    rows = []
    for r in ranked:
        reason = build_reason(r.details, source=r.source, hops=r.details.get("hops"),
                              capability_names=vocab.capabilities, trait_names=vocab.traits)
        rows.append(MatchRow(candidate_member_id=uuid.UUID(r.candidate_id), rank=r.rank, score=round(r.score, 4),
                             source=r.source, reason=reason, components={**r.details, "rank": r.rank}))
        if exposure is not None:
            exposure[r.candidate_id] += 1
    return rows


async def store(session: AsyncSession, member_id: uuid.UUID, rows: list[MatchRow], computed_at: datetime) -> None:
    """Replace this member's stored results (one transaction, so readers never see half a list)."""
    await session.execute(delete(MatchResult).where(MatchResult.member_id == member_id))
    if rows:
        await session.execute(insert(MatchResult), [
            {"member_id": member_id, "candidate_member_id": r.candidate_member_id, "score": r.score,
             "components": r.components, "reason": r.reason, "source": r.source, "computed_at": computed_at}
            for r in rows
        ])
    await session.commit()


async def cache(redis: Redis, member_id: uuid.UUID, rows: list[MatchRow], computed_at: datetime,
                ttl_hours: float) -> None:
    payload = {"computed_at": computed_at.isoformat(), "items": [r.model_dump(mode="json") for r in rows]}
    try:
        await redis.set(cache_key(member_id), json.dumps(payload), ex=int(ttl_hours * 3600))
    except Exception as exc:  # the cache is optional: never fail because Redis is down
        log.warning("match_cache_write_failed", error=type(exc).__name__)


async def _read_cache(redis: Redis, member_id: uuid.UUID) -> tuple[datetime, list[MatchRow]] | None:
    try:
        raw = await redis.get(cache_key(member_id))
    except Exception as exc:
        log.warning("match_cache_read_failed", error=type(exc).__name__)
        return None
    if raw is None:
        return None
    data = json.loads(raw)
    return datetime.fromisoformat(data["computed_at"]), [MatchRow.model_validate(i) for i in data["items"]]


async def _read_db(session: AsyncSession, member_id: uuid.UUID) -> tuple[datetime, list[MatchRow]] | None:
    results = (await session.scalars(select(MatchResult).where(MatchResult.member_id == member_id))).all()
    if not results:
        return None
    rows = [MatchRow(candidate_member_id=r.candidate_member_id, rank=r.components.get("rank", 0), score=r.score,
                     source=r.source, reason=r.reason or "", components=r.components) for r in results]
    rows.sort(key=lambda r: r.rank)
    return max(r.computed_at for r in results), rows


async def get_matches(
    session: AsyncSession, redis: Redis, member_id: uuid.UUID, settings: MatchingSettings, limit: int
) -> MatchesResponse:
    served_from = "cache"
    found = await _read_cache(redis, member_id)
    if found is None:
        served_from = "database"
        found = await _read_db(session, member_id)
        if found is not None:
            await cache(redis, member_id, found[1], found[0], settings.cache_ttl_hours)
    if found is None:
        served_from = "computed"
        computed_at = datetime.now(UTC)
        rows = await compute_for_member(session, member_id, settings, now=computed_at)
        await store(session, member_id, rows, computed_at)
        await cache(redis, member_id, rows, computed_at, settings.cache_ttl_hours)
        found = (computed_at, rows)

    computed_at, rows = found
    rows = rows[:limit]
    # Names and levels are looked up fresh, so an edited profile shows immediately.
    ids = [r.candidate_member_id for r in rows]
    members = {m.id: m for m in await session.scalars(select(Member).where(Member.id.in_(ids)))} if ids else {}
    items = [
        MatchOut(member=MemberSummary(id=m.id, role=m.role, display_name=m.display_name, city=m.city,
                                      verification_level=m.verification_level),
                 rank=r.rank, score=r.score, source=r.source, reason=r.reason, components=r.components)
        for r in rows if (m := members.get(r.candidate_member_id)) is not None  # skip deleted members
    ]
    return MatchesResponse(computed_at=computed_at, served_from=served_from, items=items)


async def recompute_all(
    sessionmaker: async_sessionmaker[AsyncSession], redis: Redis, settings: MatchingSettings,
    actor_id: uuid.UUID | None = None,
) -> RecomputeResult:
    """The background job: matches for every member, stored and cached."""
    started = time.perf_counter()
    computed_at = datetime.now(UTC)
    exposure: Counter = Counter()
    total = bridge = 0
    async with sessionmaker() as session:
        member_ids = list(await session.scalars(
            select(Member.id).where(Member.role.notin_(EXCLUDED_ROLES)).order_by(Member.id)
        ))
        vocab = await Vocabulary.load(session)
        profiles = await load_profiles(session, set(member_ids))  # load everyone once, reuse for all
        for member_id in member_ids:
            rows = await compute_for_member(session, member_id, settings, profiles=profiles, vocab=vocab,
                                            exposure=exposure, now=computed_at)
            await store(session, member_id, rows, computed_at)
            await cache(redis, member_id, rows, computed_at, settings.cache_ttl_hours)
            total += len(rows)
            bridge += sum(r.source == "bridge" for r in rows)
        result = RecomputeResult(members=len(member_ids), results=total, bridge_results=bridge,
                                 seconds=round(time.perf_counter() - started, 2))
        events.record(session, "matches_recomputed", actor_id=actor_id, payload=result.model_dump())
        await session.commit()
    log.info("matches_recomputed", **result.model_dump())
    return result


async def invalidate(redis: Redis, member_id: uuid.UUID) -> None:
    try:
        await redis.delete(cache_key(member_id))
    except Exception as exc:
        log.warning("match_cache_delete_failed", error=type(exc).__name__)


async def record_shown(session: AsyncSession, member_id: uuid.UUID, response: MatchesResponse) -> None:
    """One `recommendation_shown` event per match returned, so we can later learn which
    recommendations led to intros (and which were ignored)."""
    await events.record_many(session, [
        {"type": "recommendation_shown", "actor_id": member_id, "target_type": "member",
         "target_id": item.member.id,
         "payload": {"rank": item.rank, "score": item.score, "source": item.source,
                     "served_from": response.served_from,
                     "computed_at": response.computed_at.isoformat() if response.computed_at else None}}
        for item in response.items
    ])
    await session.commit()
