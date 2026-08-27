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

## Vercel preview

`frontend/vercel.json` rewrites `/api` to `${API_ORIGIN}`.

| Vercel environment | `API_ORIGIN` |
| --- | --- |
| Preview / this draft PR | Staging API origin, **never** `https://miningos.onrender.com` |
| Production (only if this PR is later approved) | `https://miningos.onrender.com` |

If `API_ORIGIN` is unset on a preview deployment, API calls fail closed instead of
proxying to production.

## Render staging template

See `render.staging.yaml`. Apply it as a **separate** Render service and attach a
**staging** Postgres instance. Do not point it at the production `DATABASE_URL`.

## Smoke test

```bash
MINING_OS_ENVIRONMENT=staging .venv/bin/python scripts/staging_smoke.py
```
