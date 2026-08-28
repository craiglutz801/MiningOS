# Active Mine Search — staging isolation

This task authorizes a **production-isolated staging / preview** environment only.
It does **not** authorize production deploy, promotion, merge, or auto-merge.

## Isolation rules

Set `MINING_OS_ENVIRONMENT=staging` on the staging API.

Staging **refuses** to start (and refuses Pull / Fetch unpaid) when:

- `DATABASE_URL` points at `miningos.onrender.com` or another production marker
- `API_ORIGIN` is `https://miningos.onrender.com`
- `RENDER_SERVICE_NAME` is the production service `mining-os-api`

Production **refuses** to start when `API_ORIGIN` / `DATABASE_URL` is a
trycloudflare/ngrok tunnel or the staging Render service.

`python scripts/assert_env_wiring.py` (also run from pytest) checks mergeable
files:

- `frontend/vercel.json`, `frontend/.env.production`, and `render.yaml` must
  **not** mention `trycloudflare.com`
- `frontend/vercel.json` default `/api` rewrite is `https://miningos.onrender.com`
- Git-preview Vercel hosts (`mining-os-git-*.vercel.app`) rewrite `/api` to
  `https://mining-os-api-staging.onrender.com` (never production, never a tunnel)
- `render.staging.yaml` / `config/staging.env.example` must **not** mention
  `miningos.onrender.com`

## Durable staging URL

Hands-on testing uses the **Render staging service**, not the agent-host
Cloudflare quick tunnel:

**https://mining-os-api-staging.onrender.com**

That origin is **not** `miningos.onrender.com`. It uses its own Postgres
(`miningos-staging-db` in `render.staging.yaml`). Login credentials are in the
draft PR description (staging-only account; do not use production passwords).

Do not treat `*.trycloudflare.com` as the durable staging URL. Quick tunnels die
when the agent VM stops.

## How to provision (one-time)

This repo cannot create Render resources without credentials. Either:

1. **Dashboard:** apply `render.staging.yaml` as a **new** Blueprint (do not
   replace production `render.yaml`). That creates `miningos-staging-db` +
   `mining-os-api-staging`.
2. **API:** set `RENDER_API_KEY` plus optionally `STAGING_DATABASE_URL` (only if
   you are not using the Blueprint database) and re-run the agent.

Never copy the production `DATABASE_URL` into the staging service.

## Vercel preview vs production

| Vercel host | `/api` rewrite |
| --- | --- |
| `mining-os-git-*.vercel.app` (PR previews) | `https://mining-os-api-staging.onrender.com` |
| Production / custom domain / default | `https://miningos.onrender.com` |

The SPA keeps relative `/api/...` calls. Preview isolation is the rewrite, not a
hard-coded tunnel in `vercel.json`.

## Local isolated compose

Use `docker-compose.staging.yml` plus `config/staging.env.example` for a local
isolated Postgres (`miningos_staging`). Never copy production secrets into that file.

## Smoke tests

```bash
python scripts/assert_env_wiring.py
MINING_OS_ENVIRONMENT=staging python scripts/staging_smoke.py
STAGING_BASE_URL=https://mining-os-api-staging.onrender.com \
  STAGING_USERNAME=craig-staging STAGING_PASSWORD='…' \
  python scripts/staging_e2e_smoke.py
```
