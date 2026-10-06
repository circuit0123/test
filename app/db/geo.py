"""Converting between (lat, lng) and PostGIS point values.

Writing: we send "extended well-known text" (EWKT), e.g. 'SRID=4326;POINT(77.59 12.97)'.
Reading: PostGIS returns "extended well-known binary" (EWKB); we decode the two
numbers ourselves rather than pulling in a geometry library just for points.
Note: PostGIS puts longitude (x) first, but our API always says lat, lng.
"""

import struct
from typing import Any

_SRID_FLAG = 0x20000000


def to_point(lat: float | None, lng: float | None) -> str | None:
    if lat is None or lng is None:
        return None
    return f"SRID=4326;POINT({lng} {lat})"


def from_point(value: Any) -> tuple[float, float] | None:
    """Decode a PostGIS point (WKBElement, bytes or hex string) into (lat, lng)."""
    if value is None:
        return None
    data = getattr(value, "data", value)
    if isinstance(data, str):
        data = bytes.fromhex(data)
    data = bytes(data)
    endian = "<" if data[0] == 1 else ">"
    (geom_type,) = struct.unpack(endian + "I", data[1:5])
    offset = 9 if geom_type & _SRID_FLAG else 5
    lng, lat = struct.unpack(endian + "dd", data[offset : offset + 16])
    return lat, lng
