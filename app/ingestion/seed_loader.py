"""Load crawler-shaped seed JSON into Postgres, computing embeddings.

    uv run python -m app.ingestion.seed_loader                 # seed/data, real embeddings
    uv run python -m app.ingestion.seed_loader --embeddings fake

Idempotent: running it twice leaves the database exactly as after one run.
- startups upsert on website_domain (the crawler's natural key)
- members, needs and offers upsert on the fixed ids in the seed files
- link tables (traits, team) are replaced for each loaded owner
- embeddings are only computed for new texts or texts that changed

"Upsert" = INSERT ... ON CONFLICT DO UPDATE: insert the row, or update it if a
row with the same key already exists. Everything runs in one transaction, so a
failure halfway leaves the database untouched.
"""

import argparse
import asyncio
import json
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

import structlog
from pydantic import BaseModel, TypeAdapter
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.config import get_settings
from app.db import models as m
from app.db.session import create_engine
from app.embeddings.base import EmbeddingProvider
from app.embeddings.factory import get_embedding_provider
from app.ingestion import schemas as s
from app.logging import configure_logging

log = structlog.get_logger("circuit.seed_loader")

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "seed" / "data"
REFERENCE_DIR = Path(__file__).resolve().parents[2] / "seed" / "reference"
CHUNK = 500  # rows per INSERT statement

T = TypeVar("T", bound=BaseModel)


@dataclass
class SeedBundle:
    capabilities: list[s.Capability]
    traits: list[s.Trait]
    startups: list[s.CrawlerStartup]
    profiles: list[s.StartupProfile] = field(default_factory=list)
    members: list[s.SeedMember] = field(default_factory=list)
    connections: list[s.SeedConnection] = field(default_factory=list)

    @classmethod
    def from_dirs(cls, data_dir: Path, reference_dir: Path = REFERENCE_DIR) -> "SeedBundle":
        def read(path: Path, model: type[T], required: bool = True) -> list[T]:
            if not path.exists() and not required:
                return []
            return TypeAdapter(list[model]).validate_json(path.read_text())  # type: ignore[valid-type]

        return cls(
            capabilities=read(reference_dir / "capabilities.json", s.Capability),
            traits=read(reference_dir / "traits.json", s.Trait),
            startups=read(data_dir / "startups.json", s.CrawlerStartup),
            profiles=read(data_dir / "startup_profiles.json", s.StartupProfile, required=False),
            members=read(data_dir / "members.json", s.SeedMember, required=False),
            connections=read(data_dir / "connections.json", s.SeedConnection, required=False),
        )


def point(lat: float | None, lng: float | None) -> str | None:
    """PostGIS 'extended well-known text'. Note the order: longitude first."""
    if lat is None or lng is None:
        return None
    return f"SRID=4326;POINT({lng} {lat})"


def chunks(rows: Sequence[dict[str, Any]], size: int = CHUNK) -> Iterable[Sequence[dict[str, Any]]]:
    for i in range(0, len(rows), size):
        yield rows[i : i + size]


async def upsert(
    conn: AsyncConnection, model: type[m.Base], rows: Sequence[dict[str, Any]],
    key: list[str], update: list[str] | None = None,
) -> None:
    """INSERT rows; on a key clash update the `update` columns (or do nothing)."""
    for part in chunks(rows):
        stmt = insert(model).values(list(part))
        if update:
            stmt = stmt.on_conflict_do_update(index_elements=key, set_={c: stmt.excluded[c] for c in update})
        else:
            stmt = stmt.on_conflict_do_nothing(index_elements=key)
        await conn.execute(stmt)


async def load_reference(conn: AsyncConnection, bundle: SeedBundle) -> tuple[dict[str, int], dict[str, int]]:
    await upsert(conn, m.Capability, [c.model_dump() for c in bundle.capabilities], ["slug"], ["name", "description"])
    await upsert(conn, m.Trait, [t.model_dump() for t in bundle.traits], ["slug"], ["name"])
    caps = dict((await conn.execute(select(m.Capability.slug, m.Capability.id))).all())
    traits = dict((await conn.execute(select(m.Trait.slug, m.Trait.id))).all())
    return caps, traits


async def load_startups(conn: AsyncConnection, bundle: SeedBundle) -> dict[str, uuid.UUID]:
    rows = [
        {
            "name": st.name, "website_domain": st.website_domain, "description": st.description,
            "sector": st.sector, "address": st.address, "location": point(st.lat, st.lng),
            "hiring": st.hiring, "open_roles": st.open_roles, "tech_stack": st.tech_stack, "source": st.source,
        }
        for st in bundle.startups
    ]
    # Crawler-owned fields only: never overwrite claimed_by_member_id or stage here.
    crawler_fields = ["name", "description", "sector", "address", "location", "hiring", "open_roles", "tech_stack"]
    await upsert(conn, m.Startup, rows, ["website_domain"], crawler_fields)
    domains = [st.website_domain for st in bundle.startups]
    result = await conn.execute(
        select(m.Startup.website_domain, m.Startup.id).where(m.Startup.website_domain.in_(domains))
    )
    return dict(result.all())


async def load_members(conn: AsyncConnection, bundle: SeedBundle, trait_ids: dict[str, int]) -> None:
    fields = ["role", "display_name", "bio", "location", "city", "verification_level",
              "open_intro_slots", "circuits_opt_in", "open_to_cross_sector"]
    rows = [
        {"id": mb.id, "location": point(mb.lat, mb.lng),
         **mb.model_dump(include=set(fields) - {"location"})}
        for mb in bundle.members
    ]
    await upsert(conn, m.Member, rows, ["id"], fields)

    member_ids = [mb.id for mb in bundle.members]
    if member_ids:
        await conn.execute(delete(m.MemberTrait).where(m.MemberTrait.member_id.in_(member_ids)))
    links = [{"member_id": mb.id, "trait_id": trait_ids[t]} for mb in bundle.members for t in mb.traits]
    await upsert(conn, m.MemberTrait, links, ["member_id", "trait_id"])


async def load_profiles(
    conn: AsyncConnection, bundle: SeedBundle, startup_ids: dict[str, uuid.UUID], trait_ids: dict[str, int]
) -> None:
    for profile in bundle.profiles:
        sid = startup_ids[profile.website_domain]
        values: dict[str, Any] = {"stage": profile.stage}
        await conn.execute(m.Startup.__table__.update().where(m.Startup.id == sid).values(**values))
        if profile.team:
            # The first team member claims the profile, unless someone already has.
            await conn.execute(
                m.Startup.__table__.update()
                .where(m.Startup.id == sid, m.Startup.claimed_by_member_id.is_(None))
                .values(claimed_by_member_id=profile.team[0].member_id)
            )
    profiled = [startup_ids[p.website_domain] for p in bundle.profiles]
    if profiled:
        await conn.execute(delete(m.StartupTrait).where(m.StartupTrait.startup_id.in_(profiled)))
        await conn.execute(delete(m.StartupMember).where(m.StartupMember.startup_id.in_(profiled)))
    traits = [{"startup_id": startup_ids[p.website_domain], "trait_id": trait_ids[t]}
              for p in bundle.profiles for t in p.traits]
    team = [{"startup_id": startup_ids[p.website_domain], "member_id": tm.member_id, "title": tm.title}
            for p in bundle.profiles for tm in p.team]
    await upsert(conn, m.StartupTrait, traits, ["startup_id", "trait_id"])
    await upsert(conn, m.StartupMember, team, ["startup_id", "member_id"], ["title"])


async def _texts_needing_embeddings(
    conn: AsyncConnection, model: type[m.Need] | type[m.Offer], rows: list[dict[str, Any]]
) -> set[uuid.UUID]:
    """Ids whose text is new, changed, or has no embedding yet."""
    ids = [r["id"] for r in rows]
    existing: dict[uuid.UUID, tuple[str, bool]] = {}
    for part in range(0, len(ids), CHUNK):
        result = await conn.execute(
            select(model.id, model.text, model.embedding.is_(None)).where(model.id.in_(ids[part : part + CHUNK]))
        )
        existing.update({row[0]: (row[1], row[2]) for row in result})
    return {
        r["id"] for r in rows
        if r["id"] not in existing or existing[r["id"]][0] != r["text"] or existing[r["id"]][1]
    }


async def load_needs_offers(
    conn: AsyncConnection, bundle: SeedBundle, startup_ids: dict[str, uuid.UUID],
    cap_ids: dict[str, int], embedder: EmbeddingProvider,
) -> dict[str, int]:
    needs = [
        {"id": n.id, "owner_type": "member", "owner_id": mb.id, "capability_id": cap_ids[n.capability], "text": n.text}
        for mb in bundle.members for n in mb.needs
    ] + [
        {"id": n.id, "owner_type": "startup", "owner_id": startup_ids[p.website_domain],
         "capability_id": cap_ids[n.capability], "text": n.text}
        for p in bundle.profiles for n in p.needs
    ]
    offers = [
        {"id": o.id, "member_id": mb.id, "capability_id": cap_ids[o.capability], "text": o.text}
        for mb in bundle.members for o in mb.offers
    ]

    embedded = 0
    for model, rows in ((m.Need, needs), (m.Offer, offers)):
        to_embed = await _texts_needing_embeddings(conn, model, rows)
        pending = [r for r in rows if r["id"] in to_embed]
        if pending:
            vectors = embedder.embed([r["text"] for r in pending])
            for row, vec in zip(pending, vectors, strict=True):
                row["embedding"] = vec
            embedded += len(pending)
        # Rows whose text is unchanged keep their stored embedding: we only write
        # the embedding column for rows we just embedded.
        update_cols = [c for c in rows[0] if c != "id"] if rows else []
        await upsert(conn, model, pending, ["id"], update_cols + ["embedding"])
        unchanged = [r for r in rows if r["id"] not in to_embed]
        await upsert(conn, model, unchanged, ["id"], update_cols)
    return {"needs": len(needs), "offers": len(offers), "embedded": embedded}


async def load_connections(conn: AsyncConnection, bundle: SeedBundle) -> None:
    rows = []
    for c in bundle.connections:
        a, b = sorted([c.a, c.b])  # stored once, smaller id first (matches the CHECK constraint)
        rows.append({"member_a": a, "member_b": b, "strength": c.strength, "source": c.source})
    await upsert(conn, m.Connection, rows, ["member_a", "member_b"], ["strength", "source"])


async def load(engine: AsyncEngine, bundle: SeedBundle, embedder: EmbeddingProvider) -> dict[str, int]:
    async with engine.begin() as conn:  # one transaction: all or nothing
        cap_ids, trait_ids = await load_reference(conn, bundle)
        startup_ids = await load_startups(conn, bundle)
        await load_members(conn, bundle, trait_ids)
        await load_profiles(conn, bundle, startup_ids, trait_ids)
        counts = await load_needs_offers(conn, bundle, startup_ids, cap_ids, embedder)
        await load_connections(conn, bundle)
    summary = {
        "capabilities": len(bundle.capabilities), "traits": len(bundle.traits),
        "startups": len(bundle.startups), "members": len(bundle.members),
        "connections": len(bundle.connections), **counts,
    }
    log.info("seed_loaded", **summary)
    return summary


async def _main(data_dir: Path, embeddings: str | None) -> None:
    settings = get_settings()
    if embeddings:
        settings = settings.model_copy(update={"embedding_provider": embeddings})
    bundle = SeedBundle.from_dirs(data_dir)
    engine = create_engine(settings.database_url)
    try:
        summary = await load(engine, bundle, get_embedding_provider(settings))
    finally:
        await engine.dispose()
    print(json.dumps(summary, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Load seed JSON into Postgres.")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--embeddings", choices=["fastembed", "fake"], default=None,
                        help="override EMBEDDING_PROVIDER from settings")
    args = parser.parse_args()
    configure_logging(get_settings().log_level)
    asyncio.run(_main(args.data_dir, args.embeddings))


if __name__ == "__main__":
    main()
