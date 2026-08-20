# Active Mine Search

Mining OS regenerates a ranked list of **active mines on unpatented claims** for Nevada and Utah using the same live methodology as the standalone mine-claim-matcher (BLM claims / plans / notices, MSHA, state production or DOGM). The product does **not** import a static CSV — every Pull re-queries sources (with cache TTL), rebuilds candidates, and overwrites the account’s list for that state.

## Enable

```bash
# Backend (.env)
ENABLE_ACTIVE_MINES_API=true

# Frontend
VITE_ENABLE_ACTIVE_MINES=true

# Schema
python -m mining_os.pipelines.run_all --init-db
```

Nav: **Active Mine Search** → `/active-mines`.

## User flow

1. Choose **NV** or **UT**.
2. **Pull active mines on unpatented claims** — starts an async job that runs the matcher pipeline, persists `candidate_sites`, bridges PLSS, and links each mine to a section **Target**.
3. **Fetch unpaid claims (each linked mine)** — walks linked mines in list order and runs the same Fetch Claim Records path as Target drilldown on each unique `area_of_focus_id`. Progress advances **after each Target** (not in chunks of 25). Shared PLSS Targets are scraped **once**; `update_site_claim_rollup` updates all mines on that Target. The UI reloads Claims / Unpaid as each step finishes. Each Target has a hard timeout (default **6 minutes**, env `MINING_OS_FETCH_UNPAID_TARGET_TIMEOUT_SEC`); on timeout the job **moves on** and still keeps any ArcGIS claims already checkpointed before payment enrichment.

## Architecture

| Layer | Path |
| ----- | ---- |
| Schema | `active_mine_intel` (`026_active_mine_intel.sql`) |
| Package | `mining_os/active_mine_intel/` |
| Vendored matcher | `mining_os/active_mine_intel/matcher/` |
| API | `/api/active-mines/*` behind `ENABLE_ACTIVE_MINES_API` |
| UI | `frontend/src/features/active-mines/` behind `VITE_ENABLE_ACTIVE_MINES` |
| Cache / raw downloads | `data_files/active_mines/` |

### Mine → PLSS Target

Many mines can share one section Target (`areas_of_focus` unique on `(account_id, plss_normalized)`).

- `candidate_sites` keeps matcher identity, scores, claim serials.
- `candidate_sites.area_of_focus_id` links to the section Target.
- New Targets use `source=active_mine_plss`, `retrieval_type=Known Mine`.
- Existing USMIN/MRDS/user section Targets are **reused**.
- Mines without resolvable PLSS stay `plss_unresolved` and are skipped for Fetch Claim Records.

PLSS bridge (`plss_bridge.py`): CadNSDI reverse geocode (Mining OS path) → matcher location/components → CSE_META (×10 T/R decode). Meridians: NV=21, UT=26.

### Payment status

Payment enrichment is **not** part of list construction. Use **Fetch unpaid claims** (existing MLRS / ArcGIS Fetch Claim Records path). Production payment banners still depend on the separate ROADMAP item for Render-safe enrichment.

## API

| Endpoint | Behavior |
| -------- | -------- |
| `GET /active-mines/meta` | Flag + supported states (public probe) |
| `POST /active-mines/pull` | `{state, refresh?}` → async run |
| `GET /active-mines/runs/{id}` | Progress / QC |
| `GET /active-mines/sites` | Filters: state, min_score, confidence, include_low, unpaid_only, search |
| `GET /active-mines/sites/{id}` | Detail + match pairs + Target |
| `POST /active-mines/fetch-unpaid` | `{state?}` or `{site_ids?}` → mine-order Fetch Claim Records (one Target at a time) |
| `GET /active-mines/fetch-jobs/{id}` | Progress (`processed`, `progress_json`, counts) |

## Out of scope (v1)

- Idaho
- One-time CSV import of sibling `outputs/*/candidate_sites.csv`
- Legal title opinion / surveyed boundaries
