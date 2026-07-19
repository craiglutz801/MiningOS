import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { taxSales } from "../../api";
import type {
  TaxFilters,
  TaxOpportunityDetail,
  TaxOpportunityList,
  TaxOpportunityRow,
  TaxSalesSummary,
  TaxCoverage,
} from "./types";
import {
  daysUntil,
  formatDate,
  formatMoney,
  healthClass,
  patentBadgeClass,
  statusLabel,
  tierBadgeClass,
} from "./utils";

type Tab = "opportunities" | "review" | "sources";

const EMPTY_FILTERS: TaxFilters = {
  state: "",
  county: "",
  status: "",
  patent_classification: "",
  mineral_signal: "",
  priority_tier: "",
  review_status: "",
  search: "",
  min_score: "",
  auction_within_days: "",
  active_only: true,
};

function filtersFromParams(sp: URLSearchParams): TaxFilters {
  return {
    state: sp.get("state") || "",
    county: sp.get("county") || "",
    status: sp.get("status") || "",
    patent_classification: sp.get("patent") || "",
    mineral_signal: sp.get("mineral") || "",
    priority_tier: sp.get("tier") || "",
    review_status: sp.get("review") || "",
    search: sp.get("q") || "",
    min_score: sp.get("min_score") || "",
    auction_within_days: sp.get("auction_days") || "",
    active_only: sp.get("active_only") !== "false",
  };
}

function paramsFromFilters(f: TaxFilters): URLSearchParams {
  const sp = new URLSearchParams();
  if (f.state) sp.set("state", f.state);
  if (f.county) sp.set("county", f.county);
  if (f.status) sp.set("status", f.status);
  if (f.patent_classification) sp.set("patent", f.patent_classification);
  if (f.mineral_signal) sp.set("mineral", f.mineral_signal);
  if (f.priority_tier) sp.set("tier", f.priority_tier);
  if (f.review_status) sp.set("review", f.review_status);
  if (f.search) sp.set("q", f.search);
  if (f.min_score) sp.set("min_score", f.min_score);
  if (f.auction_within_days) sp.set("auction_days", f.auction_within_days);
  if (!f.active_only) sp.set("active_only", "false");
  return sp;
}

export function TaxSalesPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [tab, setTab] = useState<Tab>((searchParams.get("tab") as Tab) || "opportunities");
  const [filters, setFilters] = useState<TaxFilters>(() => filtersFromParams(searchParams));
  const [summary, setSummary] = useState<TaxSalesSummary | null>(null);
  const [list, setList] = useState<TaxOpportunityList | null>(null);
  const [coverage, setCoverage] = useState<TaxCoverage | null>(null);
  const [reviewItems, setReviewItems] = useState<Array<Record<string, unknown>>>([]);
  const [selectedId, setSelectedId] = useState<string | null>(searchParams.get("id"));
  const [detail, setDetail] = useState<TaxOpportunityDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [disabled, setDisabled] = useState(false);
  const [jobsEnabled, setJobsEnabled] = useState(false);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [actionMsg, setActionMsg] = useState<string | null>(null);

  const syncUrl = useCallback(
    (next: TaxFilters, nextTab: Tab, id: string | null) => {
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
      const meta = await taxSales.meta();
      if (!meta.enabled) {
        setDisabled(true);
        setLoading(false);
        return;
      }
      setDisabled(false);
      setJobsEnabled(Boolean(meta.jobs_enabled || meta.admin_enabled));
      const [sumRaw, covRaw] = await Promise.all([taxSales.summary(), taxSales.coverage()]);
      const sum = sumRaw as unknown as TaxSalesSummary;
      const cov = covRaw as unknown as TaxCoverage;
      if (!sum.ok) throw new Error(sum.error || "Failed to load summary");
      setSummary(sum);
      setCoverage(cov.ok ? cov : null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load Tax Sales");
    } finally {
      setLoading(false);
    }
  }, []);

  const loadList = useCallback(async () => {
    try {
      const res = (await taxSales.list({
        state: filters.state || undefined,
        county: filters.county || undefined,
        status: filters.status || undefined,
        patent_classification: filters.patent_classification || undefined,
        mineral_signal: filters.mineral_signal || undefined,
        priority_tier: filters.priority_tier || undefined,
        review_status: filters.review_status || undefined,
        search: filters.search || undefined,
        min_score: filters.min_score ? Number(filters.min_score) : undefined,
        auction_within_days: filters.auction_within_days
          ? Number(filters.auction_within_days)
          : undefined,
        active_only: filters.active_only,
        page: 1,
        page_size: 100,
        sort: "overall_priority_score",
        order: "desc",
      })) as unknown as TaxOpportunityList;
      if (!res.ok) throw new Error(res.error || "Failed to load opportunities");
      setList(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load opportunities");
    }
  }, [filters]);

  const loadDetail = useCallback(async (id: string) => {
    try {
      const res = (await taxSales.get(id)) as unknown as TaxOpportunityDetail;
      if (!res.ok) throw new Error(res.error || "Failed to load opportunity");
      setDetail(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load detail");
    }
  }, []);

  const loadReview = useCallback(async () => {
    const res = await taxSales.review();
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

  const applyFilters = (next: TaxFilters) => {
    setFilters(next);
    syncUrl(next, tab, selectedId);
  };

  const applyCardFilter = (cardFilter?: Record<string, string | number | boolean> | null) => {
    if (!cardFilter) return;
    const next = { ...EMPTY_FILTERS, active_only: true };
    if (cardFilter.status) next.status = String(cardFilter.status);
    if (cardFilter.patent_classification) next.patent_classification = String(cardFilter.patent_classification);
    if (cardFilter.mineral_signal) next.mineral_signal = String(cardFilter.mineral_signal);
    if (cardFilter.priority_tier) next.priority_tier = String(cardFilter.priority_tier);
    if (cardFilter.review_status) next.review_status = String(cardFilter.review_status);
    if (cardFilter.min_score != null) next.min_score = String(cardFilter.min_score);
    if (cardFilter.auction_within_days != null) next.auction_within_days = String(cardFilter.auction_within_days);
    setTab("opportunities");
    applyFilters(next);
  };

  const selectRow = (id: string) => {
    setSelectedId(id);
    syncUrl(filters, tab, id);
  };

  const toggleWatch = async (row: TaxOpportunityRow) => {
    try {
      if (row.watchlisted) await taxSales.unwatch(row.id);
      else await taxSales.watch(row.id);
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
      const res = await taxSales.refresh();
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
      const res = await taxSales.promote(selectedId);
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

  if (disabled) {
    return (
      <div className="p-6 max-w-3xl">
        <h1 className="text-2xl font-bold text-slate-900">Tax Sales</h1>
        <p className="mt-2 text-slate-600">
          Patented Claim Watch is installed but disabled. Set{" "}
          <code className="text-xs bg-slate-100 px-1 rounded">ENABLE_TAX_SALES_API=true</code> on the
          API and <code className="text-xs bg-slate-100 px-1 rounded">VITE_ENABLE_TAX_SALES=true</code>{" "}
          for the frontend, then restart.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-xs font-semibold tracking-[0.18em] uppercase text-emerald-700">
            Patented Claim Watch
          </p>
          <h1 className="text-2xl font-bold text-slate-900 mt-1">Tax Sales</h1>
          <p className="text-sm text-slate-600 mt-1 max-w-2xl">
            Public tax-sale and delinquency records triangulated against patents, Mineral Surveys,
            mines, and nearby claims — with evidence you can audit.
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
            Counties {summary.coverage_banner.healthy_counties}/{summary.coverage_banner.enabled_counties}{" "}
            healthy
            {(summary.coverage_banner.failed_or_stale || 0) > 0 && (
              <span className="text-amber-700"> · {summary.coverage_banner.failed_or_stale} stale/failed</span>
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
                placeholder="Search APN, name, PLSS…"
                className="col-span-2 px-3 py-2 border border-slate-200 rounded-lg text-sm"
              />
              <select
                value={filters.state}
                onChange={(e) => applyFilters({ ...filters, state: e.target.value })}
                className="px-3 py-2 border border-slate-200 rounded-lg text-sm"
              >
                <option value="">All states</option>
                <option value="UT">Utah</option>
                <option value="ID">Idaho</option>
                <option value="NV">Nevada</option>
              </select>
              <select
                value={filters.patent_classification}
                onChange={(e) => applyFilters({ ...filters, patent_classification: e.target.value })}
                className="px-3 py-2 border border-slate-200 rounded-lg text-sm"
              >
                <option value="">Patent status</option>
                <option value="CONFIRMED">Confirmed</option>
                <option value="PROBABLE">Probable</option>
                <option value="POSSIBLE">Possible</option>
                <option value="UNLIKELY">Unlikely</option>
                <option value="UNKNOWN">Unknown</option>
              </select>
              <select
                value={filters.status}
                onChange={(e) => applyFilters({ ...filters, status: e.target.value })}
                className="px-3 py-2 border border-slate-200 rounded-lg text-sm"
              >
                <option value="">Tax stage</option>
                <option value="AUCTION_SCHEDULED">Auction scheduled</option>
                <option value="SALE_ELIGIBLE">Sale eligible</option>
                <option value="COUNTY_OR_TRUSTEE_HELD">County/trustee held</option>
                <option value="NOTICE_PUBLISHED">Notice published</option>
                <option value="PENDING_TAX_DEED">Pending tax deed</option>
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
            </div>

            <div className="flex-1 overflow-auto">
              {loading && !list ? (
                <p className="p-6 text-sm text-slate-500">Loading opportunities…</p>
              ) : (
                <table className="min-w-full text-sm">
                  <thead className="sticky top-0 bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500 border-b border-slate-200">
                    <tr>
                      <th className="px-3 py-2 font-medium">Priority</th>
                      <th className="px-3 py-2 font-medium">Property / Claim</th>
                      <th className="px-3 py-2 font-medium">Stage</th>
                      <th className="px-3 py-2 font-medium">Auction</th>
                      <th className="px-3 py-2 font-medium">Due / Bid</th>
                      <th className="px-3 py-2 font-medium">Patent</th>
                      <th className="px-3 py-2 font-medium">Mineral</th>
                      <th className="px-3 py-2 font-medium">Watch</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(list?.items || []).map((row) => {
                      const selected = selectedId === row.id;
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
                            <span className={`inline-flex h-6 w-6 items-center justify-center rounded-md text-xs font-bold ${tierBadgeClass(row.priority_tier)}`}>
                              {row.priority_tier}
                            </span>
                            <div className="text-xs text-slate-500 mt-1">{Number(row.overall_priority_score).toFixed(0)}</div>
                          </td>
                          <td className="px-3 py-2.5 align-top">
                            <div className="font-medium text-slate-900">{row.best_name || "Untitled"}</div>
                            <div className="text-xs text-slate-500">
                              {row.state} · {row.county_name} · {row.primary_apn || "No APN"}
                            </div>
                            {row.plss_key && <div className="text-xs text-slate-400 mt-0.5">{row.plss_key}</div>}
                          </td>
                          <td className="px-3 py-2.5 align-top text-xs text-slate-700">
                            {statusLabel(row.sale_lifecycle_status)}
                          </td>
                          <td className="px-3 py-2.5 align-top">
                            <div>{formatDate(row.auction_start_at)}</div>
                            {row.auction_start_at && (
                              <div className="text-xs text-amber-700">{daysUntil(row.auction_start_at)}</div>
                            )}
                          </td>
                          <td className="px-3 py-2.5 align-top text-xs">
                            <div>{formatMoney(row.amount_due)}</div>
                            <div className="text-slate-500">min {formatMoney(row.minimum_bid)}</div>
                          </td>
                          <td className="px-3 py-2.5 align-top">
                            <span className={`inline-flex px-2 py-0.5 rounded-full text-[11px] font-medium ${patentBadgeClass(row.patent_classification)}`}>
                              {row.patent_classification}
                            </span>
                          </td>
                          <td className="px-3 py-2.5 align-top text-xs text-slate-700">
                            <div>{row.mineral_signal}</div>
                            <div className="text-slate-500">{(row.commodities || []).slice(0, 3).join(", ") || "—"}</div>
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
              {list?.total ?? 0} opportunities · scores are deterministic (tax-v1.0) · demo fixtures included for pilot counties
            </div>
          </div>

          <aside className="min-h-0 overflow-auto rounded-xl border border-slate-200 bg-white">
            {!detail?.opportunity ? (
              <div className="p-6 text-sm text-slate-500">
                Select an opportunity to inspect tax timeline, patent evidence, mineral triangulation, and the evidence ledger.
              </div>
            ) : (
              <div className="p-4 space-y-4">
                <div>
                  <div className="flex items-start justify-between gap-2">
                    <div>
                      <h2 className="text-lg font-semibold text-slate-900">
                        {String(detail.opportunity.best_name || "Opportunity")}
                      </h2>
                      <p className="text-sm text-slate-500 mt-0.5">
                        {String(detail.opportunity.state)} · {String(detail.opportunity.county_name)} ·{" "}
                        {String(detail.opportunity.primary_apn || "—")}
                      </p>
                    </div>
                    <span className={`inline-flex h-8 w-8 items-center justify-center rounded-lg text-sm font-bold ${tierBadgeClass(String(detail.opportunity.priority_tier))}`}>
                      {String(detail.opportunity.priority_tier)}
                    </span>
                  </div>
                  <div className="mt-3 flex flex-wrap gap-2">
                    <button
                      type="button"
                      onClick={() => void promoteSelected()}
                      disabled={busyAction === "promote"}
                      className="px-3 py-1.5 rounded-lg bg-slate-900 text-white text-sm font-medium hover:bg-slate-800 disabled:opacity-60"
                    >
                      {busyAction === "promote" ? "Promoting…" : "Promote to Target"}
                    </button>
                    <Link to="/areas" className="px-3 py-1.5 rounded-lg border border-slate-200 text-sm text-slate-700 hover:bg-slate-50">
                      Open Targets
                    </Link>
                  </div>
                  <div className="mt-3 grid grid-cols-2 gap-2 text-sm">
                    <div className="rounded-lg bg-slate-50 px-3 py-2">
                      <div className="text-[11px] uppercase text-slate-500">Priority</div>
                      <div className="font-semibold">{Number(detail.opportunity.overall_priority_score).toFixed(0)}</div>
                    </div>
                    <div className="rounded-lg bg-slate-50 px-3 py-2">
                      <div className="text-[11px] uppercase text-slate-500">Mineral / Acquire</div>
                      <div className="font-semibold">
                        {Number(detail.opportunity.mineral_potential_score).toFixed(0)} /{" "}
                        {Number(detail.opportunity.acquisition_readiness_score).toFixed(0)}
                      </div>
                    </div>
                    <div className="rounded-lg bg-slate-50 px-3 py-2">
                      <div className="text-[11px] uppercase text-slate-500">Amount due</div>
                      <div className="font-semibold">{formatMoney(detail.opportunity.amount_due as number)}</div>
                    </div>
                    <div className="rounded-lg bg-slate-50 px-3 py-2">
                      <div className="text-[11px] uppercase text-slate-500">Auction</div>
                      <div className="font-semibold">{formatDate(detail.opportunity.auction_start_at as string)}</div>
                    </div>
                  </div>
                </div>

                <section>
                  <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2">Patent analysis</h3>
                  <div className={`inline-flex px-2 py-0.5 rounded-full text-xs font-medium ${patentBadgeClass(String(detail.opportunity.patent_classification))}`}>
                    {String(detail.opportunity.patent_classification)} ·{" "}
                    {Math.round(Number(detail.opportunity.patent_confidence || 0) * 100)}% confidence
                  </div>
                  <ul className="mt-2 space-y-2">
                    {(detail.patent_matches || []).map((m) => (
                      <li key={String(m.id)} className="rounded-lg border border-slate-200 px-3 py-2 text-sm">
                        <div className="font-medium text-slate-800">
                          {String(m.patent_number || "Patent record")}{" "}
                          {Array.isArray(m.mineral_survey_numbers) && m.mineral_survey_numbers.length > 0
                            ? `· MS ${(m.mineral_survey_numbers as string[]).join(", ")}`
                            : ""}
                        </div>
                        <div className="text-xs text-slate-500 mt-0.5">{String(m.legal_description || "")}</div>
                        {m.document_url ? (
                          <a
                            href={String(m.document_url)}
                            target="_blank"
                            rel="noreferrer"
                            className="text-xs text-primary-700 hover:underline mt-1 inline-block"
                          >
                            Open GLO / patent source
                          </a>
                        ) : null}
                      </li>
                    ))}
                    {(detail.patent_matches || []).length === 0 && (
                      <li className="text-sm text-slate-500">No patent match candidates yet.</li>
                    )}
                  </ul>
                </section>

                <section>
                  <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2">What is there</h3>
                  <ul className="space-y-2">
                    {(detail.mineral_evidence || []).map((m) => (
                      <li key={String(m.id)} className="rounded-lg border border-slate-200 px-3 py-2 text-sm">
                        <div className="font-medium">{String(m.mine_name || m.prospect_name || "Occurrence")}</div>
                        <div className="text-xs text-slate-500">
                          {m.inside_parcel ? "Inside parcel" : `${m.distance_meters ?? "?"} m away`} ·{" "}
                          {String(m.commodity_normalized || "commodity unknown")} ·{" "}
                          {String(m.production_status || "")}
                        </div>
                      </li>
                    ))}
                    {(detail.claim_context || []).map((c) => (
                      <li key={String(c.id)} className="rounded-lg border border-dashed border-slate-200 px-3 py-2 text-sm text-slate-600">
                        Nearby MLRS {String(c.claim_status)} · {String(c.claim_name || c.mlrs_serial_number)} ·{" "}
                        {String(c.distance_meters)} m
                      </li>
                    ))}
                  </ul>
                </section>

                <section>
                  <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2">Tax timeline</h3>
                  <ol className="space-y-2 border-l border-slate-200 pl-3">
                    {(detail.timeline || []).map((ev) => (
                      <li key={String(ev.id)} className="text-sm">
                        <div className="font-medium text-slate-800">{String(ev.title || ev.event_type)}</div>
                        <div className="text-xs text-slate-500">{formatDate(ev.event_at as string)}</div>
                      </li>
                    ))}
                  </ol>
                </section>

                {scoreExpl && (
                  <section>
                    <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2">Score breakdown</h3>
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
                  <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2">Evidence ledger</h3>
                  <div className="space-y-2">
                    {(detail.evidence_ledger || []).map((e) => {
                      const val = (e.fact_value_json as { value?: unknown })?.value;
                      return (
                        <div key={String(e.id)} className="rounded-lg bg-slate-50 px-3 py-2 text-xs">
                          <div className="flex justify-between gap-2">
                            <span className="font-medium text-slate-800">{String(e.fact_key)}</span>
                            <span className="text-slate-500">{String(e.evidence_class)}</span>
                          </div>
                          <div className="text-slate-700 mt-0.5 break-words">{String(val ?? "")}</div>
                          <div className="text-slate-400 mt-1">
                            {String(e.source_name || "source")} · conf {Math.round(Number(e.confidence || 0) * 100)}% ·{" "}
                            {String(e.extraction_method)}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </section>

                <p className="text-[11px] text-slate-500 leading-relaxed border-t border-slate-100 pt-3">
                  {detail.disclaimer}
                </p>
                <Link to="/map" className="text-sm text-primary-700 hover:underline">
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
            Analyst tasks for ambiguous patents, access, and source conflicts. Decisions never erase source observations.
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
                      {String(task.best_name)} · {String(task.state)} {String(task.county_name)}
                    </div>
                    <div className="text-xs text-slate-500 mt-1">{String(task.instructions || "")}</div>
                  </div>
                  <div className="text-xs text-slate-500 whitespace-nowrap">
                    {String(task.task_type)} · tier {String(task.priority_tier)}
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
            {coverage?.coverage_language || "Pilot county registry for UT / ID / NV."}
            {" "}
            Sources refresh via fixture/CSV/ArcGIS adapters when jobs are enabled
            (<code className="text-xs bg-slate-100 px-1 rounded">ENABLE_TAX_SALES_JOBS=true</code>).
            Live HTML scrapes are opt-in per source; counties without stable feeds use fixture or manual CSV upload.
          </p>
          <div className="overflow-auto rounded-xl border border-slate-200 bg-white">
            <table className="min-w-full text-sm">
              <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="px-3 py-2 text-left">State</th>
                  <th className="px-3 py-2 text-left">County</th>
                  <th className="px-3 py-2 text-left">Source</th>
                  <th className="px-3 py-2 text-left">Scope</th>
                  <th className="px-3 py-2 text-left">Health</th>
                  <th className="px-3 py-2 text-left">Records</th>
                  <th className="px-3 py-2 text-left">Last success</th>
                </tr>
              </thead>
              <tbody>
                {(coverage?.jurisdictions || []).map((j) => (
                  <tr key={String(j.id)} className="border-t border-slate-100">
                    <td className="px-3 py-2">{String(j.state)}</td>
                    <td className="px-3 py-2">{String(j.county_name)}</td>
                    <td className="px-3 py-2">
                      <div className="font-medium">{String(j.name)}</div>
                      <div className="text-xs text-slate-500">{String(j.parser_kind)}</div>
                    </td>
                    <td className="px-3 py-2 text-xs">{statusLabel(String(j.publication_scope))}</td>
                    <td className="px-3 py-2">
                      <span className={`inline-flex px-2 py-0.5 rounded-full text-[11px] font-medium ${healthClass(String(j.health_status))}`}>
                        {String(j.health_status)}
                      </span>
                    </td>
                    <td className="px-3 py-2">{String(j.record_count ?? 0)}</td>
                    <td className="px-3 py-2 text-xs text-slate-500">{formatDate(j.last_success_at as string)}</td>
                  </tr>
                ))}
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
