"""Neo4j driver. The graph is a derived copy of Postgres relationships (Phase 6)."""

from neo4j import AsyncDriver, AsyncGraphDatabase


def create_neo4j_driver(uri: str, user: str, password: str) -> AsyncDriver:
    # Creating the driver does not connect; connections open lazily on first use.
    return AsyncGraphDatabase.driver(uri, auth=(user, password))
