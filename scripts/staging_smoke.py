#!/usr/bin/env python3
"""Production-isolated staging smoke for Active Mine Search (T-041).

Runs without contacting production hosts. Exits non-zero if staging isolation
fails or evidence-model tests fail.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    os.environ.setdefault("MINING_OS_ENVIRONMENT", "staging")
    # Guarantee we are not accidentally using a production DSN in this process.
    from mining_os.active_mine_intel.staging import (
        looks_like_production_url,
        staging_isolation_report,
    )

    db = os.getenv("DATABASE_URL", "")
    origin = os.getenv("API_ORIGIN", "")
    if looks_like_production_url(db) or looks_like_production_url(origin):
        print("FAIL: process is wired to a production host", file=sys.stderr)
        return 2

    report = staging_isolation_report()
    print("staging_isolation_report:", report)
    if report["staging"] and not report["ok"]:
        print("FAIL: staging isolation violations", report["violations"], file=sys.stderr)
        return 2

    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "tests/test_active_mine_evidence.py",
        "tests/test_active_mine_intel.py",
        "--tb=short",
    ]
    print("running", " ".join(cmd))
    proc = subprocess.run(cmd, cwd=ROOT)
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
