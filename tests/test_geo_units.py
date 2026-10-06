"""Pure geo helpers: point encoding and the student radius rule."""

import struct

import pytest

from app.config import Settings
from app.db.geo import from_point, to_point
from app.services.geo import decide_radius

S = Settings(_env_file=None)


def test_to_point_puts_longitude_first():
    assert to_point(12.97, 77.59) == "SRID=4326;POINT(77.59 12.97)"
    assert to_point(None, 77.59) is None


@pytest.mark.parametrize("endian,flag", [("<", 1), (">", 0)])
def test_from_point_decodes_ewkb_in_both_byte_orders(endian, flag):
    ewkb = bytes([flag]) + struct.pack(endian + "I", 0x20000001) + struct.pack(endian + "I", 4326) \
        + struct.pack(endian + "dd", 77.59, 12.97)
    assert from_point(ewkb) == (12.97, 77.59)
    assert from_point(ewkb.hex()) == (12.97, 77.59)  # psycopg may hand us hex text
    assert from_point(None) is None


@pytest.mark.parametrize(
    ("role", "requested", "expected", "capped"),
    [
        ("student", None, 10.0, False),   # default applied: distance is a hard filter
        ("student", 5.0, 5.0, False),
        ("student", 100.0, 25.0, True),   # capped at the student maximum
        ("founder", None, None, False),   # others may search everywhere
        ("investor", 100.0, 100.0, False),
    ],
)
def test_decide_radius(role, requested, expected, capped):
    d = decide_radius(role, requested, S)
    assert (d.radius_km, d.capped) == (expected, capped)
