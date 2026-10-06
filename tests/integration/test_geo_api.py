"""Geo endpoints against real PostGIS, with startups placed at known distances."""

import math
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import insert, select, text

from app.db import models as m
from app.db.geo import to_point

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

CENTER = (12.9716, 77.5946)


def offset(km: float, bearing_deg: float) -> tuple[float, float]:
    """A point `km` away from CENTER in a compass direction (0 = north, 90 = east)."""
    lat0, lng0 = CENTER
    b = math.radians(bearing_deg)
    return (lat0 + km * math.cos(b) / 110.574,
            lng0 + km * math.sin(b) / (111.320 * math.cos(math.radians(lat0))))


# name -> (km, bearing, hiring)
LAYOUT = {
    "A": (1, 0, True),
    "B": (3, 90, False),
    "C": (8, 180, True),
    "D": (15, 270, True),
    "E": (40, 0, True),
}


@pytest.fixture
async def world(factory, engine):
    ids = {}
    for name, (km, bearing, hiring) in LAYOUT.items():
        lat, lng = offset(km, bearing)
        ids[name] = await factory.startup(f"{name.lower()}.example", name=name, hiring=hiring,
                                          location=to_point(lat, lng))
    ids["NOWHERE"] = await factory.startup("nowhere.example", name="NOWHERE", location=None)
    async with engine.begin() as conn:
        cap = await conn.scalar(select(m.Capability.id).where(m.Capability.slug == "frontend-development"))
        await conn.execute(insert(m.Need).values([
            {"id": uuid.uuid4(), "owner_type": "startup", "owner_id": ids["A"], "capability_id": cap,
             "text": "Need a frontend intern", "expires_at": None},
            {"id": uuid.uuid4(), "owner_type": "startup", "owner_id": ids["B"], "capability_id": cap,
             "text": "Expired need", "expires_at": datetime.now(UTC) - timedelta(days=1)},
        ]))
    return ids


async def nearby(api, headers, **params):
    r = await api.get("/geo/startups/nearby", headers=headers, params=params)
    assert r.status_code == 200, r.text
    return r.json()


def names(body) -> list[str]:
    return [i["name"] for i in body["items"]]


async def test_nearest_first_with_accurate_distances(api, factory, world):
    h = await factory.headers(await factory.member("founder"))
    body = await nearby(api, h, lat=CENTER[0], lng=CENTER[1])
    assert names(body) == ["A", "B", "C", "D", "E"]  # NOWHERE (no location) is excluded
    for item in body["items"]:
        expected = LAYOUT[item["name"]][0]
        assert item["distance_km"] == pytest.approx(expected, rel=0.01)
    assert body["radius_km"] is None and body["radius_capped"] is False


async def test_filters(api, factory, world):
    h = await factory.headers(await factory.member("founder"))
    c = {"lat": CENTER[0], "lng": CENTER[1]}
    assert names(await nearby(api, h, **c, radius_km=5)) == ["A", "B"]
    assert names(await nearby(api, h, **c, hiring="true")) == ["A", "C", "D", "E"]
    assert names(await nearby(api, h, **c, hiring="false")) == ["B"]
    # B's frontend need has expired, so only A matches.
    assert names(await nearby(api, h, **c, capability="frontend-development")) == ["A"]
    assert names(await nearby(api, h, **c, limit=2)) == ["A", "B"]


async def test_students_always_get_a_radius(api, factory, world):
    h = await factory.headers(await factory.member("student", level=1))
    c = {"lat": CENTER[0], "lng": CENTER[1]}
    body = await nearby(api, h, **c)
    assert names(body) == ["A", "B", "C"] and body["radius_km"] == 10
    body = await nearby(api, h, **c, radius_km=100)
    assert names(body) == ["A", "B", "C", "D"] and body["radius_km"] == 25 and body["radius_capped"] is True
    assert names(await nearby(api, h, **c, radius_km=2)) == ["A"]


async def test_center_defaults_to_saved_location(api, factory, world):
    located = await factory.headers(await factory.member("founder", lat=CENTER[0], lng=CENTER[1]))
    body = await nearby(api, located)
    assert body["center"] == {"lat": CENTER[0], "lng": CENTER[1]} and names(body)[0] == "A"

    unlocated = await factory.headers(await factory.member("founder"))
    r = await api.get("/geo/startups/nearby", headers=unlocated)
    assert r.status_code == 422 and "save your location" in r.json()["detail"]
    r = await api.get("/geo/startups/nearby", headers=located, params={"lat": 12.9})
    assert r.status_code == 422


async def test_geo_requires_login(api, world):
    assert (await api.get("/geo/startups/nearby", params={"lat": 1, "lng": 1})).status_code == 401
    assert (await api.get("/geo/startups/bbox", params={"min_lat": 0, "min_lng": 0, "max_lat": 1, "max_lng": 1})).status_code == 401


def bbox_around(half_deg: float) -> dict:
    return {"min_lat": CENTER[0] - half_deg, "min_lng": CENTER[1] - half_deg,
            "max_lat": CENTER[0] + half_deg, "max_lng": CENTER[1] + half_deg}


async def test_bbox_returns_lightweight_records_inside_the_box(api, factory, world):
    h = await factory.headers(await factory.member("student", level=1))
    r = await api.get("/geo/startups/bbox", headers=h, params=bbox_around(0.05))  # about +-5.5 km
    body = r.json()
    assert sorted(names(body)) == ["A", "B"]
    assert body["count"] == 2 and body["truncated"] is False
    assert set(body["items"][0]) == {"id", "name", "sector", "hiring", "lat", "lng"}

    r = await api.get("/geo/startups/bbox", headers=h, params={**bbox_around(0.2), "hiring": "true"})
    assert sorted(names(r.json())) == ["A", "C", "D"]


async def test_bbox_truncates_keeping_pins_nearest_the_centre(api, factory, world):
    h = await factory.headers(await factory.member("student", level=1))
    body = (await api.get("/geo/startups/bbox", headers=h, params={**bbox_around(0.5), "limit": 2})).json()
    assert names(body) == ["A", "B"] and body["truncated"] is True and body["count"] == 2


@pytest.mark.parametrize("params", [
    {"min_lat": 13, "min_lng": 77, "max_lat": 12, "max_lng": 78},      # inverted latitudes
    {"min_lat": 12, "min_lng": 179, "max_lat": 13, "max_lng": -179},   # crosses the 180° meridian
    {"min_lat": 12, "min_lng": 77, "max_lat": 95, "max_lng": 78},      # out of range
])
async def test_bbox_validation(api, factory, params):
    h = await factory.headers(await factory.member("student", level=1))
    assert (await api.get("/geo/startups/bbox", headers=h, params=params)).status_code == 422


async def test_spatial_index_is_usable(engine, world):
    """With sequential scans discouraged, both query shapes use the GIST index."""
    point = f"ST_SetSRID(ST_MakePoint({CENTER[1]}, {CENTER[0]}), 4326)::geography"
    queries = [
        f"SELECT id FROM startups WHERE ST_DWithin(location, {point}, 5000) ORDER BY location <-> {point} LIMIT 10",
        "SELECT id FROM startups WHERE location && ST_MakeEnvelope(77.5, 12.9, 77.7, 13.0, 4326)::geography",
    ]
    async with engine.connect() as conn:
        await conn.execute(text("SET enable_seqscan = off"))
        for q in queries:
            plan = "\n".join((await conn.execute(text(f"EXPLAIN {q}"))).scalars())
            assert "ix_startups_location" in plan, plan
