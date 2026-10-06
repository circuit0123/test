# Circuit backend

Backend for Circuit, a startup ecosystem platform. It has two parts: a map where students find nearby startups that are hiring, and a matching network for founders, mentors and investors. See `CLAUDE.md` for the full build spec.

**Status:**
- Phase 0 (scaffold): done.
- Phase 1 (data model, migrations, seed data): done.
- Phase 2 (auth, members, startups, needs/offers, claiming): done.
- Phase 3 (geo search): done.
- Phase 4 (matching engine): done.
- Phase 5 (intros, events, feedback): done.

## Prerequisites

Both macOS (Apple Silicon) and Windows with WSL2 (x86_64) are supported. Every Docker image used here is published for `linux/amd64` and `linux/arm64`.

- **Docker**
  - macOS: Docker Desktop (or OrbStack).
  - WSL2: Docker Desktop with WSL integration turned on for your distro, or Docker Engine installed inside WSL.
- **uv**, the Python package and project manager: `curl -LsSf https://astral.sh/uv/install.sh | sh`. It installs Python 3.12 for you.
- **WSL2 tip:** clone the repo inside the Linux filesystem (`~/code/...`), not under `/mnt/c/...`. File access and Docker volumes are much faster there.

## Setup

```bash
cp .env.example .env          # local settings; never commit .env
uv sync                       # create .venv and install dependencies
docker compose up -d --build  # Postgres (PostGIS + pgvector), Neo4j, Redis
docker compose ps             # wait until all three show "healthy"
uv run alembic upgrade head   # create/upgrade the database tables
uv run python -m app.ingestion.seed_loader   # load seed data (downloads the embedding model once)
uv run python -m app.jobs.matching           # compute everyone's matches (the scheduler also does this)
uv run uvicorn app.main:app --reload
```

Then open:

- http://localhost:8000/health: liveness check. It confirms the process is up and touches no other service.
- http://localhost:8000/health/deps: checks Postgres (including its extensions), Neo4j and Redis. Returns 200, or 503 if any of them is down.
- http://localhost:8000/docs: interactive API docs.
- http://localhost:7474: Neo4j browser. Log in as user `neo4j` with `NEO4J_PASSWORD` from `.env`.

## Tests

```bash
uv run pytest                    # everything; live tests skip if services are down
uv run pytest -m "not integration"   # unit tests only, no Docker needed
```

Integration tests use a separate `circuit_test` database. They wipe it and rebuild it from the migrations on every run, so your development data is never touched.

## Services

| Service  | Image                                   | Role                                                   |
|----------|-----------------------------------------|--------------------------------------------------------|
| Postgres | built from `docker/postgres/Dockerfile` | Source of truth. PostGIS for geo, pgvector for embeddings |
| Neo4j    | `neo4j:5-community`                     | Derived relationship graph, rebuildable from Postgres  |
| Redis    | `redis:7-alpine`                        | Cache for match results. Safe to wipe                  |

The Postgres image is `postgres:16-bookworm` with `postgresql-16-postgis-3` and `postgresql-16-pgvector` installed from the PGDG apt repository. `docker/postgres/initdb/` runs only the first time the data volume is created. It enables both extensions and creates a separate `circuit_test` database. To start from a clean slate, run `docker compose down -v`, which deletes all local data.

## Authentication

The API accepts `Authorization: Bearer <JWT>` tokens. A token carries the claims `sub` (the member id), `role`, `verification_level`, `token_version` and `exp`.

- **Production:** tokens are checked against your identity provider's public keys at `JWKS_URL`, plus `JWT_ISSUER` and `JWT_AUDIENCE` when set. Switching between Clerk and Supabase only changes these settings.
  - A provider token's `sub` is the provider's own user id. It is matched against `members.auth_subject`, never against our member id.
  - An admin links an account with `PUT /members/{id}/auth-subject`, or sets `auth_subject` when creating the member.
- **Local development (`DEV_AUTH=true`):** `POST /auth/dev/token` issues a token for any seeded member. The app refuses to start with `DEV_AUTH=true` when `ENV=production`.

```bash
curl -X POST localhost:8000/auth/dev/token -H 'content-type: application/json' -d '{"role": "founder"}'
# or {"member_id": "<uuid>"}; then click "Authorize" on /docs and paste the access_token
```

Access rules:

| Who | Can |
|---|---|
| Level 1 (every member) | Browse members and startups. Edit their own profile, traits, needs and offers |
| Level 2 | Add a startup, claim a startup (and get matches, from Phase 4) |
| Level 3 | Request intros |
| `partner_admin` | Create members, set verification levels, review startup claims |
| Startup team / claimer | Edit the startup and its needs |

Role and level are always read fresh from the database. When an admin changes someone's level, or someone calls `POST /members/me/revoke-tokens`, their `token_version` goes up. Every token they already hold then stops working.

**Claiming a startup:**

1. A level 2+ member sends `POST /startups/{id}/claims` with evidence that they work there.
2. A `partner_admin` reviews it under `GET /startups/claims`.
3. The admin approves or rejects it. Approval makes the member the owner and adds them to the team. Any other pending claims for that startup are rejected.

## Geo search

| Endpoint | What it does |
|---|---|
| `GET /geo/startups/nearby?lat&lng&radius_km&hiring&capability&limit` | Startups nearest first, each with `distance_km`. Without `lat`/`lng`, uses your saved location. |
| `GET /geo/startups/bbox?min_lat&min_lng&max_lat&max_lng&hiring&limit` | Lightweight pins for a map viewport. Sets `truncated: true` if more than `limit` are in view. |

- **Students:** distance is a hard filter. The radius defaults to `STUDENT_DEFAULT_RADIUS_KM` (10) and is capped at `STUDENT_MAX_RADIUS_KM` (25). The response reports the radius used and whether it was capped. Other roles may leave the radius out and search everywhere.
- **The `capability` filter** finds startups with a live need for that capability. For example, a student can pass their own skill.
- **Map viewports** that cross the 180° meridian must be sent as two requests.
- **Performance:** both queries use the GIST index on `startups.location`. Nearby uses `ST_DWithin` plus `<->` (nearest-neighbour ordering). The viewport query uses `&&` followed by an exact lat/lng check.

## Matching

For each member, the engine finds candidates, scores each pair, ranks them, writes a one-line reason, and stores the results.

**1. Candidates** (`app/matching/candidates.py`) come from two sources:
- **Local funnel:** people in the same city who also share a trait or are within 2 hops in the connection graph. A recursive SQL query walks the graph.
- **Bridge search:** anyone whose offers meet your needs, or whose needs your offers meet, regardless of sector or place. It uses exact capability matches plus nearest neighbours by embedding (pgvector HNSW). It only runs between members who are both `open_to_cross_sector`.
- A founder is matched on their startup's needs too. Founders of hiring startups count as offering internships.

**2. Score** (`app/matching/scoring.py`, pure functions):

`score = w1*complementarity + w2*affinity + w3*trust_path + w4*timing - w5*load_penalty`

- Complementarity is computed in both directions ("they can help you" and "you can help them") and combined.
- Weights and thresholds live in `MatchingSettings` in `app/config.py`. Override them as `MATCHING__<NAME>` in `.env`.

**3. Rank** (`app/matching/ranking.py`):
- 25% of each member's slots are reserved for bridge matches.
- A small penalty for repeating the same capability keeps results diverse.
- At most half the results come from one role.
- Each person can appear in at most `MAX_APPEARANCES_PER_CANDIDATE` lists, so one popular mentor isn't flooded with requests.

**4. Reasons** (`app/matching/reasons.py`) are template-based, built only from the score components.

**5. Storage:** `match_results` is the source of truth. It keeps every component for later explanation and learning. Redis caches each member's list.

- `GET /matches` (level 2) reads from Redis, then falls back to `match_results`, then computes on the spot for a brand-new member. `?refresh=true` recomputes now.
- An APScheduler job recomputes everyone every `MATCHING__JOB_INTERVAL_HOURS` (default 4). An admin can trigger it with `POST /matches/recompute`.
- If you run more than one API process, set `SCHEDULER_ENABLED=false` on all but one.

**Evaluation:** `tests/eval/pairs.json` holds 49 hand-labelled good and bad pairs from the seed data. Run `uv run python -m tests.eval.run_eval` to see how often the scorer agrees. See `tests/eval/README.md`.

**Embeddings:** each need and offer records which model made its vector (`embedding_model`). If you switch models, the seed loader re-embeds automatically.

## Intros

| Endpoint | Who | What |
|---|---|---|
| `POST /intros` | level 3 | Ask to meet someone, optionally `via_member` (a mutual connection of you both) |
| `GET /intros/incoming`, `/outgoing`, `/{id}` | the people involved | Lists and details |
| `POST /intros/{id}/accept`, `/decline` | the person asked | Answer, with an optional note. Accepting creates a connection |
| `POST /intros/{id}/cancel` | the requester | Withdraw a pending request |
| `POST /intros/{id}/rating` | either person | Rate an accepted intro 1-5 (outcome feedback) |
| `GET /intros/limits` | anyone | Your current capacity |

Capacity rules (settings: `INTROS__*`):

- **Recipient inbox:** at most `open_intro_slots` requests can wait for someone at once. Set it to 0 to pause new requests.
- **Requester limits:** at most 5 requests waiting at once, and 10 sent per 7 days.
- **One pending request per pair.** No new request after an accepted intro. After a decline, wait 30 days.
- **Expiry:** unanswered requests expire after 14 days. An hourly job marks them, and expired requests are also caught on the spot.
- **No double-booking:** the last inbox slot can't be taken twice, because both members' rows are locked while the rules are checked.

Each request records which recommendation led to it (`match_source`: local or bridge) and its score. That lets the dashboard measure acceptance by match type.

## Events and the feedback loop

`events` is an append-only log of what members saw and did. It is product data, separate from the debugging logs.

- **Recommendations:** every match returned by `GET /matches` is recorded as `recommendation_shown`, with rank, score and source.
- **Write actions:** every write is recorded in the same transaction as the change itself. That covers profile, needs/offers, startups, claims, intros, ratings and admin actions. The full list is in `app/services/events.py`.
- **Frontend events:** the frontend reports what only it can see with `POST /events`: `match_clicked`, `match_dismissed`, `profile_viewed`, `startup_viewed`.
- **Privacy:** payloads hold ids, statuses, scores and the *names* of changed fields, never free text.
- **Admin access:** admins can browse the log with `GET /events`.

**Feedback into matching:** your matches skip people you already have a pending or accepted intro with. They also skip people who declined you (or whom you declined) in the last 30 days, and people you dismissed in the last 30 days. Accepted intros become connections, which raise trust scores for future matches.

Ratings, `recommendation_shown` events and the stored score components are kept so the weights can later be learned from real outcomes.

## Database and migrations

The models are in `app/db/models.py`. Migrations are in `migrations/versions/` and are managed with Alembic.

```bash
uv run alembic upgrade head                      # apply all migrations
uv run alembic revision --autogenerate -m "..."  # after changing a model: draft a migration, then review it
uv run alembic check                             # confirms the models and migrations agree
uv run alembic downgrade -1                      # undo the latest migration
```

Notes on the schema:

- Locations use PostGIS `geography` points with GIST indexes. Embeddings use pgvector `vector(384)` with HNSW indexes.
- `events` is append-only. A database trigger rejects every `UPDATE` and `DELETE`.

## Seed data

- `seed/reference/` holds the fixed lists of 60 capabilities and 25 traits.
- `seed/generate.py` builds deterministic fake data around `CITY_CENTER_LAT`/`CITY_CENTER_LNG` and writes it to `seed/data/`:
  - `startups.json` uses exactly the future crawler schema (name, website_domain, description, sector, address, lat, lng, hiring, open_roles, tech_stack, source).
  - `startup_profiles.json` holds what founders would add after claiming a profile.
  - The other files hold members, connections, 6 cross-sector "bridge" cases and 2 three-person exchange loops.
- Domains end in `.example`, a reserved name, so no real website is ever referenced.

```bash
uv run python -m seed.generate             # regenerate seed/data (commit the result)
uv run python -m app.ingestion.seed_loader # load it; safe to run repeatedly
uv run python -m app.ingestion.seed_loader --embeddings fake  # quick, offline, no model download
```

The loader is idempotent. Startups upsert on `website_domain`; members, needs and offers upsert on fixed ids. Embeddings are recomputed only for new or changed text.

## Logging

Every request produces one JSON line on stdout with `request_id`, `method`, `path`, `status`, `duration_ms` and `user_id`. The same id comes back in the `X-Request-ID` response header. Unhandled errors are logged with a stack trace and that request id. Query strings, headers and bodies are never logged.

## Layout

```
app/
  main.py        app factory, middleware, routers, startup/shutdown
  config.py      settings from environment / .env
  logging.py     structlog JSON setup + request middleware
  resources.py   shared connections (DB engine, Redis, Neo4j)
  api/           HTTP routers (thin)
  services/      business logic
  schemas/       Pydantic request/response models
  db/            engine, sessions, ORM models, Redis client
  embeddings/    EmbeddingProvider interface, fastembed + fake implementations
  ingestion/     seed file schemas + idempotent loader
  api/deps.py    auth dependencies: get_current_member, require_level, require_role
  matching/      scoring (pure), candidates (SQL), ranking, reasons
  jobs/          APScheduler setup, match job (every 4h), intro expiry (hourly)
  graph/ circuits/   (later phases)
migrations/      Alembic migrations
seed/            reference lists, generator, generated data
docker/postgres/ Postgres image + first-run SQL
tests/
```
