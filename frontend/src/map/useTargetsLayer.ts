import { useEffect, useMemo, useState } from "react";
import L from "leaflet";

export interface MapTarget {
  id: number;
  name: string;
  latitude: number;
  longitude: number;
  minerals: string[];
  status: string;
  priority: string;
  claimType: string;
  plss: string;
}

/** Compact row from GET /api/map/targets (all targets with coordinates, no cap). */
interface MapTargetRow {
  id: number;
  name: string;
  latitude: number;
  longitude: number;
  minerals: string[];
  status: string;
  priority: string;
  claim_type: string;
  location_plss: string;
}

function normalize(a: MapTargetRow): MapTarget {
  return {
    id: a.id,
    name: a.name,
    latitude: a.latitude,
    longitude: a.longitude,
    minerals: a.minerals || [],
    status: (a.status || "unknown").toLowerCase(),
    priority: (a.priority || "monitoring_low").toLowerCase(),
    claimType: a.claim_type || "",
    plss: a.location_plss || "",
  };
}

export function useTargetsLayer(selectedAreaId?: string | null) {
  const [raw, setRaw] = useState<MapTargetRow[]>([]);
  const [isLoading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // Dedicated compact endpoint: the generic list API caps results, and any
    // cap on a recency-sorted list hides whole regions after bulk imports.
    fetch("/api/map/targets", { credentials: "include" })
      .then(async (res) => {
        if (!res.ok) throw new Error(`Failed to load map targets (${res.status})`);
        const data = (await res.json()) as MapTargetRow[];
        if (!Array.isArray(data)) throw new Error("Invalid map targets response");
        setRaw(data);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  const targets = useMemo(
    () =>
      raw
        .filter(
          (a) =>
            a.latitude != null &&
            a.longitude != null &&
            Number.isFinite(a.latitude) &&
            Number.isFinite(a.longitude),
        )
        .map(normalize),
    [raw],
  );

  const bounds = useMemo(() => {
    if (targets.length === 0) return null;
    return L.latLngBounds(targets.map((t) => [t.latitude, t.longitude] as [number, number]));
  }, [targets]);

  const selectedTarget = useMemo(() => {
    if (!selectedAreaId) return null;
    return targets.find((t) => String(t.id) === String(selectedAreaId)) || null;
  }, [targets, selectedAreaId]);

  return { targets, isLoading, error, bounds, selectedTarget };
}
