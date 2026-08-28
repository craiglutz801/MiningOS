# Active Mine Search — staging hands-on checklist

Use this on the **staging / preview URL only**. Do not run against production
(`miningos.onrender.com` or the production database).

## Before you start

- [ ] Open the **complete staging URL** from the draft PR (Cloudflare tunnel serving SPA + isolated API). Prefer that over the Vercel preview if the preview login wall appears.
- [ ] If a previous visit showed `Failed to fetch dynamically imported module`, hard-refresh once (Ctrl+Shift+R / Cmd+Shift+R) so the browser drops the stale hashed chunk.
- [ ] Log in with the **staging-only** account from the draft PR (never production credentials).
- [ ] Confirm the amber **Staging environment** banner on Active Mine Search.
- [ ] Confirm `GET /api/active-mines/meta` shows `"staging": true` and `"staging_isolated": true`.
- [ ] Confirm the API origin is **not** `https://miningos.onrender.com`.

## Pull (live sources — no static CSV)

- [ ] Choose **Nevada**. Click **Pull active mines on unpatented claims**.
- [ ] Wait until the pull finishes (`success` or `partial`). A failed source must show in run QC as `failed` / `stale`, not as a silent zero list.
- [ ] Repeat for **Utah**.
- [ ] Open a Utah run’s QC JSON and confirm `utah_dogm_coverage` is present, including any **uranium** or **full-minerals** gaps.

## Evidence model (separate from payment status)

- [ ] List columns show **Op. status**, **Tenure**, and **Verify** in addition to Paid/Unpaid.
- [ ] Open a mine detail panel.
- [ ] Confirm operational status is one of: Producing, Permitted, Exploration, Mill/processor, Care-and-maintenance, Reclamation, Unknown.
- [ ] Confirm **Producing** was not assigned from a permit, claim polygon, MSHA status, BMRR status, inspection, or hours alone.
- [ ] Confirm BLM Not Closed / unpatented polygons are described as **tenure evidence only**.
- [ ] If mixed patented + unpatented intersection exists, **Tenure** is `Mixed` and geometry limitations are visible.
- [ ] Per-assertion provenance lists source ID/URL, retrieved/effective date, match method, freshness, confidence, and any contradiction.

## Source failure vs empty

- [ ] If a source is down, run QC / source status is `failed` (or `stale`), not `success` with `record_count: 0`.
- [ ] A valid empty extract uses status `empty` / outcome `empty`.

## Verification

- [ ] Default state is **Candidate** (or **Cross-source confirmed** when two independent sources agree and nothing is fail-closed).
- [ ] Complete the dated checklist (all boxes, reviewer name, date) and save **Human Verified**.
- [ ] Confirm you cannot save Human Verified without a date.

## Payment status (must be unchanged)

- [ ] **Fetch unpaid claims** still uses the existing Fetch Claim Records path.
- [ ] Paid / Unpaid / Unknown list columns still match Target drilldown claim records.
- [ ] Do not treat due dates or BMRR/MSHA as payment truth.

## Sign-off (staging only)

- [ ] I tested on a non-production URL with a non-production database.
- [ ] I am **not** requesting production promotion from this pass.
- [ ] Notes / blockers:

Reviewed by: _________________ Date: _____________
