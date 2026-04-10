import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, ApiError, type DiscoveryRun } from "../api";

function formatDate(iso: string) {
  try {
    const d = new Date(iso);
    return d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  } catch {
    return iso;
  }
}

export function DiscoveryDetail() {
  const { id } = useParams<{ id: string }>();
  const [run, setRun] = useState<DiscoveryRun | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const numId = id ? parseInt(id, 10) : NaN;
    if (Number.isNaN(numId)) {
      setError("Invalid run id");
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    api.discovery
      .getRun(numId)
      .then(setRun)
      .catch((e) => setError(e instanceof ApiError ? (e.body?.detail as string) || e.message : String(e)))
      .finally(() => setLoading(false));
  }, [id]);

  if (loading) {
    return (
      <div className="p-8 text-center text-slate-500">Loading…</div>
    );
  }
  if (error || !run) {
    return (
      <div>
        <div className="mb-6 p-4 bg-red-50 border border-red-200 rounded-lg text-red-800 text-sm flex flex-wrap items-center gap-3">
          <span className="flex-1">{error ?? "Run not found."}</span>
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="px-3 py-1.5 bg-red-100 hover:bg-red-200 rounded text-red-800 text-sm font-medium"
          >
            Retry
          </button>
        </div>
        <Link to="/discoveries" className="text-primary-600 hover:underline">← Back to Discovery runs</Link>
      </div>
    );
  }

  return (
    <div>
      <div className="mb-6 flex flex-wrap items-center gap-4">
        <Link to="/discoveries" className="text-primary-600 hover:underline text-sm">← Discovery runs</Link>
      </div>

      <div className="mb-8">
        <h1 className="text-2xl font-bold text-slate-900 mb-1">Discovery run #{run.id}</h1>
        <p className="text-slate-600 text-sm">{formatDate(run.created_at)}</p>
      </div>

      {/* What we were trying to accomplish */}
      <section className="mb-8 bg-white rounded-xl border border-slate-200 shadow-card overflow-hidden">
        <h2 className="px-6 py-3 border-b border-slate-200 bg-slate-50 font-semibold text-slate-800">
          What we were trying to accomplish
        </h2>
        <div className="p-6 space-y-3 text-sm">
          <div className="flex flex-wrap gap-4">
            <span>
              <strong className="text-slate-700">Mode:</strong>{" "}
              {run.replace ? "Replace discovery list (removed existing discovery-sourced targets first)" : "Add to / supplement existing list"}
            </span>
            <span>
              <strong className="text-slate-700">Limit per mineral:</strong> {run.limit_per_mineral}
            </span>
            <span>
              <strong className="text-slate-700">Status:</strong>{" "}
              <span
                className={`inline-flex px-2 py-0.5 rounded text-xs font-medium ${
                  run.status === "ok"
                    ? "bg-emerald-100 text-emerald-800"
                    : run.status === "error"
                      ? "bg-red-100 text-red-800"
                      : "bg-slate-100 text-slate-600"
                }`}
              >
                {run.status}
              </span>
            </span>
          </div>
          {run.minerals_checked && run.minerals_checked.length > 0 && (
            <div>
              <strong className="text-slate-700">Minerals checked:</strong>{" "}
              {run.minerals_checked.join(", ")}
            </div>
          )}
          {run.message && (
            <div className="p-3 bg-amber-50 border border-amber-200 rounded-lg text-amber-900">
              {run.message}
            </div>
          )}
          <div>
            <strong className="text-slate-700">Targets added:</strong> {run.areas_added ?? 0}
          </div>
        </div>
      </section>

      {/* Locations from AI */}
      <section className="mb-8 bg-white rounded-xl border border-slate-200 shadow-card overflow-hidden">
        <h2 className="px-6 py-3 border-b border-slate-200 bg-slate-50 font-semibold text-slate-800">
          Mines / claims / locations from AI
        </h2>
        <div className="overflow-x-auto">
          {run.locations_from_ai && run.locations_from_ai.length > 0 ? (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-200 bg-slate-50">
                  <th className="text-left py-3 px-4 font-semibold text-slate-700">Name</th>
                  <th className="text-left py-3 px-4 font-semibold text-slate-700">State</th>
                  <th className="text-left py-3 px-4 font-semibold text-slate-700">PLSS</th>
                  <th className="text-left py-3 px-4 font-semibold text-slate-700">Mineral</th>
                  <th className="text-left py-3 px-4 font-semibold text-slate-700">Notes</th>
                </tr>
              </thead>
              <tbody>
                {run.locations_from_ai.map((loc, i) => (
                  <tr key={i} className="border-b border-slate-100">
                    <td className="py-3 px-4 font-medium text-slate-900">{loc.name}</td>
                    <td className="py-3 px-4 text-slate-600">{loc.state}</td>
                    <td className="py-3 px-4 text-slate-600">{loc.plss || "—"}</td>
                    <td className="py-3 px-4 text-slate-600">{loc.mineral}</td>
                    <td className="py-3 px-4 text-slate-600 max-w-xs truncate" title={loc.notes || ""}>{loc.notes || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="p-6 text-slate-500 text-sm">No locations from AI in this run. (Older runs may not have this data.)</p>
          )}
        </div>
      </section>

      {/* URLs from web search */}
      <section className="mb-8 bg-white rounded-xl border border-slate-200 shadow-card overflow-hidden">
        <h2 className="px-6 py-3 border-b border-slate-200 bg-slate-50 font-semibold text-slate-800">
          Report URLs from web search
        </h2>
        <div className="p-6">
          {run.urls_from_web_search && run.urls_from_web_search.length > 0 ? (
            <ul className="space-y-2 text-sm">
              {run.urls_from_web_search.map((url, i) => (
                <li key={i}>
                  <a href={url} target="_blank" rel="noopener noreferrer" className="text-primary-600 hover:underline break-all">
                    {url}
                  </a>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-slate-500 text-sm">No report URLs from web search in this run. (Older runs may not have this data.)</p>
          )}
        </div>
      </section>

      {/* Full log */}
      <section className="mb-8 bg-white rounded-xl border border-slate-200 shadow-card overflow-hidden">
        <h2 className="px-6 py-3 border-b border-slate-200 bg-slate-50 font-semibold text-slate-800">
          Run log
        </h2>
        <div className="p-4 bg-slate-900 text-slate-100 font-mono text-xs overflow-x-auto max-h-96 overflow-y-auto">
          {(run.log && run.log.length > 0) ? (
            <pre className="whitespace-pre-wrap break-words">
              {run.log.map((line, i) => (
                <div key={i} className="text-slate-300">{line}</div>
              ))}
            </pre>
          ) : (
            <span className="text-slate-500">No log lines.</span>
          )}
        </div>
      </section>

      {/* Errors if any */}
      {run.errors && run.errors.length > 0 && (
        <section className="mb-8 bg-white rounded-xl border border-slate-200 shadow-card overflow-hidden">
          <h2 className="px-6 py-3 border-b border-slate-200 bg-red-50 font-semibold text-red-800">
            Errors ({run.errors.length})
          </h2>
          <ul className="p-6 list-disc list-inside text-sm text-red-800 space-y-1">
            {run.errors.map((err, i) => (
              <li key={i}>{err}</li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
