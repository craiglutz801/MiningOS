# Testing & Production Safety

Mining OS ships to production via Vercel (frontend) and Render/Railway
(backend). The deployment pipeline auto-deploys from `main`, so we treat
**the test suite as the gate** that protects production.

This document explains:
1. How to run the tests locally
2. How CI runs them automatically on every push
3. How to add tests for new features
4. The contract every API endpoint must obey

---

## 1. Run the tests locally

From the repo root:

```bash
.venv/bin/python -m pytest -q
```

or use the convenience script (also installable as a `pre-push` git hook):

```bash
bash scripts/pre-push.sh
```

Install it as an actual hook so you cannot push broken code by accident:

```bash
ln -sf ../../scripts/pre-push.sh .git/hooks/pre-push
chmod +x .git/hooks/pre-push
```

Now `git push` will refuse to push if any test fails.

---

## 2. CI runs the same tests on every push

`.github/workflows/test.yml` runs `pytest` on every push and pull request.
If tests fail in CI, **do not merge**. Render/Railway will happily deploy
a broken build — only green tests give us confidence.

---

## 3. The Endpoint Safety Contract

All user-facing API endpoints (everything called from the React UI) must
obey this contract:

> **Never return HTTP 500. Always return HTTP 200 with a structured JSON
> body that includes an `ok: bool` and, when `ok=false`, a human-readable
> `error` message.**

This rule exists because:
- The frontend interprets non-200 responses as a hard failure with no
  context, surfacing only `Internal Server Error` to the user.
- A missing companion repo (e.g. BLM_ClaimAgent) on the production host
  must NOT break a feature — it must degrade gracefully to the next-best
  data source.

The two production fixes that motivated this doc:

| Endpoint                                              | Old behavior in prod                  | New behavior                                                |
| ----------------------------------------------------- | ------------------------------------- | ----------------------------------------------------------- |
| `POST /api/areas-of-focus/{id}/fetch-claim-records`   | Returned "BLM_ClaimAgent not found".  | Falls back to built-in BLM ArcGIS API.                      |
| `POST /api/areas-of-focus/{id}/lr2000-geographic-report` | Returned `Internal Server Error` (500). | Wrapped in try/except — always returns structured JSON.   |

Both fixes are guarded by tests in `tests/test_api_endpoints.py`,
`tests/test_fetch_claim_records.py`, and `tests/test_mlrs_geographic_index.py`.

---

## 4. How to add a test for a new feature

Whenever you add or modify a user-facing endpoint or service, add at least:

1. **A happy-path test** that verifies the success response shape.
2. **A "service throws" test** that verifies the endpoint still returns
   200 with `ok: false` and a useful `error` string.
3. **A "missing dependency" test** for any optional companion repo,
   external service, or environment variable.

Use the patterns in `tests/test_api_endpoints.py`:
- `TestClient(app)` (no context manager, so startup events are skipped
  and no real DB is required).
- `monkeypatch` to replace service functions and DB writers.

---

## 5. Test inventory (current)

```
tests/
├── test_area_batch_actions.py        # batch fetch / LR2000 batching
├── test_api_endpoints.py             # ★ end-to-end API contract tests
├── test_automation_engine.py         # rules engine CRUD + execution
├── test_blm_plss.py                  # PLSS string parsing
├── test_fetch_claim_records.py       # ★ no-BLM-agent fallback (prod fix)
├── test_mlrs_geographic_index.py     # ★ LR2000 always-structured-JSON (prod fix)
└── test_plss_ai_lookup.py            # AI PLSS preview/apply flow
```

---

## 6. Pre-deploy checklist

Before pushing to `main` (which triggers the production deploy):

- [ ] `bash scripts/pre-push.sh` is green locally.
- [ ] If you added an endpoint, you added an `test_api_endpoints.py` test
      that asserts a 200 response in both success and failure paths.
- [ ] If you added a new optional dependency (a companion repo,
      environment variable, or external API), the service handles its
      absence gracefully and there's a test covering that.
