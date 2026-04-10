#!/usr/bin/env python3
"""
Verify CSV import stores State, Township, Range, Section correctly.
Creates a test CSV, imports it, checks DB, then cleans up.
"""
import csv
import io
import os
import sys

# Add project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mining_os.services.areas_of_focus import (
    preview_csv_import,
    apply_csv_import,
    get_area,
    delete_area,
)
from mining_os.db import get_engine
from sqlalchemy import text


def main():
    # Test CSV with Name, State, PLSS - mimics user format
    content = """Name,State,PLSS
Boulder Tungsten Mine,UT,T12S R18E Sec 21
Baker Tungsten Mine,ID,T7N R23E Sec 35
Wheeler Peak,NV,T12N R67E Sec 5"""
    print("Test CSV:")
    print(content)
    print()

    preview = preview_csv_import(content)
    if preview.get("errors"):
        print("PREVIEW ERRORS:", preview["errors"])
        return 1
    valid = preview["valid_rows"]
    print(f"Valid rows: {len(valid)}")
    for r in valid:
        print(f"  - {r.get('name')}: state={r.get('state_abbr')}, township={r.get('township')}, range_val={r.get('range_val')}, section={r.get('section')}")
    if not valid:
        print("No valid rows!")
        return 1

    result = apply_csv_import(valid, "use_new")
    if result.get("errors"):
        print("APPLY ERRORS:", result["errors"])
        return 1
    print(f"Applied: {result.get('applied', 0)}, Merged: {result.get('merged', 0)}")
    print()

    # Check DB - only our 3 test targets
    test_names = {"Boulder Tungsten Mine", "Baker Tungsten Mine", "Wheeler Peak"}
    eng = get_engine()
    with eng.begin() as conn:
        rows = conn.execute(
            text("""
            SELECT id, name, state_abbr, township, "range", section, location_plss
            FROM areas_of_focus
            WHERE name IN :names
            ORDER BY name
            """),
            {"names": tuple(test_names)},
        ).mappings().all()
    print("DB check:")
    ok = True
    for r in rows:
        s = r.get("state_abbr") or ""
        t = r.get("township") or ""
        rg = r.get("range") or ""
        sec = r.get("section") or ""
        if not s or not t or not rg:
            ok = False
            print(f"  FAIL {r['name']}: state={s!r}, township={t!r}, range={rg!r}, section={sec!r}")
        else:
            print(f"  OK {r['name']}: state={s}, township={t}, range={rg}, section={sec}")
    # Cleanup - only delete our test rows
    for r in rows:
        delete_area(r["id"])
    print(f"\nDeleted {len(rows)} test rows.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
