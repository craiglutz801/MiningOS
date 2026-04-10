"""Optional PLSS lookup when coordinates exist but PLSS text is missing."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, List, Optional, Protocol, Union

log = logging.getLogger("target_pipeline.spatial")


class _PointProtocol(Protocol):
    def contains(self, x: float, y: float) -> bool: ...


def load_plss_lookup_geojson(path: Union[str, Path]) -> List[dict[str, Any]]:
    """Load GeoJSON features; each should have properties.plss or properties.trs."""
    p = Path(path)
    if not p.is_file():
        log.warning("PLSS lookup file not found: %s", p)
        return []
    data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
    return list(data.get("features") or [])


def _point_in_ring(lon: float, lat: float, ring: list[list[float]]) -> bool:
    # Ray casting
    inside = False
    n = len(ring)
    if n < 3:
        return False
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        intersect = (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi + 1e-12) + xi
        if intersect:
            inside = not inside
        j = i
    return inside


def _point_in_polygon(lon: float, lat: float, coords: Any) -> bool:
    if not isinstance(coords, list) or not coords:
        return False
    # Polygon: first ring is exterior
    if isinstance(coords[0][0], (int, float)):
        return _point_in_ring(lon, lat, coords)  # type: ignore[arg-type]
    for ring in coords:
        if isinstance(ring, list) and ring and isinstance(ring[0], list):
            if _point_in_ring(lon, lat, ring):
                return True
    return False


def lookup_plss_from_point(
    lat: float,
    lon: float,
    features: Optional[List[dict[str, Any]]],
) -> Optional[str]:
    """
    Return PLSS string from first polygon feature containing (lon, lat).
    Expects GeoJSON-like features with geometry.type Polygon/MultiPolygon.
    """
    if not features:
        return None
    for feat in features:
        geom = feat.get("geometry") or {}
        if not isinstance(geom, dict):
            continue
        gtype = geom.get("type")
        coords = geom.get("coordinates")
        hit = False
        if gtype == "Polygon" and coords:
            hit = _point_in_polygon(lon, lat, coords)
        elif gtype == "MultiPolygon" and coords:
            for poly in coords:
                if _point_in_polygon(lon, lat, poly):
                    hit = True
                    break
        if not hit:
            continue
        props = feat.get("properties") or {}
        plss = props.get("plss") or props.get("trs") or props.get("location_plss")
        if plss:
            return str(plss).strip()
    return None
