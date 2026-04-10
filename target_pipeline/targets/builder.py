"""Group normalized records into targets by (PLSS, commodity)."""

from __future__ import annotations

from typing import Optional, Tuple

from target_pipeline.models import StandardRecord, TargetGroup


def _claim_id(rec: StandardRecord) -> Optional[str]:
    raw = rec.get("raw") or {}
    props = raw.get("properties") if isinstance(raw.get("properties"), dict) else {}
    for key in ("serial_num", "serial", "claim_id", "objectid", "id"):
        v = props.get(key) if props else None
        if v is None and isinstance(raw, dict):
            v = raw.get(key)
        if v is not None and str(v).strip():
            return str(v).strip()
    name = rec.get("normalized_name") or rec.get("raw_name")
    return str(name).strip() if name else None


def _report_links(rec: StandardRecord) -> list[str]:
    reports = rec.get("reports") or []
    return [str(r).strip() for r in reports if r and str(r).strip()]


def build_targets(records: list[StandardRecord]) -> list[TargetGroup]:
    """
    Each group key is (plss_normalized or plss display, commodity).
    Records without commodity are skipped (review upstream).
    Records without plss key still group under (None, commodity) — scorer/writer may skip.
    """
    buckets: dict[Tuple[Optional[str], Optional[str]], TargetGroup] = {}

    for rec in records:
        comm = rec.get("commodity")
        pnorm = rec.get("plss_normalized") or rec.get("plss")
        key = (pnorm, comm)
        if key not in buckets:
            buckets[key] = TargetGroup(
                plss=rec.get("plss") or "",
                plss_normalized=rec.get("plss_normalized"),
                commodity=comm,
                state=rec.get("state"),
                county=rec.get("county"),
                deposits=[],
                claims=[],
            )
        g = buckets[key]
        if rec.get("record_type") == "claim":
            g["claims"].append(rec)
        else:
            g["deposits"].append(rec)
        if rec.get("state") and not g.get("state"):
            g["state"] = rec.get("state")
        if rec.get("county") and not g.get("county"):
            g["county"] = rec.get("county")
        if rec.get("plss") and not g.get("plss"):
            g["plss"] = rec["plss"]

    out: list[TargetGroup] = []
    for g in buckets.values():
        deposits = g.get("deposits") or []
        claims = g.get("claims") or []
        names = []
        for d in deposits:
            n = d.get("normalized_name") or d.get("raw_name")
            if n and str(n).strip():
                names.append(str(n).strip())
        claim_ids = []
        for c in claims:
            cid = _claim_id(c)
            if cid and cid not in claim_ids:
                claim_ids.append(cid)
        links: list[str] = []
        for d in deposits:
            for u in _report_links(d):
                if u not in links:
                    links.append(u)
        plss_disp = (g.get("plss") or "").strip() or (g.get("plss_normalized") or "")
        comm = g.get("commodity") or "Unknown"
        county = g.get("county")
        if county:
            target_name = f"{comm} Target {plss_disp} - {county} County"
        else:
            target_name = f"{comm} Target {plss_disp}"
        g["target_name"] = target_name[:500]
        g["deposit_names"] = list(dict.fromkeys(names))
        g["claim_ids"] = claim_ids
        g["report_links"] = links
        g["source_count"] = len(deposits) + len(claims)
        out.append(g)

    return sorted(out, key=lambda t: (t.get("plss_normalized") or "", t.get("commodity") or ""))
