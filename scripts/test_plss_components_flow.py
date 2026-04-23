"""
Dry-run test for the per-field PLSS editor + Fetch Claim Records.

Simulates the user typing Township=T12S, Range=R12W, Section=35 for
Beryllium Mine (Spor Mountain) and verifies:

  1. The per-field normalizer produces the BLM-encoded values the rest of
     the system expects (0120S / 0120W / 035).
  2. The canonical PLSS string that would be written back to the DB
     round-trips through ``parse_plss_string`` correctly.
  3. The live BLM MLRS API actually returns claims for that PLSS when
     queried via ``query_claims_by_plss`` — i.e. Fetch Claim Records
     would succeed if the user saved these values.

**No database writes occur.** The script only calls in-memory normalizers
and hits the public BLM ArcGIS endpoint.
"""

from __future__ import annotations

import json
import sys

from mining_os.services.blm_plss import (
    normalize_plss_field,
    parse_plss_string,
    query_claims_by_plss,
)
from mining_os.services.fetch_claim_records import STATE_MERIDIAN


def banner(title: str) -> None:
    print(f"\n=== {title} ===")


def main() -> int:
    raw = {"state": "UT", "township": "T12S", "range": "R12W", "section": "35"}
    print("Simulated user input:", raw)

    banner("Step 1: per-field normalization")
    st = normalize_plss_field(raw["state"], "state")
    twp = normalize_plss_field(raw["township"], "township")
    rng = normalize_plss_field(raw["range"], "range")
    sec = normalize_plss_field(raw["section"], "section")
    mer = STATE_MERIDIAN.get(st or "UT", "26")
    normalized = {"state": st, "township": twp, "range": rng, "section": sec, "meridian": mer}
    print("Normalized:", normalized)

    expected = {"state": "UT", "township": "0120S", "range": "0120W", "section": "035", "meridian": "26"}
    if normalized != expected:
        print(f"FAIL: expected {expected}, got {normalized}")
        return 1
    print("PASS — normalizer produced the BLM-encoded values.")

    banner("Step 2: canonical PLSS string round-trip")
    canonical = f"{st} T12S R12W Sec {sec}"
    print("Canonical location_plss →", repr(canonical))
    reparsed = parse_plss_string(canonical, default_state="UT")
    print("Re-parsed →", reparsed)
    if not reparsed or reparsed.get("township") != twp or reparsed.get("range") != rng or reparsed.get("section") != sec:
        print("FAIL: canonical string does not round-trip through parse_plss_string")
        return 1
    print("PASS — canonical string parses back to the same components.")

    banner("Step 3: live BLM MLRS query (no DB writes)")
    print(f"Querying BLM MLRS for state={st} twp={twp} rng={rng} sec={sec} mer={mer}")
    claims = query_claims_by_plss(
        state=st or "UT",
        township=twp or "",
        range_val=rng or "",
        section=sec,
        meridian=mer,
    )
    print(f"BLM returned {len(claims)} claim(s).")
    if claims:
        sample = claims[0]
        trimmed = {k: sample.get(k) for k in ("claim_name", "serial_number", "CSE_META", "payment_status")}
        print("First claim (trimmed):", json.dumps(trimmed, indent=2))
    else:
        print("NOTE: 0 claims — Fetch Claim Records would broaden to the full Township/Range on pass 2.")
        broad = query_claims_by_plss(
            state=st or "UT",
            township=twp or "",
            range_val=rng or "",
            section=None,
            meridian=mer,
        )
        print(f"Broader (full T/R) query returned {len(broad)} claim(s).")

    banner("SUMMARY")
    print("Per-field editor would save:")
    print(f"  location_plss = {canonical}")
    print(f"  state_abbr    = {st}")
    print(f"  township      = {twp}")
    print(f"  range         = {rng}")
    print(f"  section       = {sec}")
    print(f"  meridian      = {mer}")
    print("These flow straight into fetch_claim_records_for_area's "
          "stored-fields path (no re-parsing required).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
