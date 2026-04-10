import { useState, useCallback } from "react";
import type { BasemapKey, OverlayKey } from "./layerTypes";
import {
  getStoredBasemap,
  setStoredBasemap,
  getStoredOverlayVisibility,
  setStoredOverlayVisibility,
} from "./storage";

export function useMapPreferences() {
  const [basemap, setBasemapState] = useState<BasemapKey>(getStoredBasemap);
  const [visibleOverlays, setVisibleState] = useState<Record<OverlayKey, boolean>>(getStoredOverlayVisibility);

  const setBasemap = useCallback((key: BasemapKey) => {
    setBasemapState(key);
    setStoredBasemap(key);
  }, []);

  const setOverlayVisible = useCallback((key: OverlayKey, visible: boolean) => {
    setVisibleState((prev) => {
      const next = { ...prev, [key]: visible };
      setStoredOverlayVisibility(next);
      return next;
    });
  }, []);

  const toggleOverlay = useCallback((key: OverlayKey) => {
    setVisibleState((prev) => {
      const next = { ...prev, [key]: !prev[key] };
      setStoredOverlayVisibility(next);
      return next;
    });
  }, []);

  return { basemap, setBasemap, visibleOverlays, setOverlayVisible, toggleOverlay };
}
