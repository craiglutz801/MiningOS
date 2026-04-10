import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, ApiError, type Mineral, type MineralReport } from "../api";

function ReportModal({
  mineral,
  onClose,
}: {
  mineral: Mineral;
  onClose: () => void;
}) {
  const [report, setReport] = useState<MineralReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    api.minerals
      .report(mineral.id)
      .then((r) => {
        if (!cancelled) setReport(r);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof ApiError ? (e.body?.detail as string) || e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [mineral.id]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-labelledby="report-modal-title"
    >
      <div
        className="bg-white rounded-2xl shadow-xl max-w-2xl w-full max-h-[90vh] overflow-hidden flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-200">
          <h2 id="report-modal-title" className="text-xl font-bold text-slate-900">
            {mineral.name} — Report
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="p-2 text-slate-400 hover:text-slate-600 rounded-lg hover:bg-slate-100"
            aria-label="Close"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
        <div className="px-6 py-4 overflow-y-auto flex-1">
          {loading && (
            <div className="py-8 text-center text-slate-500">
              <p>Loading report…</p>
            </div>
          )}
          {error && (
            <div className="py-4 p-4 bg-red-50 border border-red-200 rounded-xl text-red-800 text-sm">
              {error}
            </div>
          )}
          {report && !loading && (
            <div className="space-y-5">
              {report.error && (
                <div className="p-3 bg-amber-50 border border-amber-200 rounded-lg text-amber-800 text-sm">
                  {report.error}
                </div>
              )}
              {report.overview && (
                <section>
                  <h3 className="text-sm font-semibold text-slate-500 uppercase tracking-wide mb-1">Overview</h3>
                  <p className="text-slate-700">{report.overview}</p>
                </section>
              )}
              {report.uses?.length > 0 && (
                <section>
                  <h3 className="text-sm font-semibold text-slate-500 uppercase tracking-wide mb-1">Uses</h3>
                  <ul className="list-disc list-inside text-slate-700 space-y-0.5">
                    {report.uses.map((u, i) => (
                      <li key={i}>{u}</li>
                    ))}
                  </ul>
                </section>
              )}
              {report.key_buyers?.length > 0 && (
                <section>
                  <h3 className="text-sm font-semibold text-slate-500 uppercase tracking-wide mb-1">Key buyers</h3>
                  <ul className="list-disc list-inside text-slate-700 space-y-0.5">
                    {report.key_buyers.map((b, i) => (
                      <li key={i}>{b}</li>
                    ))}
                  </ul>
                </section>
              )}
              {report.major_mining_operations?.length > 0 && (
                <section>
                  <h3 className="text-sm font-semibold text-slate-500 uppercase tracking-wide mb-1">
                    Major mining operations
                  </h3>
                  <ul className="list-disc list-inside text-slate-700 space-y-0.5">
                    {report.major_mining_operations.map((op, i) => (
                      <li key={i}>{op}</li>
                    ))}
                  </ul>
                </section>
              )}
              {report.common_formations?.length > 0 && (
                <section>
                  <h3 className="text-sm font-semibold text-slate-500 uppercase tracking-wide mb-1">
                    Common formations
                  </h3>
                  <ul className="list-disc list-inside text-slate-700 space-y-0.5">
                    {report.common_formations.map((f, i) => (
                      <li key={i}>{f}</li>
                    ))}
                  </ul>
                </section>
              )}
              {report.prevalent_locations?.length > 0 && (
                <section>
                  <h3 className="text-sm font-semibold text-slate-500 uppercase tracking-wide mb-1">
                    Prevalent locations
                  </h3>
                  <ul className="list-disc list-inside text-slate-700 space-y-0.5">
                    {report.prevalent_locations.map((loc, i) => (
                      <li key={i}>{loc}</li>
                    ))}
                  </ul>
                </section>
              )}
              {report.mining_and_milling && (
                <section>
                  <h3 className="text-sm font-semibold text-slate-500 uppercase tracking-wide mb-1">
                    Mining &amp; milling
                  </h3>
                  <p className="text-slate-700">{report.mining_and_milling}</p>
                </section>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export function Minerals() {
  const navigate = useNavigate();
  const [list, setList] = useState<Mineral[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const [adding, setAdding] = useState(false);
  const [reportMineral, setReportMineral] = useState<Mineral | null>(null);
  const [actionMineral, setActionMineral] = useState<Mineral | null>(null);
  const [actionAnchorRect, setActionAnchorRect] = useState<DOMRect | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    api.minerals
      .list()
      .then(setList)
      .catch((e) => {
        if (e instanceof ApiError && e.status === 503 && e.body?.error === "database_unavailable") {
          setError("DB_SETUP");
        } else {
          setError(e instanceof ApiError && e.body?.detail ? e.body.detail : (e as Error).message);
        }
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => load(), [load]);

  const add = async () => {
    const name = newName.trim();
    if (!name) return;
    setAdding(true);
    try {
      await api.minerals.add(name);
      setNewName("");
      load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setAdding(false);
    }
  };

  const remove = async (id: number) => {
    if (!confirm("Remove this mineral?")) return;
    try {
      await api.minerals.delete(id);
      setActionMineral(null);
      setActionAnchorRect(null);
      load();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const duplicate = async (m: Mineral) => {
    try {
      await api.minerals.add(`${m.name} (copy)`);
      setActionMineral(null);
      setActionAnchorRect(null);
      load();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <div>
      <h1 className="text-2xl font-bold text-slate-900 mb-2">Minerals of interest</h1>
      <p className="text-slate-600 mb-8">
        Edit the list of priority minerals. Click a mineral to see its targets. Use the report icon for a detailed AI report.
      </p>

      {error && error === "DB_SETUP" && (
        <div className="mb-6 p-6 bg-amber-50 border border-amber-200 rounded-xl text-amber-900 text-sm">
          <strong>Database not running.</strong> On the{" "}
          <Link to="/" className="text-primary-600 underline">
            Dashboard
          </Link>{" "}
          see &quot;Set up the database&quot; for steps (Docker → <code>docker compose up -d</code> →{" "}
          <code>--init-db</code>).
        </div>
      )}
      {error && error !== "DB_SETUP" && (
        <div className="mb-6 p-4 bg-red-50 border border-red-200 rounded-lg text-red-800 text-sm">{error}</div>
      )}

      <div className="bg-white rounded-xl border border-slate-200 shadow-card overflow-hidden">
        <div className="p-4 border-b border-slate-100 flex flex-wrap items-center gap-3">
          <input
            type="text"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && add()}
            placeholder="Add mineral (e.g. Lithium)"
            className="flex-1 min-w-[200px] px-3 py-2 border border-slate-200 rounded-lg text-sm focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
          />
          <button
            onClick={add}
            disabled={adding || !newName.trim()}
            className="px-4 py-2 bg-primary-600 text-white rounded-lg text-sm font-medium hover:bg-primary-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {adding ? "Adding…" : "Add"}
          </button>
        </div>

        {loading ? (
          <div className="p-8 text-center text-slate-500">Loading…</div>
        ) : list.length === 0 ? (
          <div className="p-8 text-center text-slate-500">No minerals yet. Add one above.</div>
        ) : (
          <ul className="divide-y divide-slate-100">
            {list.map((m) => (
              <li
                key={m.id}
                className="flex items-center justify-between px-4 py-3 hover:bg-slate-50 group cursor-pointer"
                onClick={() => navigate(`/areas?mineral=${encodeURIComponent(m.name)}`)}
              >
                <span className="font-medium text-slate-900 group-hover:text-primary-600">
                  {m.name}
                </span>
                <span className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      setReportMineral(m);
                    }}
                    className="p-1.5 text-slate-400 hover:text-primary-600 hover:bg-primary-50 rounded-lg"
                    title="View report"
                    aria-label="View report"
                  >
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden>
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                    </svg>
                  </button>
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      setActionMineral(m);
                      setActionAnchorRect((e.currentTarget as HTMLElement).getBoundingClientRect());
                    }}
                    className="p-1.5 text-slate-400 hover:text-slate-600 hover:bg-slate-100 rounded-lg"
                    title="Edit"
                    aria-label="Edit"
                  >
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden>
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
                    </svg>
                  </button>
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>

      {reportMineral && (
        <ReportModal mineral={reportMineral} onClose={() => setReportMineral(null)} />
      )}

      {actionMineral && actionAnchorRect && (
        <div
          className="fixed inset-0 z-50"
          onClick={() => {
            setActionMineral(null);
            setActionAnchorRect(null);
          }}
          role="dialog"
          aria-modal="true"
          aria-label="Mineral actions"
        >
          <div
            className="absolute bg-white rounded-xl shadow-xl border border-slate-200 p-2 flex items-center gap-1"
            style={{
              left: actionAnchorRect.left + actionAnchorRect.width / 2,
              top: actionAnchorRect.top - 8,
              transform: "translate(-50%, -100%)",
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <button
              type="button"
              onClick={() => remove(actionMineral.id)}
              className="p-2 text-slate-500 hover:text-red-600 hover:bg-red-50 rounded-lg transition-colors"
              title="Delete"
              aria-label="Delete mineral"
            >
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
              </svg>
            </button>
            <button
              type="button"
              onClick={() => duplicate(actionMineral)}
              className="p-2 text-slate-500 hover:text-primary-600 hover:bg-primary-50 rounded-lg transition-colors"
              title="Duplicate"
              aria-label="Duplicate mineral"
            >
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
              </svg>
            </button>
            {/* Tail pointing down to the edit icon */}
            <span
              className="absolute left-1/2 -translate-x-1/2 w-0 h-0 border-[6px] border-transparent border-t-slate-200"
              style={{ bottom: -12 }}
              aria-hidden
            />
            <span
              className="absolute left-1/2 -translate-x-1/2 w-0 h-0 border-[5px] border-transparent border-t-white"
              style={{ bottom: -10 }}
              aria-hidden
            />
          </div>
        </div>
      )}
    </div>
  );
}
