#!/usr/bin/env python3
"""Fail CI if production and staging configs are cross-wired.

Production mergeable files must not reference trycloudflare/ngrok.
Staging files must not reference miningos.onrender.com.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mining_os.active_mine_intel.staging import (  # noqa: E402
    scan_production_config_files,
    scan_staging_config_files,
)


def main() -> int:
    prod = scan_production_config_files(ROOT)
    stage = scan_staging_config_files(ROOT)
    print("production_config:", prod)
    print("staging_config:", stage)
    if not prod["ok"] or not stage["ok"]:
        print("FAIL: environment wiring", file=sys.stderr)
        return 2
    print("PASS: production and staging configs are isolated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
