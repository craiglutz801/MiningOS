import { LOCAL_STORAGE_KEYS } from "./mapDefaults";
import type { BasemapKey, OverlayKey } from "./layerTypes";
import { BASEMAPS } from "./basemaps";
import { OVERLAYS } from "./overlays";

function safeGet(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function safeSet(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* storage unavailable */
  }
}

export function getStoredBasemap(): BasemapKey {
  const raw = safeGet(LOCAL_STORAGE_KEYS.basemap);
  if (raw && raw in BASEMAPS) return raw as BasemapKey;
  return "satellite";
}

export function setStoredBasemap(key: BasemapKey) {
  safeSet(LOCAL_STORAGE_KEYS.basemap, key);
}

export function getStoredOverlayVisibility(): Record<OverlayKey, boolean> {
  const defaults: Record<OverlayKey, boolean> = {} as Record<OverlayKey, boolean>;
  for (const def of Object.values(OVERLAYS)) {
    defaults[def.key] = def.visibleByDefault;
  }

  const raw = safeGet(LOCAL_STORAGE_KEYS.overlays);
  if (!raw) return defaults;

  try {
    const parsed = JSON.parse(raw) as Record<string, boolean>;
    const result = { ...defaults };
    for (const key of Object.keys(result) as OverlayKey[]) {
      if (typeof parsed[key] === "boolean") {
        result[key] = parsed[key];
      }
    }
    return result;
  } catch {
    return defaults;
  }
}

export function setStoredOverlayVisibility(state: Record<OverlayKey, boolean>) {
  safeSet(LOCAL_STORAGE_KEYS.overlays, JSON.stringify(state));
}
