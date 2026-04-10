import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, ApiError, type Area } from "../api";

type SortKey = "state" | "plss" | "magnitude" | "reports";

const CLAIM_TYPE_OPTIONS = [
  { value: "", label: "All claim types" },
  { value: "patented", label: "Patented" },
  { value: "unpatented", label: "Unpatented" },
  { value: "mining_claim", label: "Mining claims" },
];

export function MineralLocations() {
  const { mineralName } = useParams<{ mineralName: string }>();
  const decodedMineral = mineralName ? decodeURIComponent(mineralName) : "";
  const [areas, setAreas] = useState<Area[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [stateFilter, setStateFilter] = useState("");
  const [claimTypeFilter, setClaimTypeFilter] = useState("");
  const [sortBy, setSortBy] = useState<SortKey>("magnitude");
  const [sortDesc, setSortDesc] = useState(true);

  const load = useCallback(() => {
    if (!decodedMineral) return;
    setLoading(true);
    setError(null);
    api.areas
      .list({
        mineral: decodedMineral,
        state_abbr: stateFilter || undefined,
        claim_type: claimTypeFilter || undefined,
        limit: 1000,
      })
      .then(setAreas)
      .catch((e) => {
        setError(e instanceof ApiError && e.body?.detail ? e.body.detail : (e as Error).message);
      })
      .finally(() => setLoading(false));
  }, [decodedMineral, stateFilter, claimTypeFilter]);

  useEffect(() => load(), [load]);

  const states = useMemo(() => {
    const set = new Set<string>();
    areas.forEach((a) => {
      const s = (a.state_abbr || "").trim();
      if (s) set.add(s);
    });
    return Array.from(set).sort();
  }, [areas]);

  const sortedAreas = useMemo(() => {
    const list = [...areas];
    list.sort((a, b) => {
      let cmp = 0;
      switch (sortBy) {
        case "state":
          cmp = (a.state_abbr || "").localeCompare(b.state_abbr || "");
          break;
        case "plss":
          cmp = (a.location_plss || "").localeCompare(b.location_plss || "");
          break;
        case "magnitude":
          cmp = (a.magnitude_score ?? 0) - (b.magnitude_score ?? 0);
          break;
        case "reports":
          cmp = (a.report_count ?? 0) - (b.report_count ?? 0);
          break;
        default:
          break;
      }
      return sortDesc ? -cmp : cmp;
    });
    return list;
  }, [areas, sortBy, sortDesc]);

  if (!decodedMineral) {
    return (
      <div>
        <p className="text-slate-600">Mineral not specified.</p>
        <Link to="/minerals" className="text-primary-600 hover:underline mt-2 inline-block">
          ← Back to Minerals
        </Link>
      </div>
    );
  }

  return (
    <div>
      <div className="mb-6">
        <Link to="/minerals" className="text-primary-600 hover:underline text-sm mb-2 inline-block">
          ← Back to Minerals
        </Link>
        <h1 className="text-2xl font-bold text-slate-900">
          List of locations — {decodedMineral}
        </h1>
        <p className="text-slate-600 mt-1">
          All targets for this mineral. Sort and filter below.
        </p>
      </div>

      {error && (
        <div className="mb-6 p-4 bg-red-50 border border-red-200 rounded-lg text-red-800 text-sm">
          {error}
        </div>
      )}

      {/* Filters and sort */}
      <div className="bg-white rounded-xl border border-slate-200 shadow-card p-4 mb-6 flex flex-wrap items-center gap-4">
        <span className="text-sm font-medium text-slate-700">Filters & sort</span>
        <select
          value={stateFilter}
          onChange={(e) => setStateFilter(e.target.value)}
          className="px-3 py-2 border border-slate-200 rounded-lg text-sm focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
          title="Filter by state"
        >
          <option value="">All states</option>
          {states.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <select
          value={claimTypeFilter}
          onChange={(e) => setClaimTypeFilter(e.target.value)}
          className="px-3 py-2 border border-slate-200 rounded-lg text-sm focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
          title="Filter by claim type"
        >
          {CLAIM_TYPE_OPTIONS.map((o) => (
            <option key={o.value || "all"} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        <label className="flex items-center gap-2 text-sm text-slate-700">
          Sort by
          <select
            value={sortBy}
            onChange={(e) => setSortBy(e.target.value as SortKey)}
            className="px-3 py-2 border border-slate-200 rounded-lg text-sm focus:ring-2 focus:ring-primary-500"
          >
            <option value="magnitude">Magnitude score</option>
            <option value="reports">Report count</option>
            <option value="state">State</option>
            <option value="plss">PLSS</option>
          </select>
          <button
            type="button"
            onClick={() => setSortDesc((d) => !d)}
            className="px-2 py-1 border border-slate-200 rounded text-xs hover:bg-slate-50"
            title={sortDesc ? "Descending" : "Ascending"}
          >
            {sortDesc ? "↓" : "↑"}
          </button>
        </label>
      </div>

      <div className="bg-white rounded-xl border border-slate-200 shadow-card overflow-hidden">
        {loading ? (
          <div className="p-8 text-center text-slate-500">Loading…</div>
        ) : sortedAreas.length === 0 ? (
          <div className="p-8 text-center text-slate-500">
            No locations for this mineral with the current filters.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-200 bg-slate-50">
                  <th className="text-left py-3 px-4 font-semibold text-slate-700">State</th>
                  <th className="text-left py-3 px-4 font-semibold text-slate-700">PLSS / Location</th>
                  <th className="text-left py-3 px-4 font-semibold text-slate-700">Magnitude score</th>
                  <th className="text-left py-3 px-4 font-semibold text-slate-700">Reports</th>
                </tr>
              </thead>
              <tbody>
                {sortedAreas.map((a) => (
                  <tr key={a.id} className="border-b border-slate-100 hover:bg-slate-50">
                    <td className="py-3 px-4 text-slate-700">{a.state_abbr || "—"}</td>
                    <td className="py-3 px-4 text-slate-700">{a.location_plss || a.location_coords || "—"}</td>
                    <td className="py-3 px-4 text-slate-700">{a.magnitude_score ?? "—"}</td>
                    <td className="py-3 px-4">
                      {(a.report_count ?? 0) > 0 ? (
                        <Link
                          to={`/areas?areaId=${a.id}`}
                          className="text-primary-600 hover:underline font-medium"
                        >
                          {a.report_count}
                        </Link>
                      ) : (
                        <span className="text-slate-400">0</span>
                      )}
                    </td>
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
