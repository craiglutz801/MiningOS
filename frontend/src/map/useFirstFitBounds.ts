import { useEffect, useRef } from "react";
import { useMap } from "react-leaflet";
import type { LatLngBounds } from "leaflet";
import { MAX_FIT_ZOOM, DEFAULT_BOUNDS_PADDING } from "./mapDefaults";

export function useFirstFitBounds(bounds: LatLngBounds | null) {
  const map = useMap();
  const hasFit = useRef(false);

  useEffect(() => {
    if (!bounds || hasFit.current) return;
    map.fitBounds(bounds, { padding: DEFAULT_BOUNDS_PADDING, maxZoom: MAX_FIT_ZOOM });
    hasFit.current = true;
  }, [map, bounds]);
}

export function FirstFitBounds({ bounds }: { bounds: LatLngBounds | null }) {
  useFirstFitBounds(bounds);
  return null;
}
