"""SQLAlchemy ORM models: the Postgres schema, which is the source of truth.

Conventions:
- Fixed choices (role, status, source...) are plain text columns guarded by a CHECK
  constraint rather than Postgres ENUM types. Same safety, but adding a value later is
  a one-line migration instead of an ALTER TYPE dance.
- Locations are PostGIS `geography(Point, 4326)`: lng/lat on the real globe, so
  distances come out in metres without map-projection maths. Each gets a GIST index.
- Embeddings are pgvector `vector(384)` with an HNSW index for fast nearest-neighbour
  search by cosine distance.
- Undirected connections are stored once, with member_a < member_b.
"""

import uuid
from datetime import datetime
from typing import Any

from geoalchemy2 import Geography
from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

from app.embeddings.base import EMBEDDING_DIM

MEMBER_ROLES = ("student", "founder", "mentor", "investor", "partner_admin")
STARTUP_SOURCES = ("seed", "crawler", "user")
NEED_OWNER_TYPES = ("member", "startup")
CONNECTION_SOURCES = ("mutual", "event", "intro")
INTRO_STATUSES = ("pending", "accepted", "declined", "expired", "cancelled")
MATCH_SOURCES = ("local", "bridge")
CIRCUIT_STATUSES = ("proposed", "active", "completed", "dissolved")
CONSENT_STATES = ("pending", "accepted", "declined")
CLAIM_STATUSES = ("pending", "approved", "rejected")


def _in(column: str, values: tuple[str, ...]) -> str:
    """SQL for a CHECK constraint: column IN ('a', 'b', ...)."""
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _point() -> Geography:
    # spatial_index=False: we declare the GIST index explicitly in __table_args__,
    # so it is visible (and named) in the models and the migration.
    return Geography(geometry_type="POINT", srid=4326, spatial_index=False)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Member(TimestampMixin, Base):
    __tablename__ = "members"
    __table_args__ = (
        CheckConstraint(_in("role", MEMBER_ROLES), name="ck_members_role"),
        CheckConstraint("verification_level BETWEEN 1 AND 4", name="ck_members_verification_level"),
        CheckConstraint("open_intro_slots >= 0", name="ck_members_open_intro_slots"),
        Index("ix_members_location", "location", postgresql_using="gist"),
        Index("ix_members_city", "city"),
        Index("ix_members_role", "role"),
        # One member per identity-provider account (NULLs allowed: seeded members).
        Index("uq_members_auth_subject", "auth_subject", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    bio: Mapped[str | None] = mapped_column(Text)
    location: Mapped[Any | None] = mapped_column(_point())
    city: Mapped[str | None] = mapped_column(String(100))
    verification_level: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="1")
    open_intro_slots: Mapped[int] = mapped_column(Integer, nullable=False, server_default="3")
    circuits_opt_in: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sql_text("false"))
    open_to_cross_sector: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sql_text("true"))
    # The identity provider's user id (the JWT "sub" from Clerk/Supabase), linking
    # their account to this member. Null until linked; dev tokens use `id` instead.
    auth_subject: Mapped[str | None] = mapped_column(String(255))
    # Bumped to invalidate every JWT already issued to this member (Phase 2).
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")


class Startup(TimestampMixin, Base):
    __tablename__ = "startups"
    __table_args__ = (
        CheckConstraint(_in("source", STARTUP_SOURCES), name="ck_startups_source"),
        Index("ix_startups_location", "location", postgresql_using="gist"),
        Index("ix_startups_sector", "sector"),
        # Partial index: map queries filter on hiring=true, so index only those rows.
        Index("ix_startups_hiring", "hiring", postgresql_where=sql_text("hiring")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Natural key shared with the crawler: upserts match on this.
    website_domain: Mapped[str] = mapped_column(String(253), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    sector: Mapped[str | None] = mapped_column(String(50))
    stage: Mapped[str | None] = mapped_column(String(30))
    address: Mapped[str | None] = mapped_column(Text)
    location: Mapped[Any | None] = mapped_column(_point())
    hiring: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sql_text("false"))
    open_roles: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default=sql_text("'[]'::jsonb"))
    tech_stack: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default=sql_text("'[]'::jsonb"))
    claimed_by_member_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id", ondelete="SET NULL")
    )
    source: Mapped[str] = mapped_column(String(20), nullable=False, server_default="seed")


class StartupMember(Base):
    __tablename__ = "startup_members"
    __table_args__ = (Index("ix_startup_members_member_id", "member_id"),)

    startup_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("startups.id", ondelete="CASCADE"), primary_key=True
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id", ondelete="CASCADE"), primary_key=True
    )
    title: Mapped[str | None] = mapped_column(String(120))


class StartupClaim(Base):
    """A member asking to be recognised as the owner of a startup profile.

    A partner_admin reviews the evidence and approves or rejects it. On approval
    the member becomes `startups.claimed_by_member_id` and joins the team.
    """

    __tablename__ = "startup_claims"
    __table_args__ = (
        CheckConstraint(_in("status", CLAIM_STATUSES), name="ck_startup_claims_status"),
        Index("ix_startup_claims_status_created", "status", "created_at"),
        # At most one open claim per member per startup.
        Index(
            "uq_startup_claims_one_pending",
            "startup_id",
            "member_id",
            unique=True,
            postgresql_where=sql_text("status = 'pending'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    startup_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("startups.id", ondelete="CASCADE"), nullable=False
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str | None] = mapped_column(String(120))
    evidence: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(10), nullable=False, server_default="pending")
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id", ondelete="SET NULL")
    )
    decision_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Capability(Base):
    """Fixed vocabulary of things people can need or offer (seeded)."""

    __tablename__ = "capabilities"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    slug: Mapped[str] = mapped_column(String(60), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)


class Trait(Base):
    """Fixed vocabulary of descriptive tags for members and startups (seeded)."""

    __tablename__ = "traits"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    slug: Mapped[str] = mapped_column(String(60), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)


class Need(Base):
    __tablename__ = "needs"
    __table_args__ = (
        CheckConstraint(_in("owner_type", NEED_OWNER_TYPES), name="ck_needs_owner_type"),
        Index("ix_needs_owner", "owner_type", "owner_id"),
        Index("ix_needs_capability_id", "capability_id"),
        Index(
            "ix_needs_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Polymorphic owner (a member or a startup), so there is no foreign key here;
    # the service layer checks the owner exists.
    owner_type: Mapped[str] = mapped_column(String(10), nullable=False)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    capability_id: Mapped[int] = mapped_column(ForeignKey("capabilities.id"), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[Any | None] = mapped_column(Vector(EMBEDDING_DIM))
    embedding_model: Mapped[str | None] = mapped_column(String(100))  # which model made `embedding`
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sql_text("true"))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Offer(Base):
    __tablename__ = "offers"
    __table_args__ = (
        Index("ix_offers_member_id", "member_id"),
        Index("ix_offers_capability_id", "capability_id"),
        Index(
            "ix_offers_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id", ondelete="CASCADE"), nullable=False
    )
    capability_id: Mapped[int] = mapped_column(ForeignKey("capabilities.id"), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[Any | None] = mapped_column(Vector(EMBEDDING_DIM))
    embedding_model: Mapped[str | None] = mapped_column(String(100))  # which model made `embedding`
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sql_text("true"))


class MemberTrait(Base):
    __tablename__ = "member_traits"
    __table_args__ = (Index("ix_member_traits_trait_id", "trait_id"),)

    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id", ondelete="CASCADE"), primary_key=True
    )
    trait_id: Mapped[int] = mapped_column(ForeignKey("traits.id", ondelete="CASCADE"), primary_key=True)


class StartupTrait(Base):
    __tablename__ = "startup_traits"
    __table_args__ = (Index("ix_startup_traits_trait_id", "trait_id"),)

    startup_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("startups.id", ondelete="CASCADE"), primary_key=True
    )
    trait_id: Mapped[int] = mapped_column(ForeignKey("traits.id", ondelete="CASCADE"), primary_key=True)


class Connection(Base):
    """An undirected tie between two members, stored once with member_a < member_b."""

    __tablename__ = "connections"
    __table_args__ = (
        CheckConstraint("member_a < member_b", name="ck_connections_ordered"),
        CheckConstraint("strength >= 0 AND strength <= 1", name="ck_connections_strength"),
        CheckConstraint(_in("source", CONNECTION_SOURCES), name="ck_connections_source"),
        Index("ix_connections_member_b", "member_b"),  # member_a is covered by the PK
    )

    member_a: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id", ondelete="CASCADE"), primary_key=True
    )
    member_b: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id", ondelete="CASCADE"), primary_key=True
    )
    strength: Mapped[float] = mapped_column(Float, nullable=False, server_default="0.5")
    source: Mapped[str] = mapped_column(String(10), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class IntroRequest(Base):
    __tablename__ = "intro_requests"
    __table_args__ = (
        CheckConstraint(_in("status", INTRO_STATUSES), name="ck_intro_requests_status"),
        CheckConstraint("from_member <> to_member", name="ck_intro_requests_not_self"),
        CheckConstraint(_in("match_source", MATCH_SOURCES), name="ck_intro_requests_match_source"),
        CheckConstraint("from_rating IS NULL OR from_rating BETWEEN 1 AND 5", name="ck_intro_requests_from_rating"),
        CheckConstraint("to_rating IS NULL OR to_rating BETWEEN 1 AND 5", name="ck_intro_requests_to_rating"),
        Index("ix_intro_requests_to_status", "to_member", "status"),
        Index("ix_intro_requests_from_created", "from_member", "created_at"),
        # At most one open request from one member to another.
        Index("uq_intro_requests_one_pending", "from_member", "to_member", unique=True,
              postgresql_where=sql_text("status = 'pending'")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    from_member: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id", ondelete="CASCADE"), nullable=False
    )
    to_member: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id", ondelete="CASCADE"), nullable=False
    )
    via_member: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(10), nullable=False, server_default="pending")
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    response_note: Mapped[str | None] = mapped_column(Text)  # optional message with accept/decline
    # Which recommendation led to this request (copied from match_results, if any),
    # so we can measure acceptance for local vs bridge matches.
    match_source: Mapped[str | None] = mapped_column(String(10))
    match_score: Mapped[float | None] = mapped_column(Float)
    # Outcome feedback after an accepted intro: each side rates it 1-5.
    from_rating: Mapped[int | None] = mapped_column(SmallInteger)
    to_rating: Mapped[int | None] = mapped_column(SmallInteger)


class MatchResult(Base):
    __tablename__ = "match_results"
    __table_args__ = (
        CheckConstraint(_in("source", MATCH_SOURCES), name="ck_match_results_source"),
        Index("ix_match_results_member_score", "member_id", sql_text("score DESC")),
    )

    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id", ondelete="CASCADE"), primary_key=True
    )
    candidate_member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id", ondelete="CASCADE"), primary_key=True
    )
    score: Mapped[float] = mapped_column(Float, nullable=False)
    components: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(10), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Circuit(Base):
    __tablename__ = "circuits"
    __table_args__ = (
        CheckConstraint(_in("status", CIRCUIT_STATUSES), name="ck_circuits_status"),
        Index("ix_circuits_round_id", "round_id"),
        Index("ix_circuits_status_expires", "status", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Groups the circuits proposed by one solver run (Phase 7).
    round_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, server_default="proposed")
    score: Mapped[float] = mapped_column(Float, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CircuitLeg(Base):
    """One directed 'giver helps receiver with capability' step of a circuit."""

    __tablename__ = "circuit_legs"
    __table_args__ = (
        CheckConstraint(_in("consent", CONSENT_STATES), name="ck_circuit_legs_consent"),
        CheckConstraint("rating IS NULL OR rating BETWEEN 1 AND 5", name="ck_circuit_legs_rating"),
        CheckConstraint("giver_id <> receiver_id", name="ck_circuit_legs_not_self"),
        UniqueConstraint("circuit_id", "giver_id", name="uq_circuit_legs_circuit_giver"),
        Index("ix_circuit_legs_giver_id", "giver_id"),
        Index("ix_circuit_legs_receiver_id", "receiver_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    circuit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("circuits.id", ondelete="CASCADE"), nullable=False
    )
    giver_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id", ondelete="CASCADE"), nullable=False
    )
    receiver_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id", ondelete="CASCADE"), nullable=False
    )
    capability_id: Mapped[int] = mapped_column(ForeignKey("capabilities.id"), nullable=False)
    consent: Mapped[str] = mapped_column(String(10), nullable=False, server_default="pending")
    fulfilled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sql_text("false"))
    rating: Mapped[int | None] = mapped_column(SmallInteger)


class Event(Base):
    """Append-only product event log. A database trigger rejects UPDATE and DELETE.

    No foreign keys on purpose: history must survive even if a member is deleted.
    """

    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_actor_created", "actor_id", "created_at"),
        Index("ix_events_type_created", "type", "created_at"),
        Index("ix_events_target", "target_type", "target_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    type: Mapped[str] = mapped_column(String(60), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(30))
    # Text, because targets can be uuids (members) or integers (capabilities).
    target_id: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=sql_text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
