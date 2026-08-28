# Active Mine Search — staging isolation

This task authorizes a **production-isolated staging / preview** environment only.
It does **not** authorize production deploy, promotion, merge, or auto-merge.

## Isolation rules

Set `MINING_OS_ENVIRONMENT=staging` on the staging API.

Staging **refuses** to run Pull / Fetch unpaid when:

- `DATABASE_URL` points at `miningos.onrender.com` or another production marker
- `API_ORIGIN` is `https://miningos.onrender.com`
- `RENDER_SERVICE_NAME` is the production service `mining-os-api`

Use `docker-compose.staging.yml` plus `config/staging.env.example` for a local
isolated Postgres (`miningos_staging`). Never copy production secrets into that file.

## Hands-on staging URL (this draft PR)

The complete app (SPA + API + isolated Postgres) is served from:

**https://changed-questionnaire-wav-shaw.trycloudflare.com**

That origin is **not** `miningos.onrender.com`. The database is local
`miningos_staging` on this staging host. Login credentials are in the draft PR
description (staging-only account; do not use production passwords).

Vercel Preview (`frontend/vercel.json`) rewrites `/api` to that same staging
API so the preview cannot silently hit production.

**Lifetime:** this URL is a Cloudflare quick tunnel in front of the isolated
staging API for this PR. If it goes down, re-run the tunnel or apply
`render.staging.yaml` as a **separate** Render service with a new staging
Postgres. Do not point either path at the production `DATABASE_URL`.

## Vercel preview

| Vercel environment | `/api` rewrite |
| --- | --- |
| Preview / this draft PR | Isolated staging API (trycloudflare URL above), **never** `https://miningos.onrender.com` |
| Production (only if this PR is later approved) | Restore `frontend/vercel.json` to `${API_ORIGIN}` with Production `API_ORIGIN=https://miningos.onrender.com` |

## Render staging template

See `render.staging.yaml`. Apply it as a **separate** Render service and attach a
**staging** Postgres instance. Do not point it at the production `DATABASE_URL`.

## Smoke tests

```bash
MINING_OS_ENVIRONMENT=staging .venv/bin/python scripts/staging_smoke.py
STAGING_BASE_URL=https://changed-questionnaire-wav-shaw.trycloudflare.com \
  STAGING_USERNAME=craig-staging STAGING_PASSWORD='…' \
  .venv/bin/python scripts/staging_e2e_smoke.py
```
