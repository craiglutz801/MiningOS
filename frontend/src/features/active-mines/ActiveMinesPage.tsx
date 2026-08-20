import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { activeMines, api } from "../../api";
import {
  ClaimRecordsMlrsPanel,
  type ClaimRecordsPayload,
} from "../../areas/ClaimRecordsMlrsPanel";

const STATE_STORAGE_KEY = "mining_os.active_mines.state";

function readStoredState(): "NV" | "UT" {
  try {
    const raw = sessionStorage.getItem(STATE_STORAGE_KEY);
    if (raw === "NV" || raw === "UT") return raw;
  } catch {
    /* ignore */
  }
  return "NV";
}

function writeStoredState(next: "NV" | "UT") {
  try {
    sessionStorage.setItem(STATE_STORAGE_KEY, next);
  } catch {
    /* ignore */
  }
}

type SiteRow = {
  id: string;
  mine_site_id: string;
  rank?: number | null;
  name?: string | null;
  county?: string | null;
  total_score?: number | null;
  confidence_category?: string | null;
  activity_label?: string | null;
  location_plss?: string | null;
  plss_status?: string | null;
  best_claim_serial?: string | null;
  best_distance_meters?: number | null;
  area_of_focus_id?: number | null;
  unpaid_claim_count?: number | null;
  paid_claim_count?: number | null;
  unknown_claim_count?: number | null;
  mlrs_claim_count?: number | null;
  claim_status_rollup?: string | null;
  claim_count?: number | null;
  claims_fetched_at?: string | null;
};

function claimRecordsFromSite(site: Record<string, unknown> | null | undefined): ClaimRecordsPayload | null {
  if (!site) return null;
  const target = site.target as Record<string, unknown> | undefined;
  let chars = target?.characteristics as Record<string, unknown> | string | undefined;
  if (typeof chars === "string") {
    try {
      chars = JSON.parse(chars) as Record<string, unknown>;
    } catch {
      return null;
    }
  }
  const cr = chars && typeof chars === "object" ? chars.claim_records : null;
  if (cr && typeof cr === "object") return cr as ClaimRecordsPayload;
  return null;
}

type RunInfo = {
  id: string;
  status: string;
  state_abbr?: string;
  site_count?: number;
  linked_count?: number;
  unresolved_plss?: number;
  targets_created?: number;
  targets_reused?: number;
  error_message?: string | null;
  progress_percent?: number | null;
  progress_message?: string | null;
  progress_stage?: number | null;
  progress_total?: number | null;
  progress?: {
    percent?: number;
    message?: string;
    stage?: number;
    total_stages?: number;
    detail?: Record<string, unknown>;
  } | null;
};

function runProgressPercent(run: RunInfo | null): number {
  if (!run) return 0;
  const p =
    run.progress_percent ??
    run.progress?.percent ??
    (run.progress_stage != null && run.progress_total
      ? Math.round((100 * run.progress_stage) / run.progress_total)
      : null);
  if (p != null) return Math.max(0, Math.min(100, Number(p)));
  if (["success", "partial"].includes(run.status)) return 100;
  if (run.status === "failed") return 100;
  return 5;
}

function runProgressMessage(run: RunInfo | null): string {
  if (!run) return "";
  return (
    run.progress_message ||
    run.progress?.message ||
    (run.status === "running" ? "Working…" : run.status)
  );
}

function Spinner() {
  return (
    <span
      className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-slate-800"
      aria-hidden
    />
  );
}

function scoreClass(score?: number | null) {
  if (score == null) return "text-slate-500";
  if (score >= 85) return "text-emerald-700 font-semibold";
  if (score >= 70) return "text-teal-700 font-semibold";
  if (score >= 55) return "text-amber-700";
  return "text-slate-500";
}

function unpaidBadge(row: SiteRow) {
  if (row.claims_fetched_at == null && row.unpaid_claim_count == null && row.mlrs_claim_count == null) {
    return <span className="text-xs text-slate-400">—</span>;
  }
  const n = row.unpaid_claim_count ?? 0;
  if (n > 0) {
    return (
      <span className="inline-flex items-center rounded px-1.5 py-0.5 text-xs font-medium bg-rose-100 text-rose-800 tabular-nums">
        {n}
      </span>
    );
  }
  return <span className="text-xs text-slate-600 tabular-nums">{n}</span>;
}

function paidBadge(row: SiteRow) {
  if (row.claims_fetched_at == null && row.paid_claim_count == null && row.mlrs_claim_count == null) {
    return <span className="text-xs text-slate-400">—</span>;
  }
  const n = row.paid_claim_count ?? 0;
  if (n > 0) {
    return (
      <span className="inline-flex items-center rounded px-1.5 py-0.5 text-xs font-medium bg-emerald-100 text-emerald-800 tabular-nums">
        {n}
      </span>
    );
  }
  return <span className="text-xs text-slate-600 tabular-nums">{n}</span>;
}

function unknownBadge(row: SiteRow) {
  if (
    row.claims_fetched_at == null &&
    row.unknown_claim_count == null &&
    row.mlrs_claim_count == null
  ) {
    return <span className="text-xs text-slate-400">—</span>;
  }
  const n = row.unknown_claim_count ?? 0;
  if (n > 0) {
    return (
      <span className="inline-flex items-center rounded px-1.5 py-0.5 text-xs font-medium bg-slate-200 text-slate-700 tabular-nums">
        {n}
      </span>
    );
  }
  return <span className="text-xs text-slate-600 tabular-nums">{n}</span>;
}

function claimsTotalCell(row: SiteRow) {
  if (row.mlrs_claim_count == null && !row.claims_fetched_at) {
    return <span className="text-xs text-slate-400">—</span>;
  }
  return (
    <span className="tabular-nums text-slate-800">
      {row.mlrs_claim_count ?? 0}
    </span>
  );
}

export function ActiveMinesPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [state, setState] = useState<"NV" | "UT">(() => {
    const fromUrl = (searchParams.get("state") || "").toUpperCase();
    if (fromUrl === "NV" || fromUrl === "UT") return fromUrl;
    return readStoredState();
  });
  const [includeLow, setIncludeLow] = useState(false);
  const [minScore, setMinScore] = useState(55);
  const [unpaidOnly, setUnpaidOnly] = useState(false);
  const [search, setSearch] = useState("");
  const [sites, setSites] = useState<SiteRow[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pullBusy, setPullBusy] = useState(false);
  const [fetchBusy, setFetchBusy] = useState(false);
  const [run, setRun] = useState<RunInfo | null>(null);
  const [fetchJob, setFetchJob] = useState<{
    id: string;
    status: string;
    processed?: number;
    succeeded?: number;
    failed?: number;
    target_ids?: number[];
    progress_json?: {
      progress_message?: string;
      current_area_id?: number | null;
      current_mine_name?: string | null;
      current_index?: number;
      total?: number;
      phase?: string;
    } | null;
    error_message?: string | null;
  } | null>(null);
  const [selected, setSelected] = useState<Record<string, unknown> | null>(null);
  const [fetchClaimsBusy, setFetchClaimsBusy] = useState(false);
  const [rawJsonOpen, setRawJsonOpen] = useState<ClaimRecordsPayload | null>(null);
  /** Inline MLRS table under list rows (populated by Fetch unpaid / open). */
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [claimsBySite, setClaimsBySite] = useState<Record<string, ClaimRecordsPayload | null>>({});
  const [claimsLoadingId, setClaimsLoadingId] = useState<string | null>(null);
  const selectedRef = useRef(selected);
  selectedRef.current = selected;
  const expandedIdRef = useRef(expandedId);
  expandedIdRef.current = expandedId;

  // Persist the user's state choice only — never overwrite from pull results.
  useEffect(() => {
    writeStoredState(state);
    const current = (searchParams.get("state") || "").toUpperCase();
    if (current === state) return;
    const next = new URLSearchParams(searchParams);
    next.set("state", state);
    setSearchParams(next, { replace: true });
    // Intentionally omit searchParams from deps to avoid replace loops; we only
    // push when our React state changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state, setSearchParams]);

  function onStateChange(next: "NV" | "UT") {
    if (next === state) return;
    setState(next);
    // Drop in-flight run UI for the previous state; list reload follows via loadSites deps.
    setRun(null);
    setPullBusy(false);
    setFetchJob(null);
    setFetchBusy(false);
  }

  const linkedCount = useMemo(
    () => sites.filter((s) => s.area_of_focus_id).length,
    [sites]
  );

  const loadSites = useCallback(async (opts?: { quiet?: boolean }) => {
    if (!opts?.quiet) setLoading(true);
    setError(null);
    try {
      const res = await activeMines.list({
        state,
        min_score: minScore,
        include_low: includeLow,
        unpaid_only: unpaidOnly,
        search: search.trim() || undefined,
        page_size: 200,
      });
      if (!res.ok && res.error) {
        setError(String(res.error));
        setSites([]);
        setTotal(0);
      } else {
        setSites((res.sites as SiteRow[]) || []);
        setTotal(Number(res.total) || 0);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      if (!opts?.quiet) setLoading(false);
    }
  }, [state, minScore, includeLow, unpaidOnly, search]);

  useEffect(() => {
    void loadSites();
  }, [loadSites]);

  // Resume an in-flight pull when opening the page / changing state.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await activeMines.latestRun({ state, running_only: true });
        if (cancelled || !res.ok || !res.run) return;
        const latest = res.run as RunInfo;
        if (["running", "pending"].includes(String(latest.status))) {
          setRun(latest);
          setPullBusy(true);
          setError(null);
        }
      } catch {
        /* ignore */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [state]);

  useEffect(() => {
    if (!run || !["running", "pending"].includes(run.status)) return;
    const t = window.setInterval(async () => {
      try {
        const res = await activeMines.getRun(run.id);
        if (res.ok && res.run) {
          const next = res.run as RunInfo;
          setRun(next);
          if (!["running", "pending"].includes(next.status)) {
            setPullBusy(false);
            void loadSites();
          }
        }
      } catch {
        /* ignore poll errors */
      }
    }, 1500);
    return () => window.clearInterval(t);
  }, [run, loadSites]);

  const fetchJobId = fetchJob?.id;
  const fetchJobStatus = fetchJob?.status;

  useEffect(() => {
    if (!fetchJobId || !fetchJobStatus || !["running", "pending"].includes(fetchJobStatus)) {
      return;
    }
    let lastProcessed = -1;
    let lastMessage = "";
    const t = window.setInterval(async () => {
      try {
        const res = await activeMines.getFetchJob(fetchJobId);
        if (!res.ok || !res.job) return;
        const next = res.job as NonNullable<typeof fetchJob>;
        setFetchJob(next);
        const processed = Number(next.processed ?? 0);
        const msg = String(next.progress_json?.progress_message || "");
        const progressed = processed !== lastProcessed || msg !== lastMessage;
        if (progressed) {
          lastProcessed = processed;
          lastMessage = msg;
          void loadSites({ quiet: true });
          const cur = selectedRef.current;
          const siteId = String(cur?.id || cur?.mine_site_id || "");
          const expandId = expandedIdRef.current;
          const finishedArea = next.progress_json?.current_area_id;
          const reloadIds = new Set<string>();
          if (siteId) reloadIds.add(siteId);
          if (expandId) reloadIds.add(expandId);
          for (const id of reloadIds) {
            void activeMines.get(id).then((r) => {
              if (!r.ok || !r.site) return;
              const site = r.site as Record<string, unknown>;
              const siteArea = site.area_of_focus_id != null ? Number(site.area_of_focus_id) : null;
              // Refresh open detail / expand when this Target just finished (or always while running).
              if (
                finishedArea == null ||
                siteArea == null ||
                siteArea === Number(finishedArea) ||
                next.progress_json?.phase === "done"
              ) {
                if (id === siteId) setSelected(site);
                const cr = claimRecordsFromSite(site);
                setClaimsBySite((prev) => ({ ...prev, [id]: cr }));
              }
            });
          }
        }
        if (!["running", "pending"].includes(String(next.status))) {
          setFetchBusy(false);
          setClaimsBySite({});
          void loadSites({ quiet: true });
        }
      } catch {
        /* ignore */
      }
    }, 2000);
    return () => window.clearInterval(t);
    // Poll only while a job is active; identity is fetchJobId + status gate.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fetchJobId, fetchJobStatus, loadSites]);

  async function loadClaimsForSite(siteId: string) {
    setClaimsLoadingId(siteId);
    try {
      const res = await activeMines.get(siteId);
      if (res.ok && res.site) {
        const cr = claimRecordsFromSite(res.site as Record<string, unknown>);
        setClaimsBySite((prev) => ({ ...prev, [siteId]: cr }));
        return cr;
      }
      setClaimsBySite((prev) => ({ ...prev, [siteId]: null }));
      return null;
    } catch {
      setClaimsBySite((prev) => ({ ...prev, [siteId]: null }));
      return null;
    } finally {
      setClaimsLoadingId(null);
    }
  }

  async function toggleExpand(row: SiteRow, e: { stopPropagation: () => void }) {
    e.stopPropagation();
    if (expandedId === row.id) {
      setExpandedId(null);
      return;
    }
    setExpandedId(row.id);
    if (!(row.id in claimsBySite)) {
      await loadClaimsForSite(row.id);
    }
  }

  async function onPull() {
    setPullBusy(true);
    setError(null);
    try {
      const res = await activeMines.pull({ state, refresh: true });
      if (!res.ok) {
        setError(String(res.error || "Pull failed"));
        setPullBusy(false);
        return;
      }
      setRun({
        id: String(res.run_id),
        status: "running",
        progress_percent: res.already_running ? undefined : 2,
        progress_message:
          res.message ||
          (res.already_running
            ? "A pull is already in progress…"
            : "Pull started — fetching live sources…"),
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setPullBusy(false);
    }
  }

  async function onFetchUnpaid() {
    setFetchBusy(true);
    setError(null);
    setFetchJob(null);
    setClaimsBySite({});
    setExpandedId(null);
    try {
      const res = await activeMines.fetchUnpaid({ state });
      if (!res.ok) {
        setError(String(res.error || "Fetch unpaid failed"));
        setFetchBusy(false);
        return;
      }
      setFetchJob({
        id: String(res.job_id),
        status: "running",
        target_ids: Array.isArray(res.target_ids)
          ? (res.target_ids as number[])
          : res.target_count
            ? Array.from({ length: Number(res.target_count) }, (_, i) => i)
            : [],
        processed: 0,
        succeeded: 0,
        failed: 0,
        progress_json: {
          progress_message: `Starting — ${Number(res.target_count) || 0} linked Targets…`,
          current_index: 0,
          total: Number(res.target_count) || 0,
          phase: "start",
        },
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setFetchBusy(false);
    }
  }

  async function openDetail(row: SiteRow) {
    try {
      const res = await activeMines.get(row.id);
      if (res.ok && res.site) {
        const site = res.site as Record<string, unknown>;
        setSelected(site);
        const cr = claimRecordsFromSite(site);
        setClaimsBySite((prev) => ({ ...prev, [row.id]: cr }));
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function fetchClaimsForSelectedTarget() {
    const areaId = selected?.area_of_focus_id;
    if (areaId == null) return;
    setFetchClaimsBusy(true);
    setError(null);
    try {
      const result = await api.areas.fetchClaimRecords(Number(areaId));
      if (!result.ok && result.error) {
        setError(String(result.error));
      }
      // Reload site detail so target.characteristics.claim_records is fresh.
      const siteId = String(selected?.id || selected?.mine_site_id || "");
      if (siteId) {
        const res = await activeMines.get(siteId);
        if (res.ok && res.site) setSelected(res.site as Record<string, unknown>);
      }
      void loadSites();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setFetchClaimsBusy(false);
    }
  }

  const selectedClaimRecords = useMemo(
    () => claimRecordsFromSite(selected),
    [selected]
  );

  return (
    <div className="p-6 max-w-[1400px] mx-auto space-y-4">
      <header className="space-y-1">
        <h1 className="text-2xl font-semibold text-slate-900">Active Mine Search</h1>
        <p className="text-sm text-slate-600 max-w-3xl">
          Research prioritization for active mines on unpatented claims (Nevada &amp; Utah).
          This is not a title opinion. Every pull regenerates the list from live BLM / MSHA /
          state sources — it does not import a static CSV.
        </p>
      </header>

      <div className="flex flex-wrap items-end gap-3 rounded-lg border border-slate-200 bg-white p-4">
        <label className="text-sm">
          <span className="block text-slate-500 mb-1">State</span>
          <select
            className="border border-slate-300 rounded px-2 py-1.5 text-sm"
            value={state}
            onChange={(e) => onStateChange(e.target.value as "NV" | "UT")}
            disabled={pullBusy || fetchBusy}
          >
            <option value="NV">Nevada</option>
            <option value="UT">Utah</option>
          </select>
        </label>
        <button
          type="button"
          onClick={() => void onPull()}
          disabled={pullBusy}
          className="inline-flex items-center gap-2 rounded bg-slate-900 text-white text-sm px-3 py-1.5 disabled:opacity-50"
        >
          {pullBusy && <Spinner />}
          {pullBusy ? "Pull in progress…" : "Pull active mines on unpatented claims"}
        </button>
        <button
          type="button"
          onClick={() => void onFetchUnpaid()}
          disabled={fetchBusy || linkedCount === 0 || pullBusy}
          className="inline-flex items-center gap-2 rounded border border-slate-300 bg-white text-sm px-3 py-1.5 disabled:opacity-50"
          title={
            linkedCount === 0
              ? "Pull first so mines link to PLSS Targets"
              : "Walks each linked mine in list order; scrapes each unique Target once (shared PLSS Targets are not re-fetched)"
          }
        >
          {fetchBusy && <Spinner />}
          {fetchBusy ? "Fetching unpaid…" : "Fetch unpaid claims (each linked mine)"}
        </button>
        <button
          type="button"
          onClick={() => void loadSites()}
          disabled={pullBusy}
          className="rounded border border-slate-200 text-sm px-3 py-1.5 text-slate-600 disabled:opacity-50"
        >
          Refresh list
        </button>
      </div>

      {run && (
        <div
          className={`rounded-lg border px-4 py-3 space-y-2 ${
            run.status === "failed"
              ? "border-rose-200 bg-rose-50"
              : ["running", "pending"].includes(run.status)
                ? "border-teal-200 bg-teal-50/60"
                : "border-slate-200 bg-white"
          }`}
        >
          <div className="flex items-start justify-between gap-3">
            <div className="flex items-center gap-2 text-sm font-medium text-slate-900">
              {["running", "pending"].includes(run.status) && <Spinner />}
              <span>
                {["running", "pending"].includes(run.status)
                  ? "Pulling active mines on unpatented claims"
                  : run.status === "failed"
                    ? "Pull failed"
                    : "Pull finished"}
              </span>
            </div>
            <span className="text-sm tabular-nums text-slate-700">
              {runProgressPercent(run)}%
            </span>
          </div>
          <div className="h-2 w-full overflow-hidden rounded-full bg-slate-200">
            <div
              className={`h-full rounded-full transition-all duration-500 ${
                run.status === "failed" ? "bg-rose-500" : "bg-teal-600"
              }`}
              style={{ width: `${runProgressPercent(run)}%` }}
            />
          </div>
          <p className="text-sm text-slate-700">{runProgressMessage(run)}</p>
          <p className="text-xs text-slate-500">
            Run <code>{run.id.slice(0, 8)}</code>
            {run.progress_stage != null && run.progress_total != null && (
              <>
                {" "}
                · stage {run.progress_stage}/{run.progress_total}
              </>
            )}
            {!["running", "pending"].includes(run.status) && run.site_count != null && (
              <>
                {" "}
                · {run.site_count} sites · {run.linked_count ?? 0} linked ·{" "}
                {run.unresolved_plss ?? 0} PLSS unresolved
                {run.targets_created != null && (
                  <>
                    {" "}
                    ({run.targets_created} created / {run.targets_reused ?? 0} reused)
                  </>
                )}
              </>
            )}
            {run.error_message && (
              <span className="text-rose-700"> — {run.error_message}</span>
            )}
          </p>
          {["running", "pending"].includes(run.status) && (
            <p className="text-xs text-slate-500">
              This can take several minutes (live BLM / MSHA / state downloads). You can leave
              this page and come back — progress will resume.
            </p>
          )}
        </div>
      )}

      {fetchJob && (
        <div className="text-sm text-slate-600 rounded-lg border border-slate-200 bg-white px-4 py-3 space-y-1">
          <div className="flex items-center gap-2">
            {fetchBusy && <Spinner />}
            <span>
              {fetchBusy
                ? fetchJob.progress_json?.progress_message ||
                  "Fetching MLRS claim records mine-by-mine…"
                : fetchJob.progress_json?.progress_message || "Fetch unpaid finished"}{" "}
              · <strong>{fetchJob.status}</strong>
              {fetchJob.processed != null && (
                <>
                  {" "}
                  — {fetchJob.processed}
                  {Array.isArray(fetchJob.target_ids) && fetchJob.target_ids.length > 0
                    ? ` / ${fetchJob.target_ids.length}`
                    : fetchJob.progress_json?.total
                      ? ` / ${fetchJob.progress_json.total}`
                      : ""}{" "}
                  ({fetchJob.succeeded ?? 0} ok / {fetchJob.failed ?? 0} failed)
                </>
              )}
            </span>
          </div>
          {fetchBusy && (
            <p className="text-xs text-slate-500">
              Claims / Unpaid update after each Target. Shared PLSS Targets are scraped once.
            </p>
          )}
          {!fetchBusy && ["success", "partial"].includes(fetchJob.status) && (
            <p className="text-xs text-slate-500">
              Claims / Unpaid columns refresh from the MLRS scrape. Expand a mine row (▸) or open
              detail for the full Claim Records table (same as Targets).
            </p>
          )}
          {!fetchBusy && fetchJob.status === "failed" && fetchJob.error_message && (
            <p className="text-xs text-rose-700">{fetchJob.error_message}</p>
          )}
        </div>
      )}

      {error && (
        <div className="rounded border border-rose-200 bg-rose-50 text-rose-800 text-sm px-3 py-2">
          {error}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-3 text-sm">
        <label className="flex items-center gap-1.5">
          Min score
          <input
            type="number"
            className="w-16 border border-slate-300 rounded px-1.5 py-1"
            value={minScore}
            onChange={(e) => setMinScore(Number(e.target.value) || 0)}
          />
        </label>
        <label className="flex items-center gap-1.5">
          <input
            type="checkbox"
            checked={includeLow}
            onChange={(e) => setIncludeLow(e.target.checked)}
          />
          Include weak (LOW)
        </label>
        <label className="flex items-center gap-1.5">
          <input
            type="checkbox"
            checked={unpaidOnly}
            onChange={(e) => setUnpaidOnly(e.target.checked)}
          />
          Unpaid only
        </label>
        <input
          type="search"
          placeholder="Search mine, county, claim, PLSS…"
          className="border border-slate-300 rounded px-2 py-1 min-w-[220px]"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <span className="text-slate-500">
          {loading ? "Loading…" : `${total} site${total === 1 ? "" : "s"}`}
        </span>
      </div>

      <div className="overflow-auto rounded-lg border border-slate-200 bg-white">
        <table className="min-w-full text-sm">
          <thead className="bg-slate-50 text-left text-slate-600">
            <tr>
              <th className="px-2 py-2 font-medium w-8" aria-label="Expand claims" />
              <th className="px-3 py-2 font-medium">#</th>
              <th className="px-3 py-2 font-medium">Score</th>
              <th className="px-3 py-2 font-medium">Conf.</th>
              <th className="px-3 py-2 font-medium">Mine</th>
              <th className="px-3 py-2 font-medium">County</th>
              <th className="px-3 py-2 font-medium">PLSS</th>
              <th className="px-3 py-2 font-medium">Best claim</th>
              <th className="px-3 py-2 font-medium">Dist m</th>
              <th className="px-3 py-2 font-medium">Target</th>
              <th className="px-3 py-2 font-medium">Claims</th>
              <th className="px-3 py-2 font-medium">Paid</th>
              <th className="px-3 py-2 font-medium">Unpaid</th>
              <th className="px-3 py-2 font-medium">Unknown</th>
            </tr>
          </thead>
          <tbody>
            {sites.length === 0 && !loading ? (
              <tr>
                <td colSpan={14} className="px-3 py-8 text-center text-slate-500">
                  No sites yet. Choose a state and pull active mines on unpatented claims.
                </td>
              </tr>
            ) : (
              sites.map((row, idx) => {
                const isExpanded = expandedId === row.id;
                const claims = claimsBySite[row.id];
                const claimsLoading = claimsLoadingId === row.id;
                return (
                  <Fragment key={row.id}>
                    <tr
                      className="border-t border-slate-100 hover:bg-slate-50 cursor-pointer"
                      onClick={() => void openDetail(row)}
                    >
                      <td className="px-2 py-2" onClick={(e) => void toggleExpand(row, e)}>
                        <button
                          type="button"
                          className="text-slate-500 hover:text-slate-800 text-xs w-5 disabled:opacity-30"
                          title={
                            row.area_of_focus_id
                              ? "Show MLRS claim records"
                              : "No linked Target yet"
                          }
                          disabled={!row.area_of_focus_id}
                          aria-expanded={isExpanded}
                        >
                          {isExpanded ? "▾" : "▸"}
                        </button>
                      </td>
                      <td className="px-3 py-2 text-slate-500">{row.rank ?? idx + 1}</td>
                      <td className={`px-3 py-2 ${scoreClass(row.total_score)}`}>
                        {row.total_score != null ? Math.round(row.total_score) : "—"}
                      </td>
                      <td className="px-3 py-2">{row.confidence_category || "—"}</td>
                      <td className="px-3 py-2 font-medium text-slate-900">
                        {row.name || row.mine_site_id}
                      </td>
                      <td className="px-3 py-2">{row.county || "—"}</td>
                      <td className="px-3 py-2">
                        {row.location_plss || (
                          <span className="text-amber-700 text-xs">
                            {row.plss_status === "unresolved" ? "unresolved" : "—"}
                          </span>
                        )}
                      </td>
                      <td className="px-3 py-2 font-mono text-xs">
                        {row.best_claim_serial || "—"}
                      </td>
                      <td className="px-3 py-2">
                        {row.best_distance_meters != null
                          ? Math.round(row.best_distance_meters)
                          : "—"}
                      </td>
                      <td className="px-3 py-2" onClick={(e) => e.stopPropagation()}>
                        {row.area_of_focus_id ? (
                          <Link
                            className="text-teal-700 hover:underline"
                            to={`/areas?areaId=${row.area_of_focus_id}`}
                          >
                            #{row.area_of_focus_id}
                          </Link>
                        ) : (
                          <span className="text-slate-400">—</span>
                        )}
                      </td>
                      <td className="px-3 py-2">{claimsTotalCell(row)}</td>
                      <td className="px-3 py-2">{paidBadge(row)}</td>
                      <td className="px-3 py-2">{unpaidBadge(row)}</td>
                      <td className="px-3 py-2">{unknownBadge(row)}</td>
                    </tr>
                    {isExpanded && (
                      <tr className="border-t border-emerald-100 bg-emerald-50/20">
                        <td colSpan={14} className="px-3 py-3">
                          {claimsLoading ? (
                            <div className="flex items-center gap-2 text-xs text-slate-500 py-2">
                              <Spinner /> Loading claim records…
                            </div>
                          ) : claims ? (
                            <ClaimRecordsMlrsPanel
                              claimRecords={claims}
                              subtitle={
                                row.area_of_focus_id
                                  ? `From linked Target #${row.area_of_focus_id} (section PLSS)`
                                  : null
                              }
                              onViewRaw={() => setRawJsonOpen(claims)}
                            />
                          ) : (
                            <div className="rounded-lg border border-dashed border-emerald-200 bg-white px-3 py-3 text-xs text-slate-600">
                              {row.claims_fetched_at
                                ? "No MLRS claims stored on this Target (fetch returned empty)."
                                : "No MLRS claim records yet. Run Fetch unpaid claims for this state, or Fetch Claim Records on the linked Target."}
                            </div>
                          )}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      {selected && (
        <div
          className="fixed inset-0 z-40 flex justify-end bg-black/30"
          onClick={() => {
            setSelected(null);
            setRawJsonOpen(null);
          }}
        >
          <aside
            className="w-full max-w-3xl h-full bg-white shadow-xl overflow-auto p-5 space-y-3"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-start justify-between gap-2">
              <h2 className="text-lg font-semibold">
                {String(selected.name || selected.mine_site_id)}
              </h2>
              <button
                type="button"
                className="text-slate-500 text-sm"
                onClick={() => {
                  setSelected(null);
                  setRawJsonOpen(null);
                }}
              >
                Close
              </button>
            </div>
            <dl className="grid grid-cols-2 gap-2 text-sm">
              <div>
                <dt className="text-slate-500">Score</dt>
                <dd>{selected.total_score != null ? String(selected.total_score) : "—"}</dd>
              </div>
              <div>
                <dt className="text-slate-500">Confidence</dt>
                <dd>{String(selected.confidence_category || "—")}</dd>
              </div>
              <div>
                <dt className="text-slate-500">Activity</dt>
                <dd>{String(selected.activity_label || "—")}</dd>
              </div>
              <div>
                <dt className="text-slate-500">PLSS</dt>
                <dd>{String(selected.location_plss || selected.plss_status || "—")}</dd>
              </div>
              <div className="col-span-2">
                <dt className="text-slate-500">Best claim</dt>
                <dd className="font-mono text-xs">{String(selected.best_claim_serial || "—")}</dd>
              </div>
              <div className="col-span-2">
                <dt className="text-slate-500">Next action</dt>
                <dd>{String(selected.recommended_next_action || "—")}</dd>
              </div>
            </dl>

            {selected.area_of_focus_id ? (
              <div className="flex flex-wrap items-center gap-3 text-sm">
                <span>
                  Linked Target{" "}
                  <Link
                    className="text-teal-700 hover:underline"
                    to={`/areas?areaId=${selected.area_of_focus_id}`}
                  >
                    #{String(selected.area_of_focus_id)}
                  </Link>
                </span>
                <button
                  type="button"
                  disabled={fetchClaimsBusy}
                  onClick={() => void fetchClaimsForSelectedTarget()}
                  className="rounded border border-slate-300 bg-white px-2.5 py-1 text-xs disabled:opacity-50"
                >
                  {fetchClaimsBusy ? "Fetching claims…" : "Fetch Claim Records"}
                </button>
              </div>
            ) : (
              <p className="text-xs text-amber-700">
                No PLSS Target linked yet — claim records attach after PLSS resolves and Fetch
                unpaid / Fetch Claim Records runs.
              </p>
            )}

            {selectedClaimRecords ? (
              <ClaimRecordsMlrsPanel
                claimRecords={selectedClaimRecords}
                subtitle={
                  selected.area_of_focus_id
                    ? `From linked Target #${String(selected.area_of_focus_id)} (section PLSS)`
                    : null
                }
                onViewRaw={() => setRawJsonOpen(selectedClaimRecords)}
              />
            ) : selected.area_of_focus_id ? (
              <div className="rounded-lg border border-dashed border-emerald-200 bg-emerald-50/40 px-3 py-3 text-xs text-slate-600">
                No MLRS claim records stored on this Target yet. Click{" "}
                <strong>Fetch Claim Records</strong> above (same action as on Targets).
              </div>
            ) : null}

            {Array.isArray(selected.claim_serials) &&
              (selected.claim_serials as string[]).length > 0 &&
              !selectedClaimRecords && (
                <div>
                  <h3 className="text-sm font-medium text-slate-700 mb-1">
                    Matcher claim serials
                  </h3>
                  <ul className="text-xs font-mono space-y-0.5 max-h-32 overflow-auto">
                    {(selected.claim_serials as string[]).map((s) => (
                      <li key={s}>{s}</li>
                    ))}
                  </ul>
                </div>
              )}

            {selected.score_breakdown_json ? (
              <div>
                <h3 className="text-sm font-medium text-slate-700 mb-1">Score breakdown</h3>
                <pre className="text-xs bg-slate-50 rounded p-2 overflow-auto max-h-48">
                  {JSON.stringify(selected.score_breakdown_json, null, 2)}
                </pre>
              </div>
            ) : null}
          </aside>
        </div>
      )}

      {rawJsonOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
          onClick={() => setRawJsonOpen(null)}
        >
          <div
            className="max-h-[80vh] w-full max-w-2xl overflow-auto rounded-lg bg-white p-4 shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mb-2 flex items-center justify-between gap-2">
              <h3 className="text-sm font-semibold">MLRS Scrape — Raw JSON</h3>
              <button
                type="button"
                className="text-sm text-slate-500"
                onClick={() => setRawJsonOpen(null)}
              >
                Close
              </button>
            </div>
            <pre className="text-[11px] whitespace-pre-wrap break-words">
              {JSON.stringify(rawJsonOpen, null, 2)}
            </pre>
          </div>
        </div>
      )}
    </div>
  );
}
