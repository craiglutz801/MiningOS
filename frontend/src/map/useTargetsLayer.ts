import { useEffect, useMemo, useState } from "react";
import L from "leaflet";
import { api, type Area } from "../api";

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

function normalize(a: Area): MapTarget {
  return {
    id: a.id,
    name: a.name,
    latitude: a.latitude!,
    longitude: a.longitude!,
    minerals: a.minerals || [],
    status: (a.status || "unknown").toLowerCase(),
    priority: (a.priority || "monitoring_low").toLowerCase(),
    claimType: a.claim_type || "",
    plss: a.location_plss || "",
  };
}

export function useTargetsLayer(selectedAreaId?: string | null) {
  const [raw, setRaw] = useState<Area[]>([]);
  const [isLoading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.areas
      .list({ limit: 1000 })
      .then((list) => setRaw(list))
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
