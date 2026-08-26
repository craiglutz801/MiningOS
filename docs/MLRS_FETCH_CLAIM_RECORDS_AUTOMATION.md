# MLRS Fetch Claim Records Automation

This document explains how Mining OS turns a Target with PLSS and/or latitude/longitude into stored MLRS claim records and a rolled-up `paid` / `unpaid` / `unknown` claim status.

The user-facing automation is:

- Page: `Automations`
- Modal: `New automation rule`
- Action: `Fetch Claim Records (MLRS)`
- Internal action id: `fetch_claim_records`

The most important implementation detail is that the app does not primarily scrape the MLRS search UI to discover claims. It queries BLM's public ArcGIS FeatureServer for active/not-closed MLRS mining claim records, then optionally loads each public MLRS case page to detect the red maintenance-fee banner that means a claim is unpaid.

## Executive Summary

Given a Target:

1. The automation rule selects matching Targets from `areas_of_focus`.
2. For each Target, the automation calls the canonical runner `run_fetch_claim_records_for_area_id()`.
3. The runner loads the Target and extracts:
   - `location_plss`
   - `state_abbr`
   - `meridian`
   - `township`
   - `range`
   - `section`
   - `latitude`
   - `longitude`
   - previous `characteristics.claim_records`
4. The Target's PLSS is normalized into BLM's encoded format.
5. The app queries BLM's MLRS ArcGIS FeatureServer by PLSS.
6. If no section-level claims are found, it broadens to township/range.
7. If coordinates exist, it also queries nearby claims within 2 km and merges them.
8. Each claim is normalized into a common shape with `claim_name`, `serial_number`, `case_page`, `payment_report`, and `payment_status`.
9. For claims with an MLRS case page, the app tries to determine payment status:
   - `unpaid` if the MLRS/RAS page contains the maintenance-fee warning.
   - `paid` if a fully loaded MLRS case page does not contain that warning.
   - `unknown` if the app cannot reliably load/inspect the page.
10. Results are saved to `areas_of_focus.characteristics.claim_records`.
11. The Target's top-level `status` is updated:
   - any claim `unpaid` -> Target `status = unpaid`
   - otherwise any claim `paid` -> Target `status = paid`
   - otherwise -> Target `status = unknown`

## Key Files

| Area | File | Purpose |
| --- | --- | --- |
| Automation UI | `frontend/src/pages/Automations.tsx` | Defines the `Fetch Claim Records (MLRS)` action label and rule form. |
| Target detail UI | `frontend/src/pages/Areas.tsx` | Manual per-target `Fetch Claim Records (PLSS)` button and display of claim records. |
| API client | `frontend/src/api.ts` | Starts/polls background fetch jobs and calls automation endpoints. |
| Automation API | `mining_os/api/main.py` | Exposes `/api/automations/*`, `/api/areas-of-focus/{id}/fetch-claim-records/start`, and batch endpoints. |
| Automation engine | `mining_os/services/automation_engine.py` | Filters Targets, queues/runs rules, dispatches `fetch_claim_records`. |
| Scheduler | `mining_os/services/automation_scheduler.py` | Runs enabled cron rules and reconciles stale runs. |
| Claim fetch pipeline | `mining_os/services/fetch_claim_records.py` | Canonical MLRS claim-record runner and Target status roll-up. |
| PLSS/ArcGIS query | `mining_os/services/blm_plss.py` | Normalizes PLSS and queries the BLM MLRS FeatureServer. |
| Payment enrichment | `mining_os/services/mlrs_case_payment.py` | Detects paid/unpaid by inspecting MLRS case pages and optional RAS pages. |
| Batch actions | `mining_os/services/area_batch_actions.py` | Batch version used by multi-select Targets table. |
| Automation schema | `mining_os/sql/017_automation_engine.sql` | `automation_rules` and `automation_run_log`. |
| Account migration | `mining_os/sql/019_accounts_auth.sql` | Adds `account_id` scoping to automation rules and targets. |

## External BLM Data Sources

### Claim Discovery: MLRS ArcGIS FeatureServer

The primary source for claim records is:

```text
https://gis.blm.gov/nlsdb/rest/services/HUB/BLM_Natl_MLRS_Mining_Claims_Not_Closed/FeatureServer/0/query
```

This endpoint returns MLRS mining claims that are not closed. The app queries it in two ways:

1. Attribute query by PLSS prefix, using `CSE_META LIKE ...`.
2. Spatial query by latitude/longitude envelope.

The endpoint is called from `mining_os/services/blm_plss.py`.

### Payment Status: MLRS Case Page

For each claim, the ArcGIS response includes a Salesforce id (`SF_ID`) and case number (`CSE_NR`). The app constructs:

```text
https://mlrs.blm.gov/s/blm-case/{SF_ID}/{CSE_NR}
```

The unpaid signal is the public MLRS maintenance-fee warning:

```text
Maintenance fee payment was not received and may result in the closing of the claim.
```

If that text appears, the claim is treated as `unpaid`.

### Optional RAS Serial Register Page

The app also constructs a RAS payment/report URL:

```text
https://reports.blm.gov/report.cfm?application=RAS&report=1&serial_number={CSE_NR}
```

RAS HTTP scanning is optional and off by default because the public report wrapper is inconsistent. It can be enabled with:

```text
MINING_OS_MLRS_PAYMENT_TRY_RAS_HTTP=1
```

## Data Model

### Target Input Fields

The source Target is a row in `areas_of_focus`. The fetch runner uses:

```text
id
name
location_plss
state_abbr
meridian
township
range
section
latitude
longitude
status
characteristics
account_id
```

The preferred input is already-parsed PLSS fields:

```text
state_abbr + township + range + optional section + optional meridian
```

If those are missing, the app parses `location_plss`.

Coordinates are not enough by themselves for this action today. The runner requires a valid PLSS row first, then uses coordinates as an augmentation/fallback. If no PLSS can be parsed and no stored PLSS fields exist, the action returns `ok: false`.

### Stored Output

The full result is saved in:

```text
areas_of_focus.characteristics.claim_records
```

Shape:

```json
{
  "fetched_at": "2026-07-16T00:00:00+00:00",
  "log": "human-readable query log",
  "claims": [
    {
      "claim_name": "Example Claim",
      "serial_number": "UT123456789",
      "case_page": "https://mlrs.blm.gov/s/blm-case/...",
      "payment_report": "https://reports.blm.gov/report.cfm?...",
      "state_abbr": "UT",
      "plss": "UT 26 0120S 0140E 023 ...",
      "BLM_PROD": "Lode",
      "payment_status": "paid|unpaid|unknown",
      "payment_message": "Maintenance fee payment was not received and may result in the closing of the claim.",
      "payment_check_source": "mlrs_case_aura",
      "payment_source_url": "https://mlrs.blm.gov/s/blm-case/...",
      "payment_checked_at": "2026-08-26T12:00:00Z",
      "payment_evidence_text": "BLM MLRS case record Next_Payment_Due_Date__c=2024-09-03 is on or before observation date 2026-08-26.",
      "payment_evidence_code": "NEXT_PAYMENT_DUE_OVERDUE"
    }
  ],
  "plss": "original location_plss string",
  "query_method": "built_in_api|built_in_api_broadened|spatial|...",
  "ok": true
}
```

The Target's top-level state is also updated:

```text
areas_of_focus.status
areas_of_focus.status_checked_at
areas_of_focus.state_abbr
areas_of_focus.meridian
areas_of_focus.characteristics.blm_prod_types
```

## Automation Rule Flow

### 1. UI Creates Rule

`frontend/src/pages/Automations.tsx` defines:

```ts
const ACTION_LABELS: Record<string, string> = {
  fetch_claim_records: "Fetch Claim Records (MLRS)",
  lr2000_report: "LR2000 / Geographic Index",
  check_blm_status: "Check BLM Status",
  generate_report: "Generate Report",
};
```

When a user creates a rule, the frontend sends:

```http
POST /api/automations/rules
```

Payload:

```json
{
  "name": "Weekly unpaid checks",
  "action_type": "fetch_claim_records",
  "filter_config": {
    "tag": "priority",
    "state_abbr": "NV",
    "include_targets_with_claim_status": false
  },
  "outcome_type": "log_only",
  "schedule_cron": "0 8 * * 1",
  "max_targets": 50,
  "enabled": true
}
```

The important optional flag is:

```text
include_targets_with_claim_status
```

If false or omitted, the automation skips Targets already marked `paid` or `unpaid`. This prevents an automation from overwriting a human-accepted claim-status decision unless explicitly allowed.

### 2. API Stores Rule

`mining_os/api/main.py` maps the request to:

```python
create_rule(...)
```

in `mining_os/services/automation_engine.py`.

Rules are stored in `automation_rules`:

```text
id
account_id
name
enabled
filter_config
action_type
outcome_type
schedule_cron
max_targets
created_at
updated_at
```

The supported action list is hard-coded:

```python
ACTION_TYPES = [
    "fetch_claim_records",
    "lr2000_report",
    "check_blm_status",
    "generate_report",
]
```

### 3. Rule Starts

A rule can run in two ways:

1. Manual: `POST /api/automations/rules/{rule_id}/trigger`
2. Scheduled: `automation_scheduler.py` wakes every 60 seconds and checks cron rules.

Both paths call:

```python
queue_rule_run(rule_id, trigger_type="manual|scheduled")
```

`queue_rule_run()`:

1. Loads the rule.
2. Refuses to start a duplicate run if the same rule already has a `running` run.
3. Creates a row in `automation_run_log`.
4. Starts a daemon thread for `_execute_rule_run()`.
5. Returns immediately to the frontend with a `run_id`.

### 4. Automation Filters Targets

The engine calls:

```python
_filter_targets(filter_config, max_targets, account_id=rule_account_id)
```

It delegates to `list_areas()` with filters such as:

```text
tag
mineral
status
state_abbr
claim_type
township
range_val
sector
name
```

Safety limits:

```text
MAX_TARGETS_CAP = 200
PAUSE_BETWEEN_TARGETS_SEC = 0.3
```

So one automation run processes at most 200 Targets.

### 5. Automation Dispatches Fetch Claim Records

For each selected Target:

```python
_run_action_on_target("fetch_claim_records", area, account_id=rule_account_id)
```

That calls:

```python
run_fetch_claim_records_for_area_id(area_id, account_id=account_id)
```

The automation records per-target result rows:

```json
{
  "id": 123,
  "name": "Target Name",
  "ok": true,
  "changed": true,
  "claims_count": 5,
  "error": null
}
```

`changed` means the number of stored claims changed compared with the previous `characteristics.claim_records.claims` count.

### 6. Automation Finishes

The engine updates `automation_run_log` throughout the run with:

```text
targets_total
targets_ok
targets_err
changes_found
results
summary
status
finished_at
email_sent
```

If configured, it sends an outcome email:

```text
log_only
email_always
email_on_change
email_on_error
```

The scheduler also runs a watchdog. Any run still marked `running` after six hours is auto-failed as stale.

## Manual Target Flow

The same canonical backend runner is used outside automations.

On the Targets page, a selected Target has a button:

```text
Fetch Claim Records (PLSS)
```

Frontend path:

```text
frontend/src/pages/Areas.tsx
```

API client path:

```text
frontend/src/api.ts
```

The frontend starts a background job:

```http
POST /api/areas-of-focus/{area_id}/fetch-claim-records/start
```

Then polls:

```http
GET /api/jobs/{job_id}
```

This avoids keeping one HTTP request open for several minutes while MLRS payment pages are checked. The browser polls for up to 90 minutes.

The direct synchronous endpoint also exists:

```http
POST /api/areas-of-focus/{area_id}/fetch-claim-records
```

But the frontend uses `/start`.

## PLSS Normalization

The code accepts several PLSS formats, including:

```text
12S 14E 23
T12S R14E Sec 23
UT T12S R14E Sec 23
Township 12 South Range 14 East Section 23
NV 21 0210N 0570E Sec 023
```

The parser lives in:

```text
mining_os/services/blm_plss.py
```

Core functions:

```python
parse_plss_string(plss, default_state="UT")
normalize_plss_field(value, kind)
_normalize_township(value)
_normalize_range(value)
_normalize_section(value)
```

BLM expects township/range in a 4-digit "times 10" encoded form:

```text
12S  -> 0120S
8N   -> 0080N
14E  -> 0140E
57W  -> 0570W
```

Sections are 3 digits:

```text
1  -> 001
23 -> 023
36 -> 036
```

Meridian defaults come from `STATE_MERIDIAN` in `fetch_claim_records.py`. Examples:

```text
UT -> 26  Salt Lake
NV -> 21  Mount Diablo
AZ -> 12  Gila and Salt River
WY -> 28  6th Principal
```

If the Target already has `state_abbr`, `township`, and `range`, the runner uses those fields instead of reparsing `location_plss`.

## Claim Discovery Algorithm

The canonical function is:

```python
fetch_claim_records_for_area(...)
```

in:

```text
mining_os/services/fetch_claim_records.py
```

### Step 1: Build PLSS Row

The runner builds:

```python
plss_row = {
    "Township": township,
    "Range": range_val,
    "Section": section or "",
    "State": state_abbr,
    "Meridian": meridian or STATE_MERIDIAN[state_abbr],
}
```

If stored fields are missing, it calls `_parse_plss_for_script(location_plss)`.

If no valid PLSS row can be produced, the function returns:

```json
{
  "ok": false,
  "claims": [],
  "error": "This target has no PLSS location set..."
}
```

### Step 2: Optional Legacy BLM_ClaimAgent Script

The app can optionally call a sibling repo named `BLM_ClaimAgent`:

```text
../BLM_ClaimAgent/get_mlrs_from_PLSS.py
```

This path is disabled by default, even if the repo exists.

Enable it with:

```text
MINING_OS_FETCH_CLAIM_RECORDS_USE_AGENT=1
```

The script path:

1. Writes a temporary CSV with `ProjectName`, `Township`, `Range`, `Section`, `State`, `Meridian`.
2. Runs `get_mlrs_from_PLSS.py`.
3. Reads `DataOutput/{out_name}.json`.
4. Normalizes claims.
5. Deletes temporary outputs.

This path can be slow and Selenium-heavy. Production normally uses the built-in ArcGIS API path instead.

### Step 3: Built-In PLSS ArcGIS Query

If the script path is disabled or returns no claims, the app queries BLM directly:

```python
query_claims_by_plss_with_status(
    state=plss_row["State"],
    township=plss_row["Township"],
    range_val=plss_row["Range"],
    section=plss_row["Section"] or None,
    meridian=plss_row["Meridian"],
)
```

This function returns:

```python
(queried_ok: bool, claims: list[dict])
```

That distinction matters:

```text
queried_ok=True, claims=[]   -> BLM responded, no claims found. Valid result.
queried_ok=False, claims=[]  -> BLM failed/unreachable. Error condition.
```

The PLSS query creates a SQL-like ArcGIS `where` clause:

With section:

```text
CSE_META LIKE 'UT 26 0120S 0140E 023%'
```

Without section:

```text
CSE_META LIKE 'UT 26 0120S 0140E %'
```

Request parameters:

```json
{
  "where": "CSE_META LIKE 'UT 26 0120S 0140E 023%'",
  "outFields": "*",
  "returnGeometry": "true",
  "outSR": "4326",
  "f": "json"
}
```

The HTTP helper retries transient failures:

```python
_blm_request_with_retry(params, retries=2)
```

### Step 4: Broaden from Section to Township/Range

If a section-level query succeeds but returns zero claims, and the Target had a section, the app removes the section and queries the whole township/range:

```python
section=None
```

The `query_method` becomes:

```text
built_in_api_broadened
```

This is important because a mine or prospect point may be associated with a section that does not exactly match the claim polygon's MLRS `CSE_META`, while the claim still belongs to the same township/range.

### Step 5: Spatial Augmentation by Coordinates

If the Target has `latitude` and `longitude`, the app also queries nearby claims:

```python
query_claims_by_coords(latitude, longitude, radius_meters=2000)
```

This builds a WGS84 envelope around the point and sends a spatial query:

```json
{
  "geometry": "min_lon,min_lat,max_lon,max_lat",
  "geometryType": "esriGeometryEnvelope",
  "spatialRel": "esriSpatialRelIntersects",
  "inSR": "4326",
  "outFields": "*",
  "returnGeometry": "true",
  "outSR": "4326",
  "f": "json"
}
```

If PLSS claims already exist, spatial claims are merged in. Duplicates are removed using:

1. `serial_number`
2. `case_page`
3. `claim_name + plss`

If no PLSS claims exist, spatial claims become the result set and `query_method = spatial`.

## ArcGIS Response Parsing

The ArcGIS response is parsed by:

```python
_extract_claims_from_response(data, default_state)
```

For each `feature`:

```text
feature.attributes.SF_ID    -> Salesforce MLRS case id
feature.attributes.CSE_NR   -> serial number
feature.attributes.CSE_NAME -> claim name
feature.attributes.CSE_META -> PLSS metadata
feature.attributes.BLM_PROD -> claim/product type, when present
feature.geometry            -> polygon geometry
```

Claims without `SF_ID` or `CSE_NR` are skipped. Duplicate serial numbers are skipped.

The app constructs:

```python
case_url = f"https://mlrs.blm.gov/s/blm-case/{SF_ID}/{CSE_NR}"
report_url = f"https://reports.blm.gov/report.cfm?application=RAS&report=1&serial_number={CSE_NR}"
```

Normalized claim shape from ArcGIS:

```json
{
  "claim_name": "CLAIM NAME",
  "serial_number": "UT123456789",
  "case_page": "https://mlrs.blm.gov/s/blm-case/...",
  "payment_report": "https://reports.blm.gov/report.cfm?application=RAS&report=1&serial_number=UT123456789",
  "state_abbr": "UT",
  "plss": "UT 26 0120S 0140E 023 ...",
  "geometry": {},
  "BLM_PROD": "Lode"
}
```

Then `_normalize_claims()`:

1. Maps legacy `CSE_NAME` -> `claim_name`.
2. Maps legacy `CSE_NR` -> `serial_number`.
3. Defaults missing `payment_status` to `unknown`.
4. Removes heavy/noisy fields before storing in `claim_records`.
5. Removes accidental `.gov` banner text from `account_name`.

## Payment Status Enrichment

ArcGIS returns claim metadata, but it does not reliably return current maintenance-fee payment status. Payment status is derived by inspecting public case/report pages.

Main file:

```text
mining_os/services/mlrs_case_payment.py
```

Main entry:

```python
enrich_claims_from_mlrs_case_pages(claims, progress_cb=None)
```

### Payment Status Values

Each claim gets:

```text
payment_status = paid | unpaid | current | due_today | past_due | closed | unknown
```

Meaning:

- `unpaid`: explicit BLM nonpayment warning (page banner or case-record field).
- `paid`: explicit maintenance-fee payment date/flag on the case record.
- `current` / `due_today`: next-payment due date is still in the compliance window (due date is supporting evidence, not a receipt). Small-miner waiver + current due date is `current`, not Paid.
- `past_due`: due date is strictly before the observation date on an open case, without the nonpayment warning.
- `closed`: case status is closed/void/forfeited/abandoned.
- `unknown`: missing record, serial mismatch, schema drift, timeout, or upstream failure.

### Unpaid Detection Rule

The unpaid phrase is:

```python
_UNPAID_LOWER = "maintenance fee payment was not received"
```

Standard message:

```python
_STANDARD_MESSAGE = (
    "Maintenance fee payment was not received and may result in the closing of the claim."
)
```

If the phrase appears in page text, the claim is marked:

```json
{
  "payment_status": "unpaid",
  "payment_message": "Maintenance fee payment was not received and may result in the closing of the claim.",
  "payment_check_source": "mlrs_case_playwright"
}
```

### Enrichment Order

For each claim with a `case_page` URL and unresolved `payment_status`:

1. Check in-memory cache.
2. Try plain HTTP GET of `case_page`.
3. Optionally try RAS HTTP/iframe scan if `MINING_OS_MLRS_PAYMENT_TRY_RAS_HTTP=1`.
4. If allowed, run headless Playwright against the MLRS case page.
5. If Playwright is unavailable/fails, try Selenium.
6. If no method can determine status, leave `payment_status = unknown`.

### Why Headless Browser Is Needed

`mlrs.blm.gov/s/blm-case/...` is a Salesforce Lightning SPA. The maintenance-fee warning is usually rendered client-side. A plain `requests.get()` often returns only a shell such as "Loading" or "CSS Error", not the actual case details.

The browser path loads the page with:

```text
wait_until = domcontentloaded
```

It intentionally does not wait for `networkidle`, because Salesforce pages can keep long-lived connections open forever.

Playwright then polls:

1. `page.content()`
2. `page.inner_text("body")`
3. all frame contents/text
4. a text locator for `maintenance fee payment was not received`

If case details appear loaded and the unpaid phrase is absent, the claim is treated as `paid`.

If the page remains shell-like, the claim stays `unknown`.

### Production Behavior

By default:

- Every Fetch Claim Records run first uses the **production truth layer**: public MLRS case record via Salesforce Aura `DetailController.getRecord` (no browser).
- **Paid / Unpaid are not inferred from `Next_Payment_Due_Date__c` alone.** A future due date is a compliance deadline (Current), not a payment receipt. Fees/waivers are timely on or before the due date, so due-today is not Unpaid. A stale past due date is a Past-due indicator, not the BLM nonpayment warning.
- Exact Aura fields used: `Serial_Number__c`, `Lead_File_Number__c`, `Case_Status__c`, `Next_Payment_Due_Date__c`, plus payment/waiver/nonpayment fields when present (`Last_Payment_Date__c`, `Small_Miner_Waiver__c`, …). See `mining_os/services/mlrs_payment_truth.py`.
- Serial mismatch and closed/void/forfeited/abandoned cases fail closed (Unknown or Closed). Missing/failed Aura data stays unknown and is never unpaid.
- HTTP/Playwright/Selenium may still apply **Unpaid** when the public page contains the explicit BLM nonpayment warning. They no longer treat “page loaded, no banner” as Paid.
- Local/dev machines may still try headless Playwright if Aura leaves the claim unknown.
- PaaS hosts such as Render/Railway never use Selenium. Playwright stays off unless `MINING_OS_MLRS_PAYMENT_HEADLESS=1`.

The host check looks for:

```text
RENDER
RAILWAY_ENVIRONMENT
K_SERVICE
DYNO
```

Override:

```text
MINING_OS_MLRS_PAYMENT_HEADLESS=1  # force browser enrichment
MINING_OS_MLRS_PAYMENT_HEADLESS=0  # disable browser enrichment
```

To make production payment status authoritative, production must have:

```text
playwright
chromium browser installed
MINING_OS_MLRS_PAYMENT_HEADLESS=1
```

Without that, production still fetches claim metadata, but many `payment_status` values may remain `unknown`.

### Payment Cache

The app seeds an in-memory payment cache from the previous persisted `characteristics.claim_records` snapshot:

```python
prime_payment_cache(previous_claims, fetched_at=prior_fetched_at)
```

Default cache TTL:

```text
MINING_OS_MLRS_PAYMENT_CACHE_TTL_HOURS=24
```

Cache keys:

```text
case:{case_page}
serial:{serial_number}
```

This prevents repeated checks for recently resolved claims. Cached **current / due-today / paid** labels are **not** reused once the stored `payment_due_date` is on or before today's UTC date, even if the 24-hour TTL has not expired.

### Live smoke (Aura contract)

Aura `DetailController.getRecord` is an undocumented Salesforce implementation detail. After changing this path, run:

```bash
python scripts/smoke_mlrs_payment_truth.py \
  'https://mlrs.blm.gov/s/blm-case/a02t000000593dSAAQ/UT101527746'
```

Check:

1. `url_validation.ok` is true only for `https://mlrs.blm.gov/s/blm-case/<sfId>/…`.
2. `payment_source_health` is `ok` (identity + due-date fields present) or `drift`.
3. Closed cases return `closed`, not `unpaid`.
4. A due date equal to today returns `due_today`, not `unpaid`.
5. `GET /api/diag/check-payment?case_url=…` returns the same evidence fields.

A redacted production-shaped Aura envelope lives at `tests/fixtures/mlrs_aura/get_record_redacted.json`.

### Batch Isolation

Playwright enrichment runs in a subprocess:

```bash
python -m mining_os.services.mlrs_case_payment
```

Reason: long-lived API worker threads can hang on repeated `sync_playwright()` launches. The subprocess isolates Playwright/Chromium state and streams progress back to the parent process over stderr.

Large batches are chunked:

```text
MINING_OS_MLRS_PAYMENT_MAX_CLAIMS
MINING_OS_MLRS_PAYMENT_LARGE_BATCH_CHUNK_SIZE
MINING_OS_MLRS_PARALLELISM
```

Defaults are conservative on PaaS.

## Target Status Roll-Up

After claims are fetched and enriched, `fetch_claim_records_for_area()` computes the Target-level status:

```python
from mining_os.services.mlrs_payment_truth import rollup_payment_status

derived_status = rollup_payment_status(
    [(c.get("payment_status") or "unknown").lower() for c in claims]
)
```

Then:

```python
update_area_status(area_id, status=derived_status, account_id=account_id)
```

So:

| Claim payment statuses | Target `status` |
| --- | --- |
| any `unpaid` | `unpaid` |
| all `paid` | `paid` |
| all `current` / `due_today` | `current` |
| all `past_due` | `past_due` |
| all `closed` | `closed` |
| all `unknown` | `unknown` |
| mixed (including Paid + Unknown) | `partial` |
| no claims | no paid/unpaid roll-up from claims |

This top-level `status` is what the rest of the app calls Claim Status.

## Error Handling

The fetch path is designed to return structured JSON instead of throwing a 500.

Common results:

### No PLSS

```json
{
  "ok": false,
  "claims": [],
  "error": "This target has no PLSS location set..."
}
```

### BLM Responded, No Claims

```json
{
  "ok": true,
  "claims": [],
  "query_method": "built_in_api_only"
}
```

This is not an error. It means BLM returned zero not-closed MLRS mining claims for that PLSS/search.

### BLM Service Unreachable

```json
{
  "ok": false,
  "claims": [],
  "error": "BLM ArcGIS MLRS service is temporarily unreachable. Please try again in a moment."
}
```

### Payment Page Could Not Be Checked

The claim remains in the result set, but payment fields stay unknown:

```json
{
  "payment_status": "unknown",
  "payment_message": null,
  "payment_check_error": "playwright chromium launch failed...",
  "payment_check_source": "mlrs_case_playwright"
}
```

The Target status may therefore remain `unknown` unless another claim resolved to `paid` or `unpaid`.

## Porting Guide

To reuse this logic in another application, port these pieces in this order.

### 1. Data Needed Per Target

Minimum:

```json
{
  "id": 123,
  "name": "Target name",
  "state_abbr": "UT",
  "meridian": "26",
  "township": "0120S",
  "range": "0140E",
  "section": "023",
  "latitude": 38.123,
  "longitude": -113.456
}
```

Or:

```json
{
  "location_plss": "UT T12S R14E Sec 23",
  "latitude": 38.123,
  "longitude": -113.456
}
```

Recommended: store parsed PLSS fields separately and keep `location_plss` only as display/source text.

### 2. Port PLSS Normalizers

Port from `blm_plss.py`:

```python
parse_plss_string()
normalize_plss_field()
_normalize_township()
_normalize_range()
_normalize_section()
```

Also port `STATE_MERIDIAN` from `fetch_claim_records.py`.

### 3. Query ArcGIS by PLSS

Implement:

```python
query_claims_by_plss_with_status(state, township, range_val, section, meridian)
```

Use:

```text
GET https://gis.blm.gov/nlsdb/rest/services/HUB/BLM_Natl_MLRS_Mining_Claims_Not_Closed/FeatureServer/0/query
```

With:

```json
{
  "where": "CSE_META LIKE 'UT 26 0120S 0140E 023%'",
  "outFields": "*",
  "returnGeometry": "true",
  "outSR": "4326",
  "f": "json"
}
```

Return both:

```text
queried_ok
claims
```

Do not treat an empty successful response as an error.

### 4. Broaden When Needed

If `section` query returns zero claims and BLM responded successfully, retry with:

```text
section = None
```

This catches claims in the broader township/range.

### 5. Add Coordinate Search

If latitude/longitude exist, query a 2 km envelope:

```python
deg_lat = radius_meters / 111_320.0
deg_lon = radius_meters / (111_320.0 * max(cos(radians(lat)), 0.01))
```

Send:

```json
{
  "geometry": "min_lon,min_lat,max_lon,max_lat",
  "geometryType": "esriGeometryEnvelope",
  "spatialRel": "esriSpatialRelIntersects",
  "inSR": "4326",
  "outFields": "*",
  "returnGeometry": "true",
  "outSR": "4326",
  "f": "json"
}
```

Merge duplicates by serial number first.

### 6. Normalize Claims

For each ArcGIS feature:

```python
attrs = feature["attributes"]
sf_id = attrs["SF_ID"]
case_nr = attrs["CSE_NR"]
case_url = f"https://mlrs.blm.gov/s/blm-case/{sf_id}/{case_nr}"
report_url = f"https://reports.blm.gov/report.cfm?application=RAS&report=1&serial_number={case_nr}"
```

Store:

```json
{
  "claim_name": "attrs.CSE_NAME",
  "serial_number": "attrs.CSE_NR",
  "case_page": "case_url",
  "payment_report": "report_url",
  "state_abbr": "first 2 chars of attrs.CSE_META",
  "plss": "attrs.CSE_META",
  "BLM_PROD": "attrs.BLM_PROD",
  "payment_status": "unknown"
}
```

### 7. Enrich Payment Status

Implement the same decision rule:

```text
If page text contains "maintenance fee payment was not received":
    payment_status = unpaid
Else if a real MLRS case page loads and case details are visible:
    payment_status = paid
Else:
    payment_status = unknown
```

For reliability, use Playwright/Chromium:

1. Open `case_page`.
2. Wait for `domcontentloaded`.
3. Poll body text and frame text for up to 30 seconds.
4. Search for the unpaid phrase.
5. If case-detail markers are present and no unpaid phrase appears, mark `paid`.
6. If the page is still only `Loading` / `CSS Error`, keep `unknown`.

Case-detail markers used by Mining OS include:

```text
blm case
serial number
case disposition
case customers
related records
```

### 8. Roll Up Target Status

Use:

```python
if any(claim.payment_status == "unpaid"):
    target.status = "unpaid"
elif any(claim.payment_status == "paid"):
    target.status = "paid"
else:
    target.status = "unknown"
```

### 9. Persist Snapshot

Store a full fetch snapshot with:

```text
fetched_at
claims
query_method
log
ok
error
input PLSS/coords
```

This makes the process auditable and allows the payment cache to reuse recent known statuses.

## Environment Variables

| Variable | Purpose |
| --- | --- |
| `MINING_OS_FETCH_CLAIM_RECORDS_USE_AGENT=1` | Enables legacy sibling `BLM_ClaimAgent/get_mlrs_from_PLSS.py` path. Disabled by default. |
| `MINING_OS_BLM_AGENT_PATH=/path/to/BLM_ClaimAgent` | Custom path to sibling agent repo. |
| `MINING_OS_MLRS_PAYMENT_AURA_TIMEOUT_SEC=12` | HTTP timeout for the production Aura case-record truth layer. |
| `MINING_OS_MLRS_PAYMENT_HEADLESS=1` | Forces Playwright/Selenium payment-page enrichment even on PaaS. |
| `MINING_OS_MLRS_PAYMENT_HEADLESS=0` | Disables browser payment-page enrichment. |
| `MINING_OS_MLRS_PLAYWRIGHT_MAX_MS=30000` | Per-case Playwright polling cap, clamped between 12s and 120s. |
| `MINING_OS_MLRS_PARALLELISM=1..4` | Parallel payment-enrichment chunks. |
| `MINING_OS_MLRS_PAYMENT_MAX_CLAIMS=60` | Fast-path max claims before chunked processing. |
| `MINING_OS_MLRS_PAYMENT_LARGE_BATCH_CHUNK_SIZE=20` | Chunk size for large payment-enrichment batches. |
| `MINING_OS_MLRS_PAYMENT_CACHE_TTL_HOURS=24` | Cache TTL for previously resolved payment statuses. |
| `MINING_OS_MLRS_PAYMENT_TRY_RAS_HTTP=1` | Enables optional RAS HTTP/iframe scan. |
| `MINING_OS_MLRS_PAYMENT_HTTP_TIMEOUT_SEC=8` | HTTP timeout for MLRS case page attempt. |
| `MINING_OS_MLRS_PAYMENT_RAS_TIMEOUT_SEC=6` | HTTP timeout for RAS attempts. |
| `MINING_OS_MLRS_PAYMENT_SELENIUM_TIMEOUT_SEC=18` | Selenium page-load timeout. |

## Production Notes

1. The ArcGIS claim-record query is production-safe and does not require Selenium.
2. The payment-status enrichment is only authoritative when the environment can inspect MLRS case pages.
3. On Render/Railway, headless enrichment is off by default unless `MINING_OS_MLRS_PAYMENT_HEADLESS=1`.
4. If production does not have Playwright Chromium installed, many claims may remain `payment_status = unknown`.
5. A Target with any known `unpaid` claim will still become `unpaid` even if other claims are unknown.
6. A successful zero-claim response is valid and should not be shown as a system failure.
7. Automations skip already `paid`/`unpaid` Targets by default unless `include_targets_with_claim_status` is enabled.

## Minimal Pseudocode

```python
def fetch_claim_records_for_target(target):
    plss = build_plss_row(target)
    if not plss:
        return {"ok": False, "claims": [], "error": "No parseable PLSS"}

    claims = []
    queried_ok, section_claims = query_claims_by_plss_with_status(
        state=plss.state,
        meridian=plss.meridian,
        township=plss.township,
        range_val=plss.range,
        section=plss.section,
    )

    if section_claims:
        claims = section_claims
        query_method = "built_in_api"
    elif queried_ok and plss.section:
        queried_ok, broad_claims = query_claims_by_plss_with_status(
            state=plss.state,
            meridian=plss.meridian,
            township=plss.township,
            range_val=plss.range,
            section=None,
        )
        claims = broad_claims
        query_method = "built_in_api_broadened" if broad_claims else "built_in_api_only"
    elif not queried_ok:
        return {"ok": False, "claims": [], "error": "BLM service unreachable"}

    if target.latitude is not None and target.longitude is not None:
        spatial_claims = query_claims_by_coords(target.latitude, target.longitude, radius_meters=2000)
        claims = merge_claim_lists(claims, spatial_claims)

    claims = normalize_claims(claims)
    claims = enrich_claims_from_mlrs_case_pages(claims)

    snapshot = {
        "ok": True,
        "fetched_at": now_utc_iso(),
        "claims": claims,
        "query_method": query_method,
        "plss": target.location_plss,
    }
    target.characteristics["claim_records"] = snapshot

    statuses = {claim.get("payment_status", "unknown") for claim in claims}
    if "unpaid" in statuses:
        target.status = "unpaid"
    elif "paid" in statuses:
        target.status = "paid"
    elif claims:
        target.status = "unknown"

    return snapshot
```

## Validation Tests

Relevant tests:

```text
tests/test_fetch_claim_records.py
tests/test_mlrs_case_payment.py
tests/test_mlrs_geographic_index.py
tests/test_automation_engine.py
```

The tests cover:

- fallback when `BLM_ClaimAgent` is not deployed
- distinguishing successful zero-claim responses from BLM failures
- spatial fallback and spatial augmentation
- broadening from section to township/range
- claim normalization
- `unpaid` winning Target status roll-up
- `paid` roll-up when only paid/unknown claims exist
- payment-page enrichment behavior

## Common Misunderstandings

### "Does this scrape the MLRS map?"

No. Claim discovery uses BLM's ArcGIS FeatureServer. The MLRS website is used later for case-page payment status enrichment.

### "Can latitude/longitude alone run Fetch Claim Records?"

Not currently. The canonical Fetch Claim Records action requires parseable PLSS or stored PLSS fields. Coordinates are then used to augment/fallback with nearby spatial claims.

### "Why does production find claims but not mark them paid/unpaid?"

Because ArcGIS returns claim records, not the maintenance-fee banner. Paid/unpaid needs MLRS case-page inspection, which requires Playwright/Selenium unless the status is cached or already present.

### "What exactly means unpaid?"

The detected public phrase:

```text
Maintenance fee payment was not received and may result in the closing of the claim.
```

### "What exactly means paid?"

Mining OS treats a claim as paid when a real MLRS case page loads, expected case details are visible, and the unpaid maintenance-fee warning is absent.

### "What does unknown mean?"

The app found the claim, but could not confidently determine payment status from the case/report page.
