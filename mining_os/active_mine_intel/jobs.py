"""Active Mine Search jobs: live matcher pull + batch Fetch Claim Records."""

from __future__ import annotations

import logging
import math
import multiprocessing as mp
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mining_os.active_mine_intel.config import SUPPORTED_STATES, active_mines_enabled
from mining_os.active_mine_intel import store
from mining_os.active_mine_intel.plss_bridge import resolve_site_plss
from mining_os.active_mine_intel.target_link import resolve_or_create_section_target

log = logging.getLogger("mining_os.active_mine_intel.jobs")

_pull_lock = threading.Lock()
_fetch_lock = threading.Lock()
_active_pull: dict[str, Any] = {}  # run_id, account_id, state

# Matcher stages (16) + persist/link stages (4)
JOB_STAGE_TOTAL = 20

# Per-Target hard cap for Fetch unpaid (ArcGIS + payment enrich). One stuck
# Selenium scrape must not block the rest of the state.
_DEFAULT_PER_TARGET_TIMEOUT_SEC = 360  # 6 minutes


def _progress(run_id: str, stage: int, message: str, **detail: Any) -> None:
    try:
        store.set_run_progress(
            run_id,
            stage=stage,
            total_stages=JOB_STAGE_TOTAL,
            message=message,
            detail=detail or None,
        )
    except Exception:
        log.debug("progress update failed", exc_info=True)


def _pipeline_progress_adapter(run_id: str):
    """Map matcher 1..16 stages onto job progress (same numbers; jobs use 17–20 after)."""

    def _cb(stage: int, _total: int, message: str) -> None:
        _progress(run_id, stage, message)

    return _cb


def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _safe_str(v: Any) -> str | None:
    if v is None:
        return None
    try:
        import pandas as pd

        if pd.isna(v):
            return None
    except Exception:
        pass
    s = str(v).strip()
    return s if s and s.lower() not in ("nan", "none", "nat") else None


def _serials_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return [str(x).strip() for x in raw if str(x).strip()]
    s = str(raw).strip()
    if not s:
        return []
    return [p.strip() for p in s.replace(",", ";").split(";") if p.strip()]


def _dataframe_to_site_dicts(site_summary: Any, state_abbr: str) -> list[dict[str, Any]]:
    if site_summary is None:
        return []
    try:
        import pandas as pd

        if hasattr(site_summary, "empty") and site_summary.empty:
            return []
        df = site_summary.copy()
        if "geometry" in df.columns:
            df = df.drop(columns=["geometry"])
        rows = []
        for _, r in df.iterrows():
            d = {k: (None if (isinstance(v, float) and math.isnan(v)) else v) for k, v in r.items()}
            rows.append(
                {
                    "mine_site_id": _safe_str(d.get("mine_site_id")) or "",
                    "state_abbr": state_abbr,
                    "rank": int(d["rank"]) if d.get("rank") is not None and str(d.get("rank")).isdigit() else d.get("rank"),
                    "name": _safe_str(d.get("mine_name") or d.get("name")),
                    "operator_name": _safe_str(d.get("operator_name")),
                    "commodity": _safe_str(d.get("commodity")),
                    "county": _safe_str(d.get("county")),
                    "latitude": _safe_float(d.get("latitude")),
                    "longitude": _safe_float(d.get("longitude")),
                    "total_score": _safe_float(d.get("total_score")),
                    "activity_score": _safe_float(d.get("activity_score")),
                    "claim_match_score": _safe_float(d.get("claim_match_score")),
                    "data_quality_score": _safe_float(d.get("data_quality_score")),
                    "penalty_score": _safe_float(d.get("penalty_score")),
                    "confidence_category": _safe_str(d.get("confidence_category")),
                    "activity_label": _safe_str(d.get("mine_activity_label") or d.get("activity_label")),
                    "best_claim_serial": _safe_str(d.get("best_claim_serial_number") or d.get("best_claim_serial")),
                    "best_claim_name": _safe_str(d.get("best_claim_name")),
                    "best_match_type": _safe_str(d.get("best_match_type")),
                    "best_distance_meters": _safe_float(d.get("best_distance_meters")),
                    "claim_count": int(d.get("claim_count") or 0),
                    "claim_serials": _serials_list(d.get("all_claim_serial_numbers") or d.get("claim_serials")),
                    "blm_plan_present": bool(d.get("blm_plan_present")),
                    "blm_notice_present": bool(d.get("blm_notice_present")),
                    "msha_status": _safe_str(d.get("msha_status")),
                    "score_breakdown_json": d.get("score_breakdown_json"),
                    "evidence_summary_json": d.get("evidence_summary_json"),
                    "recommended_next_action": _safe_str(d.get("recommended_next_action")),
                    "location_plss": _safe_str(d.get("location_plss")),
                    "township": _safe_str(d.get("township") or d.get("plss_township")),
                    "range": _safe_str(d.get("range") or d.get("plss_range")),
                    "section": _safe_str(d.get("section") or d.get("plss_section")),
                    "plss_source": _safe_str(d.get("plss_source")),
                }
            )
        return [r for r in rows if r["mine_site_id"]]
    except Exception:
        log.exception("failed converting site_summary")
        return []


def _dataframe_to_match_dicts(matches: Any) -> list[dict[str, Any]]:
    if matches is None:
        return []
    try:
        if hasattr(matches, "empty") and matches.empty:
            return []
        df = matches.copy()
        if "geometry" in getattr(df, "columns", []):
            df = df.drop(columns=["geometry"])
        out = []
        for _, r in df.iterrows():
            d = dict(r)
            out.append(
                {
                    "mine_site_id": _safe_str(d.get("mine_site_id")),
                    "claim_serial_number": _safe_str(d.get("claim_serial_number")),
                    "claim_name": _safe_str(d.get("claim_name")),
                    "match_type": _safe_str(d.get("match_type")),
                    "distance_meters": _safe_float(d.get("distance_meters")),
                    "total_score": _safe_float(d.get("total_score")),
                    "activity_score": _safe_float(d.get("activity_score")),
                    "claim_match_score": _safe_float(d.get("claim_match_score")),
                    "confidence_category": _safe_str(d.get("confidence_category")),
                    "score_breakdown_json": d.get("score_breakdown_json"),
                    "evidence_summary_json": d.get("evidence_summary_json"),
                }
            )
        return out
    except Exception:
        log.exception("failed converting matches")
        return []


def run_pull(
    account_id: int,
    state: str,
    *,
    refresh: bool = True,
    fixture_dir: Path | None = None,
    use_network_plss: bool = True,
) -> dict[str, Any]:
    """
    Re-run the live matcher methodology for NV|UT, persist candidates,
    bridge PLSS, and resolve/create section Targets.
    """
    if not active_mines_enabled():
        return {"ok": False, "error": "Active Mine Search API is disabled."}
    state = state.upper().strip()
    if state not in SUPPORTED_STATES:
        return {"ok": False, "error": f"Unsupported state {state!r}. Use NV or UT."}

    run_id = store.create_run(account_id, state, refresh=refresh)
    try:
        from mining_os.active_mine_intel.matcher.config import Paths, get_config
        from mining_os.active_mine_intel.matcher.models import FatalPipelineError
        from mining_os.active_mine_intel.matcher.pipeline import run_pipeline

        paths = Paths()
        paths.ensure("Nevada" if state == "NV" else "Utah")
        result = run_pipeline(
            state,
            refresh=refresh,
            use_cache=not refresh,
            fixture_dir=fixture_dir,
            paths=paths,
            plss_use_network=False,
        )
        sites = _dataframe_to_site_dicts(result.site_summary, state)
        match_rows = _dataframe_to_match_dicts(result.matches)

        linked = 0
        unresolved = 0
        created = 0
        reused = 0
        enriched: list[dict[str, Any]] = []

        for site in sites:
            plss = resolve_site_plss(
                latitude=site.get("latitude"),
                longitude=site.get("longitude"),
                state_abbr=state,
                matcher_row=site,
                use_network=False,
            )
            site.update(
                {
                    "location_plss": plss.get("location_plss"),
                    "township": plss.get("township"),
                    "range": plss.get("range"),
                    "section": plss.get("section"),
                    "meridian": plss.get("meridian"),
                    "plss_normalized": plss.get("plss_normalized"),
                    "plss_source": plss.get("plss_source"),
                    "plss_status": plss.get("plss_status") or "unresolved",
                    "area_of_focus_id": None,
                }
            )
            if site["plss_status"] == "resolved" and site.get("plss_normalized"):
                try:
                    aof_id, was_created = resolve_or_create_section_target(
                        account_id,
                        plss=plss,
                        mine_name=site.get("name"),
                        latitude=site.get("latitude"),
                        longitude=site.get("longitude"),
                        commodity=site.get("commodity"),
                    )
                    site["area_of_focus_id"] = aof_id
                    linked += 1
                    if was_created:
                        created += 1
                    else:
                        reused += 1
                except Exception as exc:
                    log.warning("target link failed for %s: %s", site.get("mine_site_id"), exc)
                    site["plss_status"] = "unresolved"
                    unresolved += 1
            else:
                unresolved += 1
            enriched.append(site)

        store.replace_candidates_for_state(account_id, state, run_id, enriched, match_rows)
        manifest = result.manifest("mining-os-active-mines")
        store.update_run(
            run_id,
            status=result.status if result.status in ("success", "partial") else "success",
            finished_at=datetime.now(timezone.utc),
            matcher_run_id=result.run_id,
            site_count=len(enriched),
            linked_count=linked,
            unresolved_plss=unresolved,
            targets_created=created,
            targets_reused=reused,
            qc_json=result.qc or result.counts or {},
            manifest_json=manifest,
            error_message=None,
        )
        return {
            "ok": True,
            "run_id": run_id,
            "status": result.status,
            "site_count": len(enriched),
            "linked_count": linked,
            "unresolved_plss": unresolved,
            "targets_created": created,
            "targets_reused": reused,
            "degraded_mode": bool(result.degraded_mode),
            "warnings": list(result.warnings or []),
            "matcher_run_id": result.run_id,
        }
    except Exception as exc:
        log.exception("active mine pull failed")
        store.update_run(
            run_id,
            status="failed",
            finished_at=datetime.now(timezone.utc),
            error_message=str(exc),
        )
        return {"ok": False, "run_id": run_id, "error": str(exc)}


def start_pull_async(account_id: int, state: str, refresh: bool = True) -> dict[str, Any]:
    """Start pull in a background thread; returns run_id immediately after row create."""
    if not active_mines_enabled():
        return {"ok": False, "error": "Active Mine Search API is disabled."}
    state = state.upper().strip()
    if state not in SUPPORTED_STATES:
        return {"ok": False, "error": f"Unsupported state {state!r}. Use NV or UT."}

    # If a pull is already in flight, reattach instead of erroring.
    if not _pull_lock.acquire(blocking=False):
        existing = _active_pull.get("run_id") or (
            (store.get_latest_run(account_id, state=state, running_only=True) or {}).get("id")
        )
        if existing:
            return {
                "ok": True,
                "run_id": str(existing),
                "status": "running",
                "already_running": True,
                "message": "A pull is already in progress — showing live status.",
            }
        # Lock held but no known run (stale) — fall through after a short wait is unsafe;
        # report clearly so the UI can poll latest.
        latest = store.get_latest_run(account_id, state=state, running_only=True)
        if latest:
            return {
                "ok": True,
                "run_id": latest["id"],
                "status": "running",
                "already_running": True,
                "message": "A pull is already in progress — showing live status.",
            }
        return {
            "ok": False,
            "error": "A pull lock is held but no active run was found. Wait a moment and retry.",
        }

    # Clear orphaned "running" rows from a prior crashed process (lock was free).
    orphan = store.get_latest_run(account_id, state=state, running_only=True)
    if orphan:
        store.update_run(
            orphan["id"],
            status="failed",
            finished_at=datetime.now(timezone.utc),
            error_message="Superseded: previous pull did not finish (server restart or crash).",
        )

    run_id = store.create_run(account_id, state, refresh=refresh)
    _active_pull.update({"run_id": run_id, "account_id": account_id, "state": state})
    _progress(run_id, 0, f"Starting Active Mine Search pull for {state}…")

    def _worker() -> None:
        try:
            _run_pull_into(run_id, account_id, state, refresh=refresh)
        finally:
            _active_pull.clear()
            _pull_lock.release()

    threading.Thread(target=_worker, name=f"ami-pull-{state}", daemon=True).start()
    return {
        "ok": True,
        "run_id": run_id,
        "status": "running",
        "already_running": False,
        "message": "Pull started — fetching live BLM / MSHA / state sources.",
    }


def _run_pull_into(run_id: str, account_id: int, state: str, refresh: bool = True) -> dict[str, Any]:
    """Same as run_pull but uses a pre-created run row."""
    try:
        from mining_os.active_mine_intel.matcher.config import Paths
        from mining_os.active_mine_intel.matcher.pipeline import run_pipeline

        paths = Paths()
        paths.ensure("Nevada" if state == "NV" else "Utah")
        result = run_pipeline(
            state,
            refresh=refresh,
            use_cache=not refresh,
            paths=paths,
            progress_cb=_pipeline_progress_adapter(run_id),
            # CSE_META / claim attributes only — CadNSDI-per-mine is too slow for Pull UX.
            plss_use_network=False,
        )
        sites = _dataframe_to_site_dicts(result.site_summary, state)
        match_rows = _dataframe_to_match_dicts(result.matches)

        _progress(
            run_id,
            17,
            f"Resolving PLSS for {len(sites)} mines (claim metadata)…",
            site_count=len(sites),
        )

        linked = unresolved = created = reused = 0
        enriched: list[dict[str, Any]] = []
        for i, site in enumerate(sites, start=1):
            if i == 1 or i % 50 == 0 or i == len(sites):
                _progress(
                    run_id,
                    17,
                    f"Resolving PLSS / Targets ({i}/{len(sites)})…",
                    linked=linked,
                    unresolved=unresolved,
                    done=i,
                    total=len(sites),
                )
            # Fast path: use matcher PLSS / CSE_META. Network CadNSDI is opt-in elsewhere.
            plss = resolve_site_plss(
                latitude=site.get("latitude"),
                longitude=site.get("longitude"),
                state_abbr=state,
                matcher_row=site,
                use_network=False,
            )
            site.update(
                {
                    "location_plss": plss.get("location_plss"),
                    "township": plss.get("township"),
                    "range": plss.get("range"),
                    "section": plss.get("section"),
                    "meridian": plss.get("meridian"),
                    "plss_normalized": plss.get("plss_normalized"),
                    "plss_source": plss.get("plss_source"),
                    "plss_status": plss.get("plss_status") or "unresolved",
                    "area_of_focus_id": None,
                }
            )
            if site["plss_status"] == "resolved" and site.get("plss_normalized"):
                try:
                    aof_id, was_created = resolve_or_create_section_target(
                        account_id,
                        plss=plss,
                        mine_name=site.get("name"),
                        latitude=site.get("latitude"),
                        longitude=site.get("longitude"),
                        commodity=site.get("commodity"),
                    )
                    site["area_of_focus_id"] = aof_id
                    linked += 1
                    created += int(was_created)
                    reused += int(not was_created)
                except Exception as exc:
                    log.warning("target link failed for %s: %s", site.get("mine_site_id"), exc)
                    site["plss_status"] = "unresolved"
                    unresolved += 1
            else:
                unresolved += 1
            enriched.append(site)

        _progress(run_id, 18, f"Saving {len(enriched)} candidate sites…", site_count=len(enriched))
        store.replace_candidates_for_state(account_id, state, run_id, enriched, match_rows)
        # Update counts early so UI / list can show results even before final status write.
        store.update_run(
            run_id,
            site_count=len(enriched),
            linked_count=linked,
            unresolved_plss=unresolved,
            targets_created=created,
            targets_reused=reused,
        )
        _progress(
            run_id,
            19,
            f"Done — {len(enriched)} sites, {linked} linked to Targets",
            site_count=len(enriched),
            linked=linked,
        )
        store.update_run(
            run_id,
            status=result.status if result.status in ("success", "partial") else "success",
            finished_at=datetime.now(timezone.utc),
            matcher_run_id=result.run_id,
            site_count=len(enriched),
            linked_count=linked,
            unresolved_plss=unresolved,
            targets_created=created,
            targets_reused=reused,
            qc_json={
                **(result.qc or result.counts or {}),
                "progress": {
                    "stage": 20,
                    "total_stages": JOB_STAGE_TOTAL,
                    "percent": 100,
                    "message": f"Complete — {len(enriched)} sites",
                },
            },
            manifest_json=result.manifest("mining-os-active-mines"),
        )
        return {"ok": True, "run_id": run_id}
    except Exception as exc:
        log.exception("active mine async pull failed")
        store.update_run(
            run_id,
            status="failed",
            finished_at=datetime.now(timezone.utc),
            error_message=str(exc),
        )
        try:
            _progress(run_id, JOB_STAGE_TOTAL, f"Failed: {exc}")
        except Exception:
            pass
        return {"ok": False, "run_id": run_id, "error": str(exc)}


def _rollup_from_characteristics(chars: Any) -> tuple[int, int, int, int, str]:
    """Return (mlrs_total, unpaid_count, paid_count, unknown_count, rollup_status)."""
    from mining_os.active_mine_intel.claim_rollup import rollup_from_characteristics

    out = rollup_from_characteristics(chars)
    if out is None:
        return 0, 0, 0, 0, "unknown"
    return out


def _per_target_timeout_sec() -> int:
    raw = (os.getenv("MINING_OS_FETCH_UNPAID_TARGET_TIMEOUT_SEC") or "").strip()
    if not raw:
        return _DEFAULT_PER_TARGET_TIMEOUT_SEC
    try:
        return max(60, min(int(raw), 45 * 60))
    except ValueError:
        return _DEFAULT_PER_TARGET_TIMEOUT_SEC


def _fetch_area_process_main(area_id: int, account_id: int, result_path: str) -> None:
    """Child process entry: run Fetch Claim Records and write JSON result."""
    import json
    from pathlib import Path as _Path

    from mining_os.services.fetch_claim_records import run_fetch_claim_records_for_area_id

    try:
        out = run_fetch_claim_records_for_area_id(area_id, account_id=account_id)
    except Exception as exc:  # pragma: no cover - defensive
        out = {"ok": False, "error": str(exc), "claims": [], "log": ""}
    try:
        _Path(result_path).write_text(json.dumps(out), encoding="utf-8")
    except Exception:
        pass


def _run_fetch_area_with_timeout(
    area_id: int,
    account_id: int,
    *,
    timeout_sec: int,
    on_tick: Any | None = None,
) -> dict[str, Any]:
    """Run one Target fetch in a child process; kill and return error on timeout."""
    import json
    import tempfile
    from pathlib import Path as _Path

    fd, result_path = tempfile.mkstemp(prefix="ami-fetch-", suffix=".json")
    os.close(fd)
    ctx = mp.get_context("spawn")
    proc = ctx.Process(
        target=_fetch_area_process_main,
        args=(area_id, account_id, result_path),
        name=f"ami-fetch-{area_id}",
        daemon=True,
    )
    started = time.monotonic()
    proc.start()
    try:
        while proc.is_alive():
            elapsed = time.monotonic() - started
            if elapsed >= timeout_sec:
                break
            if on_tick:
                try:
                    on_tick(elapsed)
                except Exception:
                    pass
            proc.join(timeout=min(5.0, max(0.5, timeout_sec - elapsed)))

        if proc.is_alive():
            log.warning(
                "Fetch unpaid Target #%s exceeded %ss — terminating child process",
                area_id,
                timeout_sec,
            )
            proc.terminate()
            proc.join(timeout=8)
            if proc.is_alive():
                proc.kill()
                proc.join(timeout=5)
            return {
                "ok": False,
                "error": (
                    f"Timed out after {timeout_sec}s (payment enrichment / scrape). "
                    "Moved on to the next Target."
                ),
                "claims": [],
                "timed_out": True,
            }

        path = _Path(result_path)
        if not path.exists() or path.stat().st_size == 0:
            return {
                "ok": False,
                "error": "Fetch child exited without a result payload.",
                "claims": [],
            }
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            return {"ok": False, "error": f"Could not read fetch result: {exc}", "claims": []}
    finally:
        try:
            _Path(result_path).unlink(missing_ok=True)
        except Exception:
            pass


def _progress_payload(
    *,
    mine_name: str,
    index: int,
    total: int,
    area_id: int,
    succeeded: int,
    failed: int,
    phase: str,
    elapsed_sec: float | None = None,
    timeout_sec: int | None = None,
) -> dict[str, Any]:
    if phase == "start" and elapsed_sec is not None:
        verb = "Fetching"
        elapsed_bit = f" · {int(elapsed_sec)}s elapsed"
        if timeout_sec:
            elapsed_bit += f" / {timeout_sec}s cap"
    elif phase == "start":
        verb = "Fetching"
        elapsed_bit = ""
    else:
        verb = "Finished"
        elapsed_bit = ""
    msg = (
        f'{verb} mine "{mine_name}" ({index}/{total}) · '
        f"{succeeded} ok / {failed} failed · Target #{area_id}{elapsed_bit}"
    )
    return {
        "progress_message": msg,
        "current_area_id": area_id,
        "current_mine_name": mine_name,
        "current_index": index,
        "total": total,
        "phase": phase,
        "elapsed_sec": int(elapsed_sec) if elapsed_sec is not None else None,
        "timeout_sec": timeout_sec,
    }


def apply_claim_rollup_for_area(
    account_id: int,
    area_id: int,
    *,
    claims_count_hint: int | None = None,
) -> None:
    """Recompute Paid/Unpaid/Unknown on linked Active Mine rows from Target claim_records."""
    from mining_os.services.areas_of_focus import get_area

    area = get_area(int(area_id), account_id=account_id)
    chars = (area or {}).get("characteristics") if area else None
    total, unpaid, paid, unknown, rollup = _rollup_from_characteristics(chars)
    if total == 0 and isinstance(claims_count_hint, int):
        total = int(claims_count_hint)
    store.update_site_claim_rollup(
        account_id,
        int(area_id),
        unpaid_count=unpaid,
        paid_count=paid,
        unknown_count=unknown,
        rollup=rollup,
        mlrs_claim_count=total,
    )


# Back-compat alias used inside this module.
_apply_rollup_for_area = apply_claim_rollup_for_area


def _run_fetch_unpaid_worker(
    job_id: str,
    account_id: int,
    worklist: list[dict[str, Any]],
) -> dict[str, Any]:
    """One Target at a time: scrape → rollup → progress write after each step."""
    all_results: list[dict[str, Any]] = []
    succeeded = failed = 0
    total = len(worklist)
    done_areas: set[int] = set()
    timeout_sec = _per_target_timeout_sec()

    try:
        for i, item in enumerate(worklist, start=1):
            area_id = int(item["area_of_focus_id"])
            mine_name = str(item.get("mine_name") or f"Target #{area_id}")
            store.update_fetch_job(
                job_id,
                processed=len(all_results),
                succeeded=succeeded,
                failed=failed,
                results_json=all_results,
                progress_json=_progress_payload(
                    mine_name=mine_name,
                    index=i,
                    total=total,
                    area_id=area_id,
                    succeeded=succeeded,
                    failed=failed,
                    phase="start",
                    elapsed_sec=0,
                    timeout_sec=timeout_sec,
                ),
            )

            if area_id in done_areas:
                # Should not happen when worklist is unique; still refresh rollup.
                _apply_rollup_for_area(account_id, area_id)
                all_results.append(
                    {
                        "id": area_id,
                        "name": mine_name,
                        "ok": True,
                        "error": None,
                        "claims_count": 0,
                        "skipped_scrape": True,
                    }
                )
                succeeded += 1
            else:
                def _tick(elapsed: float, *, _i=i, _name=mine_name, _aid=area_id) -> None:
                    store.update_fetch_job(
                        job_id,
                        processed=len(all_results),
                        succeeded=succeeded,
                        failed=failed,
                        results_json=all_results,
                        progress_json=_progress_payload(
                            mine_name=_name,
                            index=_i,
                            total=total,
                            area_id=_aid,
                            succeeded=succeeded,
                            failed=failed,
                            phase="start",
                            elapsed_sec=elapsed,
                            timeout_sec=timeout_sec,
                        ),
                    )

                try:
                    out = _run_fetch_area_with_timeout(
                        area_id,
                        account_id,
                        timeout_sec=timeout_sec,
                        on_tick=_tick,
                    )
                except Exception as exc:
                    log.exception("fetch unpaid area_id=%s", area_id)
                    out = {"ok": False, "error": str(exc), "claims": []}
                claims = out.get("claims") or []
                claims_count = len(claims) if isinstance(claims, list) else 0
                # Checkpoint may have saved claims even when the child timed out.
                if claims_count == 0 and out.get("timed_out"):
                    try:
                        from mining_os.services.areas_of_focus import get_area

                        area = get_area(area_id, account_id=account_id)
                        cr = ((area or {}).get("characteristics") or {}).get("claim_records") or {}
                        prior = cr.get("claims") if isinstance(cr, dict) else None
                        if isinstance(prior, list):
                            claims_count = len(prior)
                    except Exception:
                        pass
                ok = bool(out.get("ok"))
                row = {
                    "id": area_id,
                    "name": mine_name,
                    "ok": ok,
                    "error": out.get("error"),
                    "claims_count": claims_count,
                    "timed_out": bool(out.get("timed_out")),
                }
                all_results.append(row)
                if ok:
                    succeeded += 1
                    _apply_rollup_for_area(account_id, area_id, claims_count_hint=claims_count)
                else:
                    failed += 1
                    # Still refresh rollup from whatever is stored (checkpoint / prior scrape).
                    try:
                        _apply_rollup_for_area(account_id, area_id, claims_count_hint=claims_count or None)
                    except Exception:
                        pass
                done_areas.add(area_id)

            store.update_fetch_job(
                job_id,
                processed=len(all_results),
                succeeded=succeeded,
                failed=failed,
                results_json=all_results,
                progress_json=_progress_payload(
                    mine_name=mine_name,
                    index=i,
                    total=total,
                    area_id=area_id,
                    succeeded=succeeded,
                    failed=failed,
                    phase="done",
                    timeout_sec=timeout_sec,
                ),
            )

        status = "success" if failed == 0 else "partial"
        store.update_fetch_job(
            job_id,
            status=status,
            finished_at=datetime.now(timezone.utc),
            processed=len(all_results),
            succeeded=succeeded,
            failed=failed,
            results_json=all_results,
            progress_json={
                "progress_message": (
                    f"Done — {succeeded} ok / {failed} failed of {total} Targets"
                ),
                "current_area_id": None,
                "current_mine_name": None,
                "current_index": total,
                "total": total,
                "phase": "complete",
            },
        )
        return {
            "ok": True,
            "job_id": job_id,
            "target_count": total,
            "processed": len(all_results),
            "succeeded": succeeded,
            "failed": failed,
            "results": all_results,
        }
    except Exception as exc:
        log.exception("fetch unpaid failed")
        store.update_fetch_job(
            job_id,
            status="failed",
            finished_at=datetime.now(timezone.utc),
            error_message=str(exc),
            results_json=all_results,
            progress_json={
                "progress_message": f"Failed: {exc}",
                "phase": "failed",
            },
        )
        return {"ok": False, "job_id": job_id, "error": str(exc), "results": all_results}


def run_fetch_unpaid(
    account_id: int,
    *,
    state: str | None = None,
    site_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Fetch Claim Records one linked Target at a time; denormalize unpaid badges."""
    store.fail_stale_fetch_jobs(
        account_id,
        reason="Superseded by a new Fetch unpaid job.",
    )
    worklist = store.linked_targets_in_site_order(
        account_id, state=state, site_ids=site_ids
    )
    if not worklist:
        return {"ok": False, "error": "No linked Targets found. Pull active mines first.", "results": []}

    target_ids = [int(t["area_of_focus_id"]) for t in worklist]
    job_id = store.create_fetch_job(account_id, target_ids, state)
    return _run_fetch_unpaid_worker(job_id, account_id, worklist)


def start_fetch_unpaid_async(
    account_id: int,
    *,
    state: str | None = None,
    site_ids: list[str] | None = None,
) -> dict[str, Any]:
    if not _fetch_lock.acquire(blocking=False):
        return {"ok": False, "error": "A Fetch unpaid claims job is already running."}

    try:
        # After lock: clear DB zombies (prior process / restart). No live worker
        # for this account can hold the lock, so these rows are orphaned.
        store.fail_stale_fetch_jobs(
            account_id,
            reason="Superseded by a new Fetch unpaid job.",
        )

        worklist = store.linked_targets_in_site_order(
            account_id, state=state, site_ids=site_ids
        )
        if not worklist:
            _fetch_lock.release()
            return {"ok": False, "error": "No linked Targets found. Pull active mines first."}

        target_ids = [int(t["area_of_focus_id"]) for t in worklist]
        job_id = store.create_fetch_job(account_id, target_ids, state)
        store.update_fetch_job(
            job_id,
            progress_json={
                "progress_message": f"Starting — {len(worklist)} linked Targets (mine order)…",
                "current_index": 0,
                "total": len(worklist),
                "phase": "start",
            },
        )
    except Exception:
        _fetch_lock.release()
        raise

    def _worker() -> None:
        try:
            _run_fetch_unpaid_worker(job_id, account_id, worklist)
        finally:
            _fetch_lock.release()

    threading.Thread(target=_worker, name="ami-fetch-unpaid", daemon=True).start()
    return {
        "ok": True,
        "job_id": job_id,
        "status": "running",
        "target_count": len(target_ids),
        "target_ids": target_ids,
    }
