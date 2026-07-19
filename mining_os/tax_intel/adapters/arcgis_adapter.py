"""ArcGIS FeatureServer / MapServer listing adapter (lightweight requests, no GeoPandas)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterable

import requests

from mining_os.tax_intel.adapters.base import RawTaxRecord, SourceArtifact, TaxSourceAdapter
from mining_os.tax_intel.adapters.csv_adapter import USER_AGENT, _float, _parse_date


class ArcgisFeatureServerAdapter(TaxSourceAdapter):
    def discover(self) -> list[str]:
        layer = self.config.get("layer_url") or self.config.get("listing_url") or self.source.get("listing_url")
        return [str(layer)] if layer else []

    def fetch(self, url: str) -> SourceArtifact:
        now = datetime.now(timezone.utc)
        where = str(self.config.get("where") or "1=1")
        out_fields = str(self.config.get("out_fields") or "*")
        max_records = int(self.config.get("max_records") or 500)
        params = {
            "where": where,
            "outFields": out_fields,
            "returnGeometry": "true",
            "outSR": "4326",
            "f": "geojson",
            "resultRecordCount": max_records,
        }
        r = requests.get(f"{url.rstrip('/')}/query", params=params, timeout=60, headers={"User-Agent": USER_AGENT})
        r.raise_for_status()
        return SourceArtifact(
            source_url=str(r.url),
            retrieved_at=now,
            media_type="application/geo+json",
            content=r.content,
            filename="layer.geojson",
            metadata={"where": where, "max_records": max_records},
        )

    def parse(self, artifact: SourceArtifact) -> Iterable[RawTaxRecord]:
        data = json.loads(artifact.content.decode("utf-8"))
        features = data.get("features") or []
        field_map: dict[str, str] = dict(self.config.get("field_map") or {})
        state = str(self.source.get("state") or "")
        county = str(self.source.get("county_name") or "")

        def prop(attrs: dict[str, Any], *names: str) -> Any:
            for name in names:
                key = field_map.get(name, name)
                if key in attrs and attrs[key] not in (None, ""):
                    return attrs[key]
                for k, v in attrs.items():
                    if k.lower() == key.lower() and v not in (None, ""):
                        return v
            return None

        for i, feat in enumerate(features):
            attrs = feat.get("properties") or feat.get("attributes") or {}
            geom = feat.get("geometry") or {}
            lat = lon = None
            if geom.get("type") == "Point" and isinstance(geom.get("coordinates"), list):
                lon, lat = geom["coordinates"][:2]
            elif geom.get("type") in ("Polygon", "MultiPolygon"):
                # centroid approx from first ring
                try:
                    ring = (
                        geom["coordinates"][0]
                        if geom["type"] == "Polygon"
                        else geom["coordinates"][0][0]
                    )
                    xs = [p[0] for p in ring]
                    ys = [p[1] for p in ring]
                    lon = sum(xs) / len(xs)
                    lat = sum(ys) / len(ys)
                except Exception:
                    pass
            apn = prop(attrs, "apn", "parcel", "parcel_id", "APN", "PIN")
            key = str(prop(attrs, "objectid", "id", "fid") or apn or f"feat-{i+1}")
            yield RawTaxRecord(
                source_record_key=key,
                state=state,
                county_name=county,
                apn_raw=str(apn) if apn is not None else None,
                owner_raw=str(prop(attrs, "owner", "owner_name") or "") or None,
                legal_description_raw=str(prop(attrs, "legal", "legal_description") or "") or None,
                raw_status=str(prop(attrs, "status", "sale_status") or "") or None,
                amount_due=_float(str(prop(attrs, "amount_due", "taxes_due") or "") or None),
                minimum_bid=_float(str(prop(attrs, "minimum_bid", "min_bid") or "") or None),
                sale_date=_parse_date(str(prop(attrs, "sale_date", "auction_date") or "") or None),
                property_address=str(prop(attrs, "address", "situs") or "") or None,
                acreage=_float(str(prop(attrs, "acreage", "acres") or "") or None),
                latitude=float(lat) if lat is not None else _float(str(prop(attrs, "latitude", "lat") or "") or None),
                longitude=float(lon) if lon is not None else _float(str(prop(attrs, "longitude", "lon") or "") or None),
                best_name=str(prop(attrs, "name", "claim_name") or apn or key),
                raw_payload={"properties": attrs, "geometry_type": geom.get("type")},
            )
