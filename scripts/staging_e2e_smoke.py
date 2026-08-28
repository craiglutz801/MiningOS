#!/usr/bin/env python3
"""Live staging E2E smoke for Active Mine Search (T-041).

Hits a running staging API. Never contacts miningos.onrender.com or a
production database. Exit non-zero on isolation or contract failures.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PRODUCTION_MARKERS = ("miningos.onrender.com", "oregon-postgres.render.com")


def _fail(msg: str, code: int = 2) -> int:
    print(f"FAIL: {msg}", file=sys.stderr)
    return code


def _get(url: str, cookie: str | None = None, timeout: int = 30) -> tuple[int, dict | str]:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    if cookie:
        req.add_header("Cookie", cookie)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, raw


def _post(url: str, body: dict, cookie: str | None = None, timeout: int = 30) -> tuple[int, dict | str, str | None]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    if cookie:
        req.add_header("Cookie", cookie)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            set_cookie = resp.headers.get("Set-Cookie")
            try:
                return resp.status, json.loads(raw), set_cookie
            except json.JSONDecodeError:
                return resp.status, raw, set_cookie
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(raw), None
        except json.JSONDecodeError:
            return exc.code, raw, None


def _cookie_header(set_cookie: str | None) -> str | None:
    if not set_cookie:
        return None
    return set_cookie.split(";", 1)[0]


def main() -> int:
    os.environ.setdefault("MINING_OS_ENVIRONMENT", "staging")
    from mining_os.active_mine_intel.staging import (
        looks_like_production_url,
        staging_isolation_report,
    )
    from mining_os.active_mine_intel.claim_rollup import rollup_from_claims

    db = os.getenv("DATABASE_URL", "")
    origin = os.getenv("API_ORIGIN", "")
    base = (os.getenv("STAGING_BASE_URL") or origin or "http://127.0.0.1:8010").rstrip("/")
    if looks_like_production_url(db) or looks_like_production_url(origin) or looks_like_production_url(base):
        return _fail("process is wired to a production host")
    for marker in PRODUCTION_MARKERS:
        if marker in db.lower() or marker in origin.lower() or marker in base.lower():
            return _fail(f"production marker present: {marker}")

    report = staging_isolation_report(database_url=db, api_origin=origin or base)
    print("staging_isolation_report:", report)
    if report["staging"] and not report["ok"]:
        return _fail(f"staging isolation violations: {report['violations']}")

    total, unpaid, paid, unknown, rollup = rollup_from_claims(
        [
            {"payment_status": "paid"},
            {"payment_status": "Paid"},
            {"payment_status": "unpaid"},
            {"payment_status": "unknown"},
            {"payment_status": ""},
        ]
    )
    if (total, unpaid, paid, unknown, rollup) != (5, 1, 2, 2, "unpaid"):
        return _fail(f"claim_rollup Paid/Unpaid/Unknown contract drifted: {(total, unpaid, paid, unknown, rollup)}")
    print("claim_rollup contract: paid=2 unpaid=1 unknown=2 rollup=unpaid")

    status, health = _get(f"{base}/api/health")
    print("GET /api/health", status, health)
    if status != 200 or not isinstance(health, dict) or health.get("status") != "ok":
        return _fail("health check failed")

    status, meta = _get(f"{base}/api/active-mines/meta")
    print("GET /api/active-mines/meta", status, meta)
    if status != 200 or not isinstance(meta, dict):
        return _fail("meta endpoint failed")
    if not meta.get("enabled"):
        return _fail("Active Mine Search is disabled on staging")
    if not meta.get("staging"):
        return _fail("meta.staging is not true")
    if meta.get("staging_isolated") is not True:
        return _fail("meta.staging_isolated is not true")
    if "Producing" not in (meta.get("operational_statuses") or []):
        return _fail("operational taxonomy missing Producing")

    ident = os.getenv("STAGING_USERNAME", "craig-staging")
    password = os.getenv("STAGING_PASSWORD", "")
    email = os.getenv("STAGING_EMAIL", "craig-staging@miningos.local")
    if not password:
        return _fail("STAGING_PASSWORD is required for authenticated smoke")

    status, boot_status = _get(f"{base}/api/auth/bootstrap-status")
    print("GET /api/auth/bootstrap-status", status, boot_status)
    if status != 200 or not isinstance(boot_status, dict):
        return _fail("bootstrap-status failed")

    if boot_status.get("needs_bootstrap"):
        status, boot, set_cookie = _post(
            f"{base}/api/auth/bootstrap-admin",
            {
                "email": email,
                "username": ident,
                "password": password,
                "display_name": "Craig Staging",
            },
        )
        print("POST /api/auth/bootstrap-admin", status)
    else:
        status, boot, set_cookie = _post(
            f"{base}/api/auth/login",
            {"identifier": ident, "password": password},
        )
        print("POST /api/auth/login", status)

    if status not in {200, 201} or not isinstance(boot, dict):
        return _fail(f"auth failed: {status} {boot}")
    cookie = _cookie_header(set_cookie)
    if not cookie:
        return _fail("auth did not set a session cookie")

    status, me = _get(f"{base}/api/auth/me", cookie=cookie)
    print("GET /api/auth/me", status, isinstance(me, dict) and me.get("user", {}).get("username"))
    if status != 200:
        return _fail("authenticated /auth/me failed")

    status, sites = _get(f"{base}/api/active-mines/sites?state=NV", cookie=cookie)
    print("GET /api/active-mines/sites?state=NV", status)
    if status != 200 or not isinstance(sites, dict) or not sites.get("ok"):
        return _fail(f"sites list failed: {sites}")

    print("PASS: staging E2E smoke (health, meta, isolation, auth, sites, claim_rollup)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
