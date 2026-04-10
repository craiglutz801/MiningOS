import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError, type DiscoveryRunSummary } from "../api";

function formatDate(iso: string | null | undefined) {
  if (iso == null || iso === "") return "—";
  try {
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? iso : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  } catch {
    return String(iso);
  }
}

export function Discoveries() {
  const [runs, setRuns] = useState<DiscoveryRunSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    setLoading(true);
    setError(null);
    api.discovery
      .listRuns(100)
      .then((data) => setRuns(Array.isArray(data) ? data : []))
      .catch((e) => {
        const msg = e instanceof ApiError ? (e.body?.detail as string) || e.message : String(e);
        if (e instanceof ApiError && e.status === 404) {
          setError("Discovery runs API not found. Restart the backend (uvicorn) so it loads the latest routes, then refresh.");
        } else {
          setError(msg);
        }
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => load(), []);

  return (
    <div>
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-slate-900 mb-2">Discovery runs</h1>
        <p className="text-slate-600">
          Log of every discovery agent run. Click a row to see full goal, log, and output.
        </p>
      </div>

      {error && (
        <div className="mb-6 p-4 bg-red-50 border border-red-200 rounded-lg text-red-800 text-sm flex flex-wrap items-center gap-3">
          <span className="flex-1">{error}</span>
          <button
            type="button"
            onClick={() => load()}
            className="px-3 py-1.5 bg-red-100 hover:bg-red-200 rounded text-red-800 text-sm font-medium"
          >
            Retry
          </button>
        </div>
      )}

      <div className="bg-white rounded-xl border border-slate-200 shadow-card overflow-hidden">
        {loading ? (
          <div className="p-8 text-center text-slate-500">Loading…</div>
        ) : runs.length === 0 ? (
          <div className="p-8 text-center text-slate-500">
            No discovery runs yet. Run discovery from the{" "}
            <Link to="/" className="text-primary-600 underline">Dashboard</Link> (Discovery agent).
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-200 bg-slate-50">
                  <th className="text-left py-3 px-4 font-semibold text-slate-700">Date</th>
                  <th className="text-left py-3 px-4 font-semibold text-slate-700">Mode</th>
                  <th className="text-left py-3 px-4 font-semibold text-slate-700">Status</th>
                  <th className="text-right py-3 px-4 font-semibold text-slate-700">Targets added</th>
                  <th className="text-right py-3 px-4 font-semibold text-slate-700">Minerals</th>
                  <th className="text-right py-3 px-4 font-semibold text-slate-700">Log lines</th>
                  <th className="text-right py-3 px-4 font-semibold text-slate-700">Errors</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((r) => (
                  <tr
                    key={r.id}
                    className="border-b border-slate-100 hover:bg-primary-50/50"
                  >
                    <td className="py-3 px-4 text-slate-700 whitespace-nowrap">
                      <Link to={`/discoveries/${r.id}`} className="text-primary-600 hover:underline font-medium">
                        {formatDate(r.created_at)}
                      </Link>
                    </td>
                    <td className="py-3 px-4 text-slate-600">
                      {r.replace ? "Replace" : "Add / supplement"}
                    </td>
                    <td className="py-3 px-4">
                      <span
                        className={`inline-flex px-2 py-0.5 rounded text-xs font-medium ${
                          r.status === "ok"
                            ? "bg-emerald-100 text-emerald-800"
                            : r.status === "error"
                              ? "bg-red-100 text-red-800"
                              : "bg-slate-100 text-slate-600"
                        }`}
                      >
                        {r.status}
                      </span>
                    </td>
                    <td className="py-3 px-4 text-right text-slate-700">{r.areas_added ?? 0}</td>
                    <td className="py-3 px-4 text-right text-slate-600">
                      {r.minerals_checked?.length ?? 0}
                    </td>
                    <td className="py-3 px-4 text-right text-slate-500">{r.log_line_count ?? 0}</td>
                    <td className="py-3 px-4 text-right text-slate-500">{r.error_count ?? 0}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
