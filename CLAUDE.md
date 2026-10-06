# Circuit — backend MVP build spec

## What we're building
Circuit is a startup ecosystem platform with two sides:
1. A geographic discovery app: students (often from lower-tier colleges) find nearby
   startups that are hiring, on a map.
2. A networking platform: founders, mentors and investors get matched on needs and
   offers, including cross-sector "bridge" matches and 3-person exchange circuits.

This build is the BACKEND ONLY. A frontend (map view + dashboard) comes later, so
design clean JSON APIs that a frontend can consume. Web crawling is OUT of scope;
use seed data shaped exactly like future crawler output instead.

## How to work with me
- I'm learning backend development. After each phase, explain what you built and
  why in plain language, including any concept I might not know.
- Work in the phases below, in order. At the end of each phase: run the tests,
  make a git commit with a clear message, summarise, and STOP for my OK.
- Keep it simple. One repo, one FastAPI app, background jobs in the same codebase.
  No microservices, no Kubernetes, no GraphQL.
- Ask before adding any dependency not listed below.
- Never commit secrets. Use .env (gitignored) and provide .env.example.
- I develop on Windows (WSL2, x86_64) and macOS (Apple Silicon, ARM64). Everything
  must run on both. Verify every Docker image supports both architectures.

## Stack (decided)
- Python 3.12, managed with uv
- FastAPI, Pydantic v2
- SQLAlchemy 2 (async) + Alembic migrations, psycopg 3
- Postgres 16 with PostGIS and pgvector. Build our own small Dockerfile FROM
  postgres:16 that installs the PostGIS and pgvector packages from the PGDG apt repo,
  so it works on both ARM64 and x86_64.
- GeoAlchemy2 and pgvector-python for the column types
- Neo4j 5 community (official image) with the official neo4j Python driver
- Redis 7 (official image) with redis-py
- Google OR-Tools (CP-SAT) for circuit selection
- fastembed with BAAI/bge-small-en-v1.5 (384 dims) for local embeddings, behind an
  EmbeddingProvider interface, plus a deterministic FakeEmbeddingProvider for tests
- APScheduler for scheduled background jobs
- structlog for JSON logs
- pytest + httpx for tests
- docker compose for local services

## Architecture (six layers)
1. Sources/ingestion — OUT of scope now. Provide app/ingestion/seed_loader.py that
   loads JSON files shaped like crawler output (see "Seed data").
2. Data — Postgres is the source of truth. Neo4j is a derived copy of relationships,
   rebuildable from Postgres at any time. Redis holds cached match results and is
   also disposable.
3. Intelligence — matching engine, circuit round, geo search, feedback loop.
4. API — one FastAPI service.
5. Users — later (frontend). Design endpoints for: student map view, network app,
   partner dashboard.

## Repo structure
app/
  main.py            FastAPI app, middleware, routers
  config.py          settings from env (pydantic-settings)
  db/                engine, session, models, base
  api/               routers: auth, members, startups, geo, matches, intros,
                     circuits, dashboard, events, health
  services/          business logic (keep routers thin)
  matching/          scoring.py (pure functions), candidates.py, ranking.py, reasons.py
  circuits/          edges.py, cycles.py, solver.py, rounds.py
  graph/             neo4j client and sync
  jobs/              scheduler and job definitions
  embeddings/        provider interface + implementations
  ingestion/         seed_loader.py
  logging.py         structlog setup + request middleware
migrations/          alembic
seed/                JSON seed files + generator script
tests/
  eval/              hand-labelled match pairs (see Phase 4)
docker/postgres/Dockerfile
docker-compose.yml, .env.example, README.md

## Data model (Postgres)
- members: id (uuid), role (student | founder | mentor | investor | partner_admin),
  display_name, bio, location (PostGIS geography point), city, verification_level
  (1-4), open_intro_slots, circuits_opt_in, open_to_cross_sector, token_version,
  created_at, updated_at
- startups: id, name, website_domain (unique), description, sector, stage, address,
  location (geography point), hiring (bool), open_roles (jsonb), tech_stack (jsonb),
  claimed_by_member_id (nullable), source (seed | crawler | user), created_at,
  updated_at
- startup_members: startup_id, member_id, title
- capabilities: id, slug, name, description   (fixed list, seeded, ~40-80 items)
- traits: id, slug, name                       (fixed list, seeded, ~20-30 items)
- needs: id, owner_type (member | startup), owner_id, capability_id, text,
  embedding vector(384), active, expires_at
- offers: id, member_id, capability_id, text, embedding vector(384), active
- member_traits, startup_traits: link tables
- connections: member_a, member_b, strength (0-1), source (mutual | event | intro),
  created_at
- intro_requests: id, from_member, to_member, via_member (nullable, for warm intros),
  status (pending | accepted | declined | expired), reason, created_at, responded_at
- match_results: member_id, candidate_member_id, score, components (jsonb), reason,
  source (local | bridge), computed_at
- circuits: id, round_id, status (proposed | active | completed | dissolved), score,
  expires_at, created_at
- circuit_legs: circuit_id, giver_id, receiver_id, capability_id, consent
  (pending | accepted | declined), fulfilled, rating
- events: id, actor_id, type, target_type, target_id, payload (jsonb), created_at
  (append-only; never update or delete rows)
Use spatial (GIST) indexes on location columns and an HNSW index on embeddings.

## Auth (MVP)
- Provider is not chosen yet (Clerk vs Supabase). Build JWT verification against a
  configurable JWKS URL so switching providers later is config only.
- For local development, add a DEV_AUTH mode with an endpoint that issues signed
  test JWTs for seeded members. It must be impossible to enable in production
  (refuse to start if DEV_AUTH=true and ENV=production).
- JWT claims: sub, role, verification_level, token_version, exp. Reject tokens whose
  token_version doesn't match the database.
- Dependency require_level(n) for endpoints that need a verification level.
  Proposed rules: level 1 browse, level 2 get matches, level 3 request intros.

## Logging
- Middleware logs one JSON line per request: request_id, method, path, status,
  duration_ms, user_id (if authenticated). Return X-Request-ID header.
- Log exceptions with stack traces and the request_id.
- Never log tokens, passwords, emails, phone numbers or names. IDs only.
- Logs are for debugging. The events table is product data. Keep them separate.

## Matching engine (v1)
- Candidate generation from two sources, merged:
  a) Local funnel: same city, shared traits, 1-2 hop connections (recursive SQL or
     Neo4j once Phase 6 exists).
  b) Bridge search: whole pool, need-to-offer by capability match plus embedding
     similarity, ignoring sector entirely.
- Score (pure function, fully unit-tested):
  score = w1*complementarity + w2*affinity + w3*trust_path + w4*timing
          - w5*load_penalty
  complementarity is computed in BOTH directions (A's need vs B's offer, and B's
  need vs A's offer) and combined. Weights live in config.
- Ranking: diversity, cap intro requests per person, reserve a configurable share
  (default 25%) of each member's results for bridge matches.
- Reasons: template-based one-line explanation built from the score components and
  shared traits (no LLM call needed).
- Store each result's components in match_results so we can explain and later learn.
- Background job computes matches for all members every few hours and caches them
  in Redis; the API reads from Redis and falls back to match_results.

## Geo search
- GET /geo/startups/nearby?lat&lng&radius_km&hiring&capability -> nearest first,
  with distance_km. For students, distance is a hard filter.
- GET /geo/startups/bbox?min_lat&min_lng&max_lat&max_lng -> for map viewports,
  return lightweight records.

## Exchange circuits
- Build directed CAN_HELP edges in Neo4j (A -> B if A's offer fits B's need above a
  threshold), only for members with circuits_opt_in and open capacity.
- Find 3-person cycles in Cypher, deduplicated (a.id < b.id AND a.id < c.id).
- Score a circuit by its weakest link for now.
- Choose non-overlapping circuits with OR-Tools CP-SAT, respecting per-person
  capacity, maximising total score.
- Write proposals to circuits/circuit_legs with a 72-hour expiry. Endpoints to view,
  accept and decline. If anyone declines, dissolve and offer remaining legs as normal
  matches. Run weekly via the scheduler, and also via a manual admin endpoint.

## Dashboard endpoints (for the partner dashboard, later frontend)
- Gap map: for a region (city or bbox), per capability: count of active needs vs
  active offers, sorted by largest gap.
- Network stats: members by role, intros requested/accepted, acceptance rate split
  by local vs bridge matches, circuits proposed/completed.

## Seed data
- A generator script producing realistic fake data around a configurable city
  centre (lat/lng in .env): ~150 startups, ~300 members across roles (students the
  largest group), needs/offers mapped to capabilities, traits, some connections.
  Include deliberate cross-sector bridge cases (e.g. a biotech startup needing
  marketing and a deep-tech marketer offering it).
- Startup records must match the future crawler schema: name, website_domain,
  description, sector, address, lat, lng, hiring, open_roles, tech_stack, source.
- seed_loader.py loads them, computes embeddings, and is idempotent (upsert by
  website_domain).

## Phases (stop after each)
0. Scaffold: repo structure, uv project, docker compose (Postgres+PostGIS+pgvector,
   Neo4j, Redis), config, logging middleware, /health and /health/deps endpoints that
   check every service. Verify it runs.
1. Data model: SQLAlchemy models, Alembic migrations, indexes, seed generator and
   loader, capability and trait lists.
2. Auth + members: JWKS verification, DEV_AUTH, require_level, member and startup
   CRUD, needs/offers endpoints, profile claiming flow for startups.
3. Geo: nearby and bbox endpoints with tests.
4. Matching: embeddings provider, scoring (pure, unit-tested), candidates, ranking,
   reasons, background job, Redis cache, GET /matches. Also create
   tests/eval/pairs.json with ~50 labelled good/bad pairs from the seed data and a
   script that reports how often the scorer agrees.
5. Intros + events + feedback: intro requests with capacity limits, outcome rating,
   events logged for every recommendation shown and every action taken.
6. Neo4j: sync job from Postgres, warm-path endpoint (best mutual to introduce two
   members), switch the local funnel to use the graph.
7. Circuits: edges, cycles, solver, rounds, consent endpoints, tests with a small
   hand-built graph where the correct circuits are known.
8. Dashboard: gap map and network stats endpoints.
9. Hardening: README with setup steps for Mac and WSL, make sure all tests pass,
   OpenAPI docs are clean and every endpoint has a description.

## Definition of done (every phase)
Tests pass, logs work for new endpoints, migrations apply cleanly from scratch,
README updated, committed, and explained to me.
