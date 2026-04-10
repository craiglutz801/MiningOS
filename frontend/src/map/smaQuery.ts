import type { SmaAttributes } from "./agencyLabels";

interface ArcgisQueryResponse {
  features?: { attributes: Record<string, unknown> }[];
  error?: { message?: string };
}

export interface SmaQueryResult {
  ok: true;
  attributes: SmaAttributes;
}

export interface SmaQueryError {
  ok: false;
  message: string;
}

export type SmaQueryResponse = SmaQueryResult | SmaQueryError;

/**
 * Query BLM SMA at a point via our API (`/api/map/sma-query`) so it works from any dev origin
 * without browser CORS issues.
 */
export async function querySurfaceManagementAtPoint(lat: number, lng: number): Promise<SmaQueryResponse> {
  const q = new URLSearchParams({ lat: String(lat), lng: String(lng) });

  let res: Response;
  try {
    res = await fetch(`/api/map/sma-query?${q.toString()}`, {
      method: "GET",
      headers: { Accept: "application/json" },
    });
  } catch (e) {
    return { ok: false, message: e instanceof Error ? e.message : "Network error" };
  }

  let data: ArcgisQueryResponse;
  try {
    data = (await res.json()) as ArcgisQueryResponse;
  } catch {
    return { ok: false, message: "Invalid response from land records service" };
  }

  if (!res.ok) {
    const detail =
      typeof (data as { detail?: string }).detail === "string"
        ? (data as { detail: string }).detail
        : `Request failed (${res.status})`;
    return { ok: false, message: detail };
  }

  if (data.error?.message) {
    return { ok: false, message: data.error.message };
  }

  const feat = data.features?.[0];
  if (!feat?.attributes) {
    return { ok: false, message: "No surface management polygon here (try another location)." };
  }

  return { ok: true, attributes: feat.attributes as SmaAttributes };
}
