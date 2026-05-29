import { useEffect, useMemo, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { api, ApiError, type SharedView, type SharedTarget } from "../api";

function formatCoord(lat: number | null, lon: number | null): string | null {
  if (lat == null || lon == null || !Number.isFinite(lat) || !Number.isFinite(lon)) return null;
  return `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
}

function formatDate(iso: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" });
}

function StatChip({ value, label }: { value: number | string; label: string }) {
  return (
    <div className="flex flex-col items-center rounded-xl bg-white/10 px-5 py-3 backdrop-blur-sm">
      <span className="text-2xl font-bold leading-none text-white">{value}</span>
      <span className="mt-1 text-[11px] font-medium uppercase tracking-wide text-emerald-100">{label}</span>
    </div>
  );
}

function TargetCard({ target, index }: { target: SharedTarget; index: number }) {
  const coords = formatCoord(target.latitude, target.longitude);
  const hasUnpaid = target.unpaid_claims.length > 0;

  return (
    <section className="break-inside-avoid overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-card">
      <div className="flex items-start justify-between gap-4 border-b border-slate-100 bg-slate-50/70 px-6 py-4">
        <div className="flex items-start gap-3">
          <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-primary-600 text-xs font-bold text-white">
            {index + 1}
          </span>
          <div>
            <h2 className="text-lg font-semibold leading-tight text-slate-900">{target.name}</h2>
            {target.location_plss && (
              <p className="mt-0.5 text-sm text-slate-500">{target.location_plss}</p>
            )}
          </div>
        </div>
        {hasUnpaid && (
          <span className="shrink-0 rounded-full border border-red-200 bg-red-50 px-3 py-1 text-[11px] font-semibold uppercase tracking-wide text-red-700">
            {target.unpaid_claims.length} unpaid
          </span>
        )}
      </div>

      <div className="grid grid-cols-1 gap-x-8 gap-y-5 px-6 py-5 sm:grid-cols-2">
        <div>
          <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">Latitude / Longitude</div>
          <div className="mt-1 text-sm text-slate-800">{coords ?? "—"}</div>
        </div>
        <div>
          <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">PLSS Coordinate</div>
          <div className="mt-1 text-sm text-slate-800">{target.location_plss ?? "—"}</div>
        </div>

        <div className="sm:col-span-2">
          <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">Minerals Present</div>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {target.minerals.length > 0 ? (
              target.minerals.map((m, i) => (
                <span
                  key={i}
                  className="inline-block rounded-full bg-primary-50 px-2.5 py-0.5 text-xs font-medium text-primary-700 ring-1 ring-inset ring-primary-100"
                >
                  {m}
                </span>
              ))
            ) : (
              <span className="text-sm text-slate-400">—</span>
            )}
          </div>
        </div>

        <div className="sm:col-span-2">
          <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">Known Reports</div>
          {target.reports.length > 0 ? (
            <ul className="mt-1.5 space-y-1">
              {target.reports.map((r, i) => (
                <li key={i} className="truncate text-sm">
                  <a
                    href={r}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-primary-600 hover:underline"
                  >
                    {r}
                  </a>
                </li>
              ))}
            </ul>
          ) : (
            <div className="mt-1 text-sm text-slate-400">No reports on file</div>
          )}
          {target.report_summary && (
            <p className="mt-2 text-sm leading-relaxed text-slate-600">{target.report_summary}</p>
          )}
        </div>
      </div>

      <div className="border-t border-slate-100 px-6 pb-6 pt-4">
        <div className="mb-2 flex items-center gap-2">
          <h3 className="text-sm font-semibold text-slate-800">Unpaid Claims</h3>
          <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] font-semibold text-slate-600">
            {target.unpaid_claims.length}
          </span>
        </div>
        {hasUnpaid ? (
          <div className="overflow-x-auto rounded-lg border border-red-100">
            <table className="min-w-full text-sm">
              <thead className="bg-red-50 text-left text-[11px] uppercase tracking-wide text-red-700">
                <tr>
                  <th className="px-3 py-2 font-semibold">Claim</th>
                  <th className="px-3 py-2 font-semibold">Serial</th>
                  <th className="px-3 py-2 font-semibold">Case</th>
                </tr>
              </thead>
              <tbody>
                {target.unpaid_claims.map((c, i) => (
                  <tr key={i} className="border-t border-red-50">
                    <td className="px-3 py-2 text-slate-800">
                      {c.claim_name ?? "—"}
                      {c.payment_message && (
                        <span className="mt-0.5 block text-[11px] text-slate-400">{c.payment_message}</span>
                      )}
                    </td>
                    <td className="whitespace-nowrap px-3 py-2 font-mono text-xs text-slate-600">
                      {c.serial_number ?? "—"}
                    </td>
                    <td className="whitespace-nowrap px-3 py-2">
                      {c.case_page ? (
                        <a
                          href={c.case_page}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="font-medium text-primary-600 hover:underline print:text-slate-700"
                        >
                          View case
                        </a>
                      ) : (
                        <span className="text-slate-400">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="rounded-lg border border-emerald-100 bg-emerald-50 px-3 py-2 text-sm text-emerald-700">
            No unpaid claims found for this target.
          </div>
        )}
      </div>
    </section>
  );
}

export function SharePage() {
  const { token } = useParams<{ token: string }>();
  const [searchParams] = useSearchParams();
  const [data, setData] = useState<SharedView | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      setLoading(true);
      setError(null);
      try {
        if (!token) throw new Error("Missing share token.");
        const view = await api.share.view(token);
        if (!cancelled) setData(view);
      } catch (e) {
        if (cancelled) return;
        if (e instanceof ApiError && e.status === 404) {
          setError("This share link is invalid or has expired.");
        } else {
          setError(e instanceof Error ? e.message : "Could not load shared targets.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [token]);

  const wantsPrint = searchParams.get("print") === "1";
  useEffect(() => {
    if (!loading && data && wantsPrint) {
      const t = setTimeout(() => window.print(), 400);
      return () => clearTimeout(t);
    }
  }, [loading, data, wantsPrint]);

  const generated = useMemo(() => formatDate(data?.created_at ?? null), [data?.created_at]);

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-50 px-6">
        <div className="flex flex-col items-center gap-4">
          <div className="h-10 w-10 animate-spin rounded-full border-4 border-slate-200 border-t-primary-600" />
          <p className="text-sm text-slate-500">Loading shared targets…</p>
        </div>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-50 px-6">
        <div className="w-full max-w-md rounded-2xl border border-slate-200 bg-white p-8 text-center shadow-card">
          <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-red-50 text-2xl">⚠️</div>
          <h1 className="mt-4 text-lg font-semibold text-slate-900">Link unavailable</h1>
          <p className="mt-2 text-sm text-slate-500">{error ?? "Shared targets could not be loaded."}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-50 text-slate-800 antialiased print:bg-white">
      <style>{`@media print {
        @page { margin: 14mm; }
        body { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
      }`}</style>

      <header className="bg-gradient-to-br from-primary-700 via-primary-600 to-emerald-500 print:bg-primary-700">
        <div className="mx-auto max-w-5xl px-6 py-10">
          <div className="flex flex-wrap items-start justify-between gap-6">
            <div>
              <div className="flex items-center gap-2 text-emerald-100">
                <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-white/15 text-sm font-bold text-white">
                  M
                </span>
                <span className="text-sm font-semibold uppercase tracking-widest">Mining OS</span>
              </div>
              <h1 className="mt-4 text-3xl font-bold leading-tight text-white">
                {data.title || "Shared Targets"}
              </h1>
              {generated && (
                <p className="mt-2 text-sm text-emerald-100">Shared {generated}</p>
              )}
            </div>
            <div className="flex flex-wrap gap-3">
              <StatChip value={data.target_count} label="Targets" />
              <StatChip value={data.unpaid_claim_count} label="Unpaid claims" />
            </div>
          </div>

          <div className="mt-8 print:hidden">
            <button
              type="button"
              onClick={() => window.print()}
              className="inline-flex items-center gap-2 rounded-lg bg-white px-4 py-2 text-sm font-semibold text-primary-700 shadow-sm transition-colors hover:bg-emerald-50"
            >
              <svg width="16" height="16" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
                <path d="M6 3a1 1 0 00-1 1v3h10V4a1 1 0 00-1-1H6zM4 8a2 2 0 00-2 2v3a2 2 0 002 2h1v-2a1 1 0 011-1h8a1 1 0 011 1v2h1a2 2 0 002-2v-3a2 2 0 00-2-2H4zm3 6a1 1 0 00-1 1v2h8v-2a1 1 0 00-1-1H7z" />
              </svg>
              Download PDF
            </button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-5xl px-6 py-10">
        {data.targets.length === 0 ? (
          <div className="rounded-2xl border border-slate-200 bg-white p-10 text-center text-slate-500 shadow-card">
            No targets are available in this share.
          </div>
        ) : (
          <div className="space-y-6">
            {data.targets.map((t, i) => (
              <TargetCard key={t.id} target={t} index={i} />
            ))}
          </div>
        )}

        <footer className="mt-10 border-t border-slate-200 pt-6 text-center text-xs text-slate-400">
          Generated by Mining OS · Data shown is read-only and may change over time.
        </footer>
      </main>
    </div>
  );
}
