/**
 * Local USGS USMIN mine-feature points, served by our own API from the state
 * KMZ files (data_files/usmin_points/*). Viewport-filtered + capped so the
 * browser only receives what it can cluster smoothly.
 *
 * Records are compact arrays from the backend: [lon, lat, type, name, usmin_id].
 */

export interface UsminPoint {
  lon: number;
  lat: number;
  type: string;
  name: string;
  usminId: string;
}

export interface UsminPointsResult {
  points: UsminPoint[];
  count: number;
  capped: boolean;
}

type RawRecord = [number, number, string, string, string];

interface RawResponse {
  points: RawRecord[];
  count: number;
  capped: boolean;
}

export async function fetchUsminInBounds(
  west: number,
  south: number,
  east: number,
  north: number,
  limit: number,
  signal?: AbortSignal,
): Promise<UsminPointsResult> {
  const bbox = `${west},${south},${east},${north}`;
  const url = `/api/map/usmin-points?bbox=${encodeURIComponent(bbox)}&limit=${limit}`;
  const res = await fetch(url, { credentials: "include", signal });
  if (!res.ok) {
    throw new Error(`USMIN request failed (${res.status})`);
  }
  const data = (await res.json()) as RawResponse;
  if (!data || !Array.isArray(data.points)) {
    throw new Error("Invalid USMIN response");
  }
  const points: UsminPoint[] = data.points.map((r) => ({
    lon: r[0],
    lat: r[1],
    type: r[2] || "",
    name: r[3] || "",
    usminId: r[4] || "",
  }));
  return { points, count: data.count ?? points.length, capped: !!data.capped };
}

/** USGS USMIN public detail page for a point id. */
export function usminDetailUrl(usminId: string): string {
  return `https://mrdata.usgs.gov/usmin/show-usmin.php?type=point&id=${encodeURIComponent(usminId)}`;
}
