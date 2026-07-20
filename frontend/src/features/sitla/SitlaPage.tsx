import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { sitla } from "../../api";
import type {
  SitlaCoverage,
  SitlaFilters,
  SitlaOpportunityDetail,
  SitlaOpportunityList,
  SitlaOpportunityRow,
  SitlaSummary,
} from "./types";
import {
  daysUntil,
  formatDate,
  formatMoney,
  healthClass,
  statusLabel,
  tierBadgeClass,
} from "./utils";

type Tab = "opportunities" | "review" | "sources";

const UTAH_COUNTIES = [
  "Beaver",
  "Box Elder",
  "Cache",
  "Carbon",
  "Daggett",
  "Davis",
  "Duchesne",
  "Emery",
  "Garfield",
  "Grand",
  "Iron",
  "Juab",
  "Kane",
  "Millard",
  "Morgan",
  "Piute",
  "Rich",
  "Salt Lake",
  "San Juan",
  "Sanpete",
  "Sevier",
  "Summit",
  "Tooele",
  "Uintah",
  "Utah",
  "Wasatch",
  "Washington",
  "Wayne",
  "Weber",
];

const LIFECYCLE_OPTIONS = [
  "DISCOVERED",
  "ANNOUNCED",
  "NOMINATION_OPEN",
  "NOMINATED",
  "PUBLIC_NOTICE_OPEN",
  "COMPETING_APPLICATION_OPEN",
  "SCHEDULED",
  "BIDDING_OPEN",
  "BIDDING_CLOSED",
  "UNDER_REVIEW",
  "AWARDED",
  "LEASE_EXECUTION_PENDING",
  "LEASE_ACTIVE",
  "NO_BID",
  "NOT_AWARDED",
  "WITHDRAWN",
  "CANCELLED",
  "EXPIRED",
  "REOFFERED",
];

const OPPORTUNITY_TYPE_OPTIONS = [
  "COMPETITIVE_MINERAL_LEASE",
  "OIL_GAS_MINERAL_LEASE",
  "METALLIFEROUS_MINERAL_LEASE",
  "INDUSTRIAL_MINERAL_LEASE",
  "MINERAL_MATERIAL_PERMIT",
  "SAND_GRAVEL_PERMIT",
  "COAL_LEASE",
  "HELIUM_LEASE",
  "LITHIUM_LEASE",
  "POTASH_LEASE",
  "PHOSPHATE_LEASE",
  "GEOTHERMAL_ARRANGEMENT",
  "OTHER_BUSINESS_ARRANGEMENT",
  "COMPETING_APPLICATION_NOTICE",
  "LAND_NOMINATION",
  "REOFFERING",
  "SURFACE_SALE_MINERAL_RELEVANT",
];

const EMPTY_FILTERS: SitlaFilters = {
  search: "",
  county: "",
  status: "",
  opportunity_type: "",
  priority_tier: "",
  active_only: true,
};

function filtersFromParams(sp: URLSearchParams): SitlaFilters {
  return {
    search: sp.get("q") || "",
    county: sp.get("county") || "",
    status: sp.get("status") || "",
    opportunity_type: sp.get("type") || "",
    priority_tier: sp.get("tier") || "",
    active_only: sp.get("active_only") !== "false",
  };
}

function paramsFromFilters(f: SitlaFilters): URLSearchParams {
  const sp = new URLSearchParams();
  if (f.search) sp.set("q", f.search);
  if (f.county) sp.set("county", f.county);
  if (f.status) sp.set("status", f.status);
  if (f.opportunity_type) sp.set("type", f.opportunity_type);
  if (f.priority_tier) sp.set("tier", f.priority_tier);
  if (!f.active_only) sp.set("active_only", "false");
  return sp;
}

function asCommercialTermsList(
  terms: SitlaOpportunityDetail["commercial_terms"]
): Array<Record<string, unknown>> {
  if (!terms) return [];
  if (Array.isArray(terms)) return terms;
  return [terms];
}

function nearestDeadline(row: SitlaOpportunityRow): string | null {
  return row.bidding_end_at || row.application_deadline || row.nomination_deadline || null;
}

export function SitlaPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [tab, setTab] = useState<Tab>((searchParams.get("tab") as Tab) || "opportunities");
  const [filters, setFilters] = useState<SitlaFilters>(() => filtersFromParams(searchParams));
  const [summary, setSummary] = useState<SitlaSummary | null>(null);
  const [list, setList] = useState<SitlaOpportunityList | null>(null);
  const [coverage, setCoverage] = useState<SitlaCoverage | null>(null);
  const [reviewItems, setReviewItems] = useState<Array<Record<string, unknown>>>([]);
  const [selectedId, setSelectedId] = useState<string | null>(searchParams.get("id"));
  const [detail, setDetail] = useState<SitlaOpportunityDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [disabled, setDisabled] = useState(false);
  const [jobsEnabled, setJobsEnabled] = useState(false);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [actionMsg, setActionMsg] = useState<string | null>(null);

  const syncUrl = useCallback(
    (next: SitlaFilters, nextTab: Tab, id: string | null) => {
      const sp = paramsFromFilters(next);
      if (nextTab !== "opportunities") sp.set("tab", nextTab);
      if (id) sp.set("id", id);
      setSearchParams(sp, { replace: true });
    },
    [setSearchParams]
  );

  const loadCore = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const meta = await sitla.meta();
      if (!meta.enabled) {
        setDisabled(true);
        setLoading(false);
        return;
      }
      setDisabled(false);
      setJobsEnabled(Boolean(meta.jobs_enabled || meta.admin_enabled));
      const [sumRaw, covRaw] = await Promise.all([sitla.summary(), sitla.coverage()]);
      const sum = sumRaw as unknown as SitlaSummary;
      const cov = covRaw as unknown as SitlaCoverage;
      if (!sum.ok) throw new Error(sum.error || "Failed to load summary");
      setSummary(sum);
      setCoverage(cov.ok ? cov : null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load SITLA");
    } finally {
      setLoading(false);
    }
  }, []);

  const loadList = useCallback(async () => {
    try {
      const res = (await sitla.list({
        county: filters.county || undefined,
        status: filters.status || undefined,
        opportunity_type: filters.opportunity_type || undefined,
        priority_tier: filters.priority_tier || undefined,
        search: filters.search || undefined,
        active_only: filters.active_only,
        page: 1,
        page_size: 100,
        sort: "overall_priority_score",
        order: "desc",
      })) as unknown as SitlaOpportunityList;
      if (!res.ok) throw new Error(res.error || "Failed to load opportunities");
      setList(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load opportunities");
    }
  }, [filters]);

  const loadDetail = useCallback(async (id: string) => {
    try {
      const res = (await sitla.get(id)) as unknown as SitlaOpportunityDetail;
      if (!res.ok) throw new Error(res.error || "Failed to load opportunity");
      setDetail(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load detail");
    }
  }, []);

  const loadReview = useCallback(async () => {
    const res = await sitla.review();
    if (res.ok && Array.isArray(res.items)) setReviewItems(res.items as Array<Record<string, unknown>>);
  }, []);

  useEffect(() => {
    void loadCore();
  }, [loadCore]);

  useEffect(() => {
    if (disabled) return;
    if (tab === "opportunities") void loadList();
    if (tab === "review") void loadReview();
  }, [tab, disabled, loadList, loadReview]);

  useEffect(() => {
    if (selectedId && !disabled) void loadDetail(selectedId);
    else setDetail(null);
  }, [selectedId, disabled, loadDetail]);

  const applyFilters = (next: SitlaFilters) => {
    setFilters(next);
    syncUrl(next, tab, selectedId);
  };

  const applyCardFilter = (cardFilter?: Record<string, string | number | boolean> | null) => {
    if (!cardFilter) return;
    const next = { ...EMPTY_FILTERS, active_only: true };
    if (cardFilter.status) next.status = String(cardFilter.status);
    if (cardFilter.lifecycle_status) next.status = String(cardFilter.lifecycle_status);
    if (cardFilter.opportunity_type) next.opportunity_type = String(cardFilter.opportunity_type);
    if (cardFilter.priority_tier) next.priority_tier = String(cardFilter.priority_tier);
    if (cardFilter.county) next.county = String(cardFilter.county);
    if (cardFilter.active_only != null) next.active_only = Boolean(cardFilter.active_only);
    setTab("opportunities");
    applyFilters(next);
  };

  const selectRow = (id: string) => {
    setSelectedId(id);
    syncUrl(filters, tab, id);
  };

  const toggleWatch = async (row: SitlaOpportunityRow) => {
    try {
      if (row.watchlisted) await sitla.unwatch(row.id);
      else await sitla.watch(row.id);
      await loadList();
      if (selectedId === row.id) await loadDetail(row.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Watch update failed");
    }
  };

  const runRefresh = async () => {
    setBusyAction("refresh");
    setActionMsg(null);
    try {
      const res = await sitla.refresh();
      if (!res.ok) throw new Error(String(res.error || "Refresh failed"));
      setActionMsg(
        `Refresh finished — discovered ${String(res.records_discovered ?? res.ran ?? "ok")}.`
      );
      await loadCore();
      await loadList();
      if (selectedId) await loadDetail(selectedId);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Refresh failed");
    } finally {
      setBusyAction(null);
    }
  };

  const promoteSelected = async () => {
    if (!selectedId) return;
    setBusyAction("promote");
    setActionMsg(null);
    try {
      const res = await sitla.promote(selectedId);
      if (!res.ok) throw new Error(res.error || "Promote failed");
      setActionMsg(
        res.already_linked
          ? `Already linked to Target #${res.area_of_focus_id}`
          : `Promoted to Target #${res.area_of_focus_id}`
      );
      await loadDetail(selectedId);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Promote failed");
    } finally {
      setBusyAction(null);
    }
  };

  const scoreExpl = useMemo(() => {
    const s = detail?.score as { explanation_json?: Record<string, unknown> } | undefined;
    if (s?.explanation_json) return s.explanation_json;
    const opp = detail?.opportunity as { score_explanation_json?: Record<string, unknown> } | undefined;
    return opp?.score_explanation_json || null;
  }, [detail]);

  const commercialTerms = useMemo(
    () => asCommercialTermsList(detail?.commercial_terms),
    [detail]
  );

  if (disabled) {
    return (
      <div className="p-6 max-w-3xl">
        <h1 className="text-2xl font-bold text-slate-900">SITLA</h1>
        <p className="mt-2 text-slate-600">
          SITLA Intelligence is installed but disabled. Set{" "}
          <code className="text-xs bg-slate-100 px-1 rounded">ENABLE_SITLA_API=true</code> on the API
          and <code className="text-xs bg-slate-100 px-1 rounded">VITE_ENABLE_SITLA=true</code> for
          the frontend, then restart.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-xs font-semibold tracking-[0.18em] uppercase text-emerald-700">
            Trust Lands
          </p>
          <h1 className="text-2xl font-bold text-slate-900 mt-1">SITLA</h1>
          <p className="text-sm text-slate-600 mt-1 max-w-2xl">
            Utah Trust Lands mineral opportunities
          </p>
        </div>
        <div className="flex flex-wrap gap-2 text-sm items-center">
          {(["opportunities", "review", "sources"] as Tab[]).map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => {
                setTab(t);
                syncUrl(filters, t, selectedId);
              }}
              className={
                tab === t
                  ? "px-3 py-1.5 rounded-lg bg-slate-900 text-white font-medium"
                  : "px-3 py-1.5 rounded-lg border border-slate-200 text-slate-700 hover:bg-slate-50"
              }
            >
              {t === "opportunities" ? "Opportunities" : t === "review" ? "Review" : "Sources"}
            </button>
          ))}
          {jobsEnabled ? (
            <button
              type="button"
              onClick={() => void runRefresh()}
              disabled={busyAction === "refresh"}
              className="px-3 py-1.5 rounded-lg border border-emerald-300 text-emerald-800 bg-emerald-50 hover:bg-emerald-100 disabled:opacity-60"
            >
              {busyAction === "refresh" ? "Refreshing…" : "Refresh sources"}
            </button>
          ) : null}
        </div>
      </div>

      {actionMsg && (
        <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-900">
          {actionMsg}
        </div>
      )}

      {summary?.coverage_banner && (
        <div className="rounded-xl border border-emerald-200/80 bg-emerald-50/40 px-4 py-3 text-sm text-slate-700 flex flex-wrap gap-x-6 gap-y-1">
          <span className="font-medium text-slate-900">{summary.coverage_banner.message}</span>
          <span className="text-slate-500">{summary.coverage_banner.detail}</span>
          <span>
            Sources {summary.coverage_banner.healthy_sources}/{summary.coverage_banner.enabled_sources}{" "}
            healthy
            {(summary.coverage_banner.failed_or_stale || 0) > 0 && (
              <span className="text-amber-700">
                {" "}
                · {summary.coverage_banner.failed_or_stale} stale/failed
              </span>
            )}
          </span>
        </div>
      )}

      {summary?.cards && tab === "opportunities" && (
        <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-5 gap-2">
          {summary.cards.slice(0, 10).map((card) => (
            <button
              key={card.key}
              type="button"
              onClick={() => applyCardFilter(card.filter)}
              className="text-left rounded-xl border border-slate-200 bg-white px-3 py-2.5 hover:border-emerald-300 hover:shadow-sm transition"
            >
              <div className="text-[11px] uppercase tracking-wide text-slate-500">{card.label}</div>
              <div className="text-xl font-semibold text-slate-900 mt-0.5">{card.value}</div>
            </button>
          ))}
        </div>
      )}

      {error && (
        <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800">
          {error}
          <button type="button" className="ml-3 underline" onClick={() => setError(null)}>
            dismiss
          </button>
        </div>
      )}

      {tab === "opportunities" && (
        <div className="grid grid-cols-1 xl:grid-cols-[1fr_420px] gap-4">
          <div className="min-h-0 flex flex-col rounded-xl border border-slate-200 bg-white overflow-hidden">
            <div className="px-4 py-3 border-b border-slate-100 grid grid-cols-2 md:grid-cols-4 xl:grid-cols-6 gap-2">
              <input
                value={filters.search}
                onChange={(e) => applyFilters({ ...filters, search: e.target.value })}
                placeholder="Search title, lease #, PLSS…"
                className="col-span-2 px-3 py-2 border border-slate-200 rounded-lg text-sm"
              />
              <select
                value={filters.county}
                onChange={(e) => applyFilters({ ...filters, county: e.target.value })}
                className="px-3 py-2 border border-slate-200 rounded-lg text-sm"
              >
                <option value="">All counties</option>
                {UTAH_COUNTIES.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
              <select
                value={filters.status}
                onChange={(e) => applyFilters({ ...filters, status: e.target.value })}
                className="px-3 py-2 border border-slate-200 rounded-lg text-sm"
              >
                <option value="">Lifecycle status</option>
                {LIFECYCLE_OPTIONS.map((s) => (
                  <option key={s} value={s}>
                    {statusLabel(s)}
                  </option>
                ))}
              </select>
              <select
                value={filters.opportunity_type}
                onChange={(e) => applyFilters({ ...filters, opportunity_type: e.target.value })}
                className="px-3 py-2 border border-slate-200 rounded-lg text-sm"
              >
                <option value="">Opportunity type</option>
                {OPPORTUNITY_TYPE_OPTIONS.map((t) => (
                  <option key={t} value={t}>
                    {statusLabel(t)}
                  </option>
                ))}
              </select>
              <select
                value={filters.priority_tier}
                onChange={(e) => applyFilters({ ...filters, priority_tier: e.target.value })}
                className="px-3 py-2 border border-slate-200 rounded-lg text-sm"
              >
                <option value="">Priority tier</option>
                {["A", "B", "C", "D", "E"].map((t) => (
                  <option key={t} value={t}>
                    Tier {t}
                  </option>
                ))}
              </select>
              <label className="col-span-2 md:col-span-1 flex items-center gap-2 px-1 text-sm text-slate-700">
                <input
                  type="checkbox"
                  checked={filters.active_only}
                  onChange={(e) => applyFilters({ ...filters, active_only: e.target.checked })}
                  className="rounded border-slate-300"
                />
                Active only
              </label>
            </div>

            <div className="flex-1 overflow-auto">
              {loading && !list ? (
                <p className="p-6 text-sm text-slate-500">Loading opportunities…</p>
              ) : (
                <table className="min-w-full text-sm">
                  <thead className="sticky top-0 bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500 border-b border-slate-200">
                    <tr>
                      <th className="px-3 py-2 font-medium">Priority</th>
                      <th className="px-3 py-2 font-medium">Opportunity</th>
                      <th className="px-3 py-2 font-medium">Lifecycle</th>
                      <th className="px-3 py-2 font-medium">Deadline</th>
                      <th className="px-3 py-2 font-medium">Terms</th>
                      <th className="px-3 py-2 font-medium">Type</th>
                      <th className="px-3 py-2 font-medium">Commodity</th>
                      <th className="px-3 py-2 font-medium">Watch</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(list?.items || []).map((row) => {
                      const selected = selectedId === row.id;
                      const deadline = nearestDeadline(row);
                      return (
                        <tr
                          key={row.id}
                          onClick={() => selectRow(row.id)}
                          className={
                            selected
                              ? "bg-emerald-50/70 cursor-pointer border-b border-emerald-100"
                              : "hover:bg-slate-50 cursor-pointer border-b border-slate-100"
                          }
                        >
                          <td className="px-3 py-2.5 align-top">
                            <span
                              className={`inline-flex h-6 w-6 items-center justify-center rounded-md text-xs font-bold ${tierBadgeClass(row.priority_tier)}`}
                            >
                              {row.priority_tier}
                            </span>
                            <div className="text-xs text-slate-500 mt-1">
                              {Number(row.overall_priority_score).toFixed(0)}
                            </div>
                          </td>
                          <td className="px-3 py-2.5 align-top">
                            <div className="font-medium text-slate-900">
                              {row.best_title || "Untitled"}
                            </div>
                            <div className="text-xs text-slate-500">
                              {row.county_name || "Utah"} ·{" "}
                              {row.reference_number || row.lease_number || "No ref"}
                            </div>
                            {row.plss_key && (
                              <div className="text-xs text-slate-400 mt-0.5">{row.plss_key}</div>
                            )}
                          </td>
                          <td className="px-3 py-2.5 align-top text-xs text-slate-700">
                            {statusLabel(row.lifecycle_status)}
                          </td>
                          <td className="px-3 py-2.5 align-top">
                            <div>{formatDate(deadline)}</div>
                            {deadline && (
                              <div className="text-xs text-amber-700">{daysUntil(deadline)}</div>
                            )}
                          </td>
                          <td className="px-3 py-2.5 align-top text-xs">
                            <div>min {formatMoney(row.minimum_bid)}</div>
                            <div className="text-slate-500">
                              rent {formatMoney(row.annual_rental)}
                            </div>
                          </td>
                          <td className="px-3 py-2.5 align-top text-xs text-slate-700">
                            {statusLabel(row.opportunity_type)}
                          </td>
                          <td className="px-3 py-2.5 align-top text-xs text-slate-700">
                            <div>{row.published_commodity || "—"}</div>
                            <div className="text-slate-500">
                              {(row.commodities || []).slice(0, 3).join(", ") || "—"}
                            </div>
                          </td>
                          <td className="px-3 py-2.5 align-top">
                            <button
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation();
                                void toggleWatch(row);
                              }}
                              className={
                                row.watchlisted
                                  ? "text-emerald-700 text-xs font-medium"
                                  : "text-slate-400 text-xs hover:text-slate-700"
                              }
                            >
                              {row.watchlisted ? "Watching" : "Watch"}
                            </button>
                          </td>
                        </tr>
                      );
                    })}
                    {!loading && (list?.items?.length || 0) === 0 && (
                      <tr>
                        <td colSpan={8} className="px-4 py-10 text-center text-slate-500">
                          No opportunities match these filters.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              )}
            </div>
            <div className="px-4 py-2 border-t border-slate-100 text-xs text-slate-500 bg-white">
              {list?.total ?? 0} opportunities · scores are deterministic (sitla-v1.0) · Utah Trust
              Lands mineral leasing
            </div>
          </div>

          <aside className="min-h-0 overflow-auto rounded-xl border border-slate-200 bg-white">
            {!detail?.opportunity ? (
              <div className="p-6 text-sm text-slate-500">
                Select an opportunity to inspect commercial terms, PLSS, mineral evidence, claim
                context, and the evidence ledger.
              </div>
            ) : (
              <div className="p-4 space-y-4">
                <div>
                  <div className="flex items-start justify-between gap-2">
                    <div>
                      <h2 className="text-lg font-semibold text-slate-900">
                        {String(detail.opportunity.best_title || "Opportunity")}
                      </h2>
                      <p className="text-sm text-slate-500 mt-0.5">
                        {String(detail.opportunity.county_name || "Utah")} ·{" "}
                        {String(
                          detail.opportunity.reference_number ||
                            detail.opportunity.lease_number ||
                            "—"
                        )}
                      </p>
                    </div>
                    <span
                      className={`inline-flex h-8 w-8 items-center justify-center rounded-lg text-sm font-bold ${tierBadgeClass(String(detail.opportunity.priority_tier))}`}
                    >
                      {String(detail.opportunity.priority_tier)}
                    </span>
                  </div>
                  <div className="mt-3 flex flex-wrap gap-2">
                    <button
                      type="button"
                      onClick={() => {
                        const opp = detail.opportunity as SitlaOpportunityRow;
                        void toggleWatch(opp);
                      }}
                      className={
                        detail.opportunity.watchlisted
                          ? "px-3 py-1.5 rounded-lg border border-emerald-300 text-emerald-800 bg-emerald-50 text-sm font-medium"
                          : "px-3 py-1.5 rounded-lg border border-slate-200 text-sm text-slate-700 hover:bg-slate-50"
                      }
                    >
                      {detail.opportunity.watchlisted ? "Unwatch" : "Watch"}
                    </button>
                    <button
                      type="button"
                      onClick={() => void promoteSelected()}
                      disabled={busyAction === "promote"}
                      className="px-3 py-1.5 rounded-lg bg-slate-900 text-white text-sm font-medium hover:bg-slate-800 disabled:opacity-60"
                    >
                      {busyAction === "promote" ? "Promoting…" : "Promote to Target"}
                    </button>
                    <Link
                      to="/areas"
                      className="px-3 py-1.5 rounded-lg border border-slate-200 text-sm text-slate-700 hover:bg-slate-50"
                    >
                      Open Targets
                    </Link>
                  </div>
                  <div className="mt-3 grid grid-cols-2 gap-2 text-sm">
                    <div className="rounded-lg bg-slate-50 px-3 py-2">
                      <div className="text-[11px] uppercase text-slate-500">Priority</div>
                      <div className="font-semibold">
                        {Number(detail.opportunity.overall_priority_score).toFixed(0)}
                      </div>
                    </div>
                    <div className="rounded-lg bg-slate-50 px-3 py-2">
                      <div className="text-[11px] uppercase text-slate-500">Mineral / Acquire</div>
                      <div className="font-semibold">
                        {Number(detail.opportunity.mineral_potential_score).toFixed(0)} /{" "}
                        {Number(detail.opportunity.acquisition_readiness_score).toFixed(0)}
                      </div>
                    </div>
                    <div className="rounded-lg bg-slate-50 px-3 py-2">
                      <div className="text-[11px] uppercase text-slate-500">Minimum bid</div>
                      <div className="font-semibold">
                        {formatMoney(detail.opportunity.minimum_bid as number)}
                      </div>
                    </div>
                    <div className="rounded-lg bg-slate-50 px-3 py-2">
                      <div className="text-[11px] uppercase text-slate-500">Deadline</div>
                      <div className="font-semibold">
                        {formatDate(
                          nearestDeadline(detail.opportunity as SitlaOpportunityRow)
                        )}
                      </div>
                    </div>
                  </div>
                </div>

                <section>
                  <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2">
                    Commercial terms
                  </h3>
                  {commercialTerms.length > 0 ? (
                    <ul className="space-y-2">
                      {commercialTerms.map((t, idx) => (
                        <li
                          key={String(t.id ?? idx)}
                          className="rounded-lg border border-slate-200 px-3 py-2 text-sm space-y-1"
                        >
                          <div className="grid grid-cols-2 gap-2 text-xs">
                            <div>
                              <span className="text-slate-500">Min bid </span>
                              {formatMoney(t.minimum_bid as number)}
                            </div>
                            <div>
                              <span className="text-slate-500">Rental </span>
                              {formatMoney(t.annual_rental as number)}
                            </div>
                            <div>
                              <span className="text-slate-500">Royalty </span>
                              {String(t.royalty_rate || "—")}
                            </div>
                            <div>
                              <span className="text-slate-500">Bond </span>
                              {formatMoney(t.bond_amount as number)}
                            </div>
                            <div>
                              <span className="text-slate-500">App fee </span>
                              {formatMoney(t.application_fee as number)}
                            </div>
                            <div>
                              <span className="text-slate-500">Term </span>
                              {t.primary_term_years != null
                                ? `${String(t.primary_term_years)} yr`
                                : "—"}
                            </div>
                          </div>
                          {t.terms_summary ? (
                            <p className="text-xs text-slate-600 mt-1">{String(t.terms_summary)}</p>
                          ) : null}
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <div className="rounded-lg border border-slate-200 px-3 py-2 text-sm grid grid-cols-2 gap-2 text-xs">
                      <div>
                        <span className="text-slate-500">Min bid </span>
                        {formatMoney(detail.opportunity.minimum_bid as number)}
                      </div>
                      <div>
                        <span className="text-slate-500">Rental </span>
                        {formatMoney(detail.opportunity.annual_rental as number)}
                      </div>
                      <div>
                        <span className="text-slate-500">Royalty </span>
                        {String(detail.opportunity.royalty_rate || "—")}
                      </div>
                      <div>
                        <span className="text-slate-500">Bond </span>
                        {formatMoney(detail.opportunity.bond_amount as number)}
                      </div>
                    </div>
                  )}
                </section>

                <section>
                  <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2">
                    PLSS / legal
                  </h3>
                  <div className="rounded-lg border border-slate-200 px-3 py-2 text-sm space-y-1">
                    <div className="font-medium text-slate-800">
                      {String(detail.opportunity.plss_key || "PLSS unresolved")}
                    </div>
                    <div className="text-xs text-slate-500">
                      T{String(detail.opportunity.township || "—")} R
                      {String(detail.opportunity.range || "—")} ·{" "}
                      {String(detail.opportunity.section_summary || "sections —")} ·{" "}
                      {String(detail.opportunity.meridian || "meridian —")}
                    </div>
                    {detail.opportunity.legal_description_raw ? (
                      <p className="text-xs text-slate-600 mt-1">
                        {String(detail.opportunity.legal_description_raw)}
                      </p>
                    ) : null}
                    <div className="text-xs text-slate-400">
                      Geometry {String(detail.opportunity.geometry_accuracy || "UNKNOWN")} ·{" "}
                      {detail.opportunity.acreage != null
                        ? `${Number(detail.opportunity.acreage).toFixed(1)} ac`
                        : "acreage —"}
                    </div>
                  </div>
                  {(detail.legal_parts || []).length > 0 && (
                    <ul className="mt-2 space-y-1">
                      {(detail.legal_parts || []).map((p) => (
                        <li
                          key={String(p.id)}
                          className="text-xs text-slate-600 rounded border border-dashed border-slate-200 px-2 py-1"
                        >
                          T{String(p.township || "—")} R{String(p.range || "—")} Sec{" "}
                          {String(p.section || "—")} {String(p.aliquot || "")}
                        </li>
                      ))}
                    </ul>
                  )}
                </section>

                <section>
                  <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2">
                    Mineral evidence
                  </h3>
                  <ul className="space-y-2">
                    {(detail.mineral_evidence || []).map((m) => (
                      <li
                        key={String(m.id)}
                        className="rounded-lg border border-slate-200 px-3 py-2 text-sm"
                      >
                        <div className="font-medium">
                          {String(m.mine_name || m.prospect_name || "Occurrence")}
                        </div>
                        <div className="text-xs text-slate-500">
                          {m.inside_parcel ? "Inside acreage" : `${m.distance_meters ?? "?"} m away`} ·{" "}
                          {String(m.commodity_normalized || "commodity unknown")} ·{" "}
                          {String(m.production_status || "")}
                        </div>
                      </li>
                    ))}
                    {(detail.mineral_evidence || []).length === 0 && (
                      <li className="text-sm text-slate-500">No mineral evidence linked yet.</li>
                    )}
                  </ul>
                </section>

                <section>
                  <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2">
                    Claim context
                  </h3>
                  <ul className="space-y-2">
                    {(detail.claim_context || []).map((c) => (
                      <li
                        key={String(c.id)}
                        className="rounded-lg border border-dashed border-slate-200 px-3 py-2 text-sm text-slate-600"
                      >
                        Nearby MLRS {String(c.claim_status || "status?")} ·{" "}
                        {String(c.claim_name || c.mlrs_serial_number || "claim")} ·{" "}
                        {String(c.distance_meters ?? "?")} m
                      </li>
                    ))}
                    {(detail.claim_context || []).length === 0 && (
                      <li className="text-sm text-slate-500">No nearby claim context yet.</li>
                    )}
                  </ul>
                </section>

                <section>
                  <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2">
                    Timeline
                  </h3>
                  <ol className="space-y-2 border-l border-slate-200 pl-3">
                    {(detail.timeline || []).map((ev) => (
                      <li key={String(ev.id)} className="text-sm">
                        <div className="font-medium text-slate-800">
                          {String(ev.title || ev.event_type)}
                        </div>
                        <div className="text-xs text-slate-500">
                          {formatDate(ev.event_at as string)}
                        </div>
                        {ev.description ? (
                          <div className="text-xs text-slate-600 mt-0.5">
                            {String(ev.description)}
                          </div>
                        ) : null}
                      </li>
                    ))}
                    {(detail.timeline || []).length === 0 && (
                      <li className="text-sm text-slate-500">No timeline events yet.</li>
                    )}
                  </ol>
                </section>

                {scoreExpl && (
                  <section>
                    <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2">
                      Score breakdown
                    </h3>
                    <div className="rounded-lg border border-slate-200 px-3 py-2 text-sm space-y-2">
                      <div>
                        <div className="text-[11px] uppercase text-slate-500">Positive factors</div>
                        <ul className="list-disc pl-4 text-slate-700">
                          {((scoreExpl.top_positive_factors as string[]) || []).map((f) => (
                            <li key={f}>{f}</li>
                          ))}
                        </ul>
                      </div>
                      <div>
                        <div className="text-[11px] uppercase text-slate-500">Risks</div>
                        <ul className="list-disc pl-4 text-slate-700">
                          {((scoreExpl.top_risks as string[]) || []).map((f) => (
                            <li key={f}>{f}</li>
                          ))}
                        </ul>
                      </div>
                    </div>
                  </section>
                )}

                <section>
                  <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2">
                    Evidence ledger
                  </h3>
                  <div className="space-y-2">
                    {(detail.evidence_ledger || []).map((e) => {
                      const val = (e.fact_value_json as { value?: unknown })?.value;
                      return (
                        <div key={String(e.id)} className="rounded-lg bg-slate-50 px-3 py-2 text-xs">
                          <div className="flex justify-between gap-2">
                            <span className="font-medium text-slate-800">{String(e.fact_key)}</span>
                            <span className="text-slate-500">{String(e.evidence_class)}</span>
                          </div>
                          <div className="text-slate-700 mt-0.5 break-words">
                            {String(val ?? "")}
                          </div>
                          <div className="text-slate-400 mt-1">
                            {String(e.source_name || "source")} · conf{" "}
                            {Math.round(Number(e.confidence || 0) * 100)}% ·{" "}
                            {String(e.extraction_method)}
                          </div>
                        </div>
                      );
                    })}
                    {(detail.evidence_ledger || []).length === 0 && (
                      <p className="text-sm text-slate-500">No evidence ledger entries yet.</p>
                    )}
                  </div>
                </section>

                <p className="text-[11px] text-slate-500 leading-relaxed border-t border-slate-100 pt-3">
                  {detail.disclaimer ||
                    "SITLA listings are public trust-land records. Verify rights, terms, and geometry with official sources before acting."}
                </p>
                {(detail.opportunity.official_detail_url ||
                  detail.opportunity.external_bid_url) && (
                  <div className="flex flex-wrap gap-3 text-sm">
                    {detail.opportunity.official_detail_url ? (
                      <a
                        href={String(detail.opportunity.official_detail_url)}
                        target="_blank"
                        rel="noreferrer"
                        className="text-primary-700 hover:underline"
                      >
                        Official listing →
                      </a>
                    ) : null}
                    {detail.opportunity.external_bid_url ? (
                      <a
                        href={String(detail.opportunity.external_bid_url)}
                        target="_blank"
                        rel="noreferrer"
                        className="text-primary-700 hover:underline"
                      >
                        Bid portal →
                      </a>
                    ) : null}
                  </div>
                )}
                <Link to="/map" className="text-sm text-primary-700 hover:underline block">
                  Open main Map →
                </Link>
              </div>
            )}
          </aside>
        </div>
      )}

      {tab === "review" && (
        <div>
          <h2 className="text-lg font-semibold text-slate-900 mb-1">Review queue</h2>
          <p className="text-sm text-slate-500 mb-4">
            Analyst tasks for ambiguous rights, geometry, and source conflicts. Decisions never erase
            source observations.
          </p>
          <div className="space-y-2 max-w-4xl">
            {reviewItems.map((task) => (
              <button
                key={String(task.id)}
                type="button"
                onClick={() => {
                  setTab("opportunities");
                  selectRow(String(task.opportunity_id));
                }}
                className="w-full text-left rounded-xl border border-slate-200 bg-white px-4 py-3 hover:border-emerald-300"
              >
                <div className="flex justify-between gap-3">
                  <div>
                    <div className="font-medium text-slate-900">{String(task.title)}</div>
                    <div className="text-sm text-slate-600 mt-0.5">
                      {String(task.best_title || task.best_name || "")} ·{" "}
                      {String(task.county_name || "Utah")}
                    </div>
                    <div className="text-xs text-slate-500 mt-1">
                      {String(task.instructions || "")}
                    </div>
                  </div>
                  <div className="text-xs text-slate-500 whitespace-nowrap">
                    {String(task.task_type)} · tier {String(task.priority_tier || "")}
                  </div>
                </div>
              </button>
            ))}
            {reviewItems.length === 0 && (
              <p className="text-sm text-slate-500">No open review tasks.</p>
            )}
          </div>
        </div>
      )}

      {tab === "sources" && (
        <div>
          <h2 className="text-lg font-semibold text-slate-900 mb-1">Source coverage</h2>
          <p className="text-sm text-slate-500 mb-4">
            {coverage?.coverage_language ||
              "SITLA public listings, notices, and offering-cycle adapters for Utah Trust Lands."}{" "}
            Sources refresh when jobs are enabled (
            <code className="text-xs bg-slate-100 px-1 rounded">ENABLE_SITLA_JOBS=true</code>
            ). Manual CSV/fixture uploads remain available when live feeds are unavailable.
          </p>
          <div className="overflow-auto rounded-xl border border-slate-200 bg-white">
            <table className="min-w-full text-sm">
              <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="px-3 py-2 text-left">Source</th>
                  <th className="px-3 py-2 text-left">Category</th>
                  <th className="px-3 py-2 text-left">Parser</th>
                  <th className="px-3 py-2 text-left">Health</th>
                  <th className="px-3 py-2 text-left">Enabled</th>
                  <th className="px-3 py-2 text-left">Last success</th>
                </tr>
              </thead>
              <tbody>
                {(coverage?.sources || []).map((s) => (
                  <tr key={String(s.id || s.source_key)} className="border-t border-slate-100">
                    <td className="px-3 py-2">
                      <div className="font-medium">{String(s.name)}</div>
                      <div className="text-xs text-slate-500">{String(s.source_key)}</div>
                    </td>
                    <td className="px-3 py-2 text-xs">{String(s.source_category || "SITLA")}</td>
                    <td className="px-3 py-2 text-xs">{String(s.parser_kind)}</td>
                    <td className="px-3 py-2">
                      <span
                        className={`inline-flex px-2 py-0.5 rounded-full text-[11px] font-medium ${healthClass(String(s.health_status))}`}
                      >
                        {String(s.health_status)}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-xs">
                      {s.enabled ? "Yes" : s.manual_only ? "Manual" : "No"}
                    </td>
                    <td className="px-3 py-2 text-xs text-slate-500">
                      {formatDate(s.last_success_at as string)}
                    </td>
                  </tr>
                ))}
                {(coverage?.sources || []).length === 0 && (
                  <tr>
                    <td colSpan={6} className="px-4 py-8 text-center text-slate-500">
                      No sources registered yet.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          {summary?.disclaimer && (
            <p className="mt-4 text-xs text-slate-500 max-w-3xl">{summary.disclaimer}</p>
          )}
        </div>
      )}
    </div>
  );
}
