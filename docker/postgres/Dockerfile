# Postgres 16 + PostGIS 3 + pgvector, built from the official multi-arch image so it
# runs natively on both x86_64 (WSL2) and ARM64 (Apple Silicon).
#
# The official postgres:16 image already ships with the PGDG apt repository
# (apt.postgresql.org) configured, and PGDG publishes these packages for both
# amd64 and arm64, so a plain apt-get install works on either architecture.
FROM postgres:16-bookworm

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      "postgresql-${PG_MAJOR}-postgis-3" \
      "postgresql-${PG_MAJOR}-postgis-3-scripts" \
      "postgresql-${PG_MAJOR}-pgvector" \
 && rm -rf /var/lib/apt/lists/*

# Scripts in this folder run once, when the data volume is first initialised.
COPY initdb/ /docker-entrypoint-initdb.d/
