"""The seed generator: shape, realism and determinism. No database needed."""

import json
import math
from collections import Counter
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from app.ingestion import schemas as s
from seed.generate import generate

CENTER = (12.9716, 77.5946)
DATA_DIR = Path(__file__).resolve().parents[1] / "seed" / "data"
REF_DIR = Path(__file__).resolve().parents[1] / "seed" / "reference"
CRAWLER_FIELDS = {"name", "website_domain", "description", "sector", "address", "lat", "lng",
                  "hiring", "open_roles", "tech_stack", "source"}


@pytest.fixture(scope="module")
def data():
    return generate(42, *CENTER, "Bengaluru")


def _km(lat1, lng1, lat2, lng2):
    # Haversine distance: good enough for a test.
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lng2 - lng1) * p / 2) ** 2)
    return 12742 * math.asin(math.sqrt(a))


def test_is_deterministic(data):
    assert generate(42, *CENTER, "Bengaluru") == data
    assert generate(7, *CENTER, "Bengaluru") != data


def test_committed_seed_files_match_generator(data):
    """seed/data must be regenerated whenever the generator changes."""
    for filename, records in data.items():
        assert json.loads((DATA_DIR / filename).read_text()) == records, f"regenerate {filename}"


def test_reference_lists_have_expected_sizes():
    caps = json.loads((REF_DIR / "capabilities.json").read_text())
    traits = json.loads((REF_DIR / "traits.json").read_text())
    assert 40 <= len(caps) <= 80 and 20 <= len(traits) <= 30
    assert len({c["slug"] for c in caps}) == len(caps)


def test_sizes_and_role_mix(data):
    assert 145 <= len(data["startups.json"]) <= 160
    roles = Counter(m["role"] for m in data["members.json"])
    assert 290 <= sum(roles.values()) <= 330
    assert roles.most_common(1)[0][0] == "student"
    assert set(roles) == {"student", "founder", "mentor", "investor", "partner_admin"}


def test_startups_match_crawler_schema_exactly(data):
    for st in data["startups.json"]:
        assert set(st) == CRAWLER_FIELDS
    TypeAdapter(list[s.CrawlerStartup]).validate_python(data["startups.json"])
    domains = [st["website_domain"] for st in data["startups.json"]]
    assert len(set(domains)) == len(domains)
    assert all(d.endswith(".example") for d in domains)  # never a real website


def test_all_files_validate_and_reference_known_slugs(data):
    caps = {c["slug"] for c in json.loads((REF_DIR / "capabilities.json").read_text())}
    traits = {t["slug"] for t in json.loads((REF_DIR / "traits.json").read_text())}
    members = TypeAdapter(list[s.SeedMember]).validate_python(data["members.json"])
    profiles = TypeAdapter(list[s.StartupProfile]).validate_python(data["startup_profiles.json"])
    TypeAdapter(list[s.SeedConnection]).validate_python(data["connections.json"])
    for mb in members:
        assert {n.capability for n in mb.needs + mb.offers} <= caps
        assert set(mb.traits) <= traits
    for p in profiles:
        assert {n.capability for n in p.needs} <= caps
        assert set(p.traits) <= traits
    member_ids = {mb.id for mb in members}
    assert len(member_ids) == len(members)
    assert all(tm.member_id in member_ids for p in profiles for tm in p.team)
    need_ids = [n.id for mb in members for n in mb.needs] + [n.id for p in profiles for n in p.needs]
    assert len(set(need_ids)) == len(need_ids)


def test_everything_is_near_the_city_centre(data):
    for rec in data["startups.json"] + data["members.json"]:
        if rec["lat"] is not None:
            assert _km(*CENTER, rec["lat"], rec["lng"]) < 25


def test_connections_are_canonical_and_reference_members(data):
    ids = {m["id"] for m in data["members.json"]}
    pairs = set()
    for c in data["connections.json"]:
        assert c["a"] in ids and c["b"] in ids and c["a"] != c["b"]
        pair = frozenset((c["a"], c["b"]))
        assert pair not in pairs
        pairs.add(pair)


def test_bridge_cases_are_cross_sector_and_resolvable(data):
    cases = data["bridge_cases.json"]
    assert len(cases) >= 4
    members = {m["id"]: m for m in data["members.json"]}
    profiles = {p["website_domain"]: p for p in data["startup_profiles.json"]}
    for case in cases:
        assert case["startup_sector"] != case["helper_sector"]
        need = next(n for n in profiles[case["startup_domain"]]["needs"] if n["id"] == case["need_id"])
        offer = next(o for o in members[case["helper_id"]]["offers"] if o["id"] == case["offer_id"])
        assert need["capability"] == offer["capability"] == case["capability"]
    assert any(c["startup_sector"] == "biotech" and c["capability"] == "marketing-strategy" for c in cases)


def test_circuit_cases_form_closed_loops(data):
    members = {m["id"]: m for m in data["members.json"]}
    for case in data["circuit_cases.json"]:
        legs = case["legs"]
        assert len(legs) == 3
        assert [leg["receiver_id"] for leg in legs] == case["members"][1:] + case["members"][:1]
        for leg in legs:
            assert members[leg["giver_id"]]["circuits_opt_in"]
            assert leg["capability"] in {o["capability"] for o in members[leg["giver_id"]]["offers"]}
            assert leg["capability"] in {n["capability"] for n in members[leg["receiver_id"]]["needs"]}
