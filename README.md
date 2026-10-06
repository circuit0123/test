# Circuit backend

Backend for Circuit, a startup ecosystem platform. It has two parts: a map where students find nearby startups that are hiring, and a matching network for founders, mentors and investors. See `CLAUDE.md` for the full build spec.

**Status:** Phase 0 (scaffold) is done: the FastAPI app, config, JSON logging, the docker compose services and health checks.

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

## Services

| Service  | Image                                   | Role                                                   |
|----------|-----------------------------------------|--------------------------------------------------------|
| Postgres | built from `docker/postgres/Dockerfile` | Source of truth. PostGIS for geo, pgvector for embeddings |
| Neo4j    | `neo4j:5-community`                     | Derived relationship graph, rebuildable from Postgres  |
| Redis    | `redis:7-alpine`                        | Cache for match results. Safe to wipe                  |

The Postgres image is `postgres:16-bookworm` with `postgresql-16-postgis-3` and `postgresql-16-pgvector` installed from the PGDG apt repository. `docker/postgres/initdb/` runs only the first time the data volume is created. It enables both extensions and creates a separate `circuit_test` database. To start from a clean slate, run `docker compose down -v`, which deletes all local data.

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
  db/ graph/ matching/ circuits/ jobs/ embeddings/ ingestion/
docker/postgres/ Postgres image + first-run SQL
tests/
```
