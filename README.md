# Circuit backend

Backend for Circuit, a startup ecosystem platform. It has two parts: a map where students find nearby startups that are hiring, and a matching network for founders, mentors and investors. See `CLAUDE.md` for the full build spec.

**Status:**
- Phase 0 (scaffold): done.
- Phase 1 (data model, migrations, seed data): done.

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
  graph/ matching/ circuits/ jobs/   (later phases)
migrations/      Alembic migrations
seed/            reference lists, generator, generated data
docker/postgres/ Postgres image + first-run SQL
tests/
```
