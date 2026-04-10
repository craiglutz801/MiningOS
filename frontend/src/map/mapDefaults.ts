import type { LatLngTuple } from "leaflet";

export const DEFAULT_CENTER: LatLngTuple = [39.5, -111.5];
export const DEFAULT_ZOOM = 5;
export const MAX_FIT_ZOOM = 12;
export const DEFAULT_BOUNDS_PADDING: [number, number] = [24, 24];

export const LOCAL_STORAGE_KEYS = {
  basemap: "mining_os_map_basemap",
  overlays: "mining_os_map_overlays",
} as const;
