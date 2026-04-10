import { useState } from "react";
import type { BasemapKey, OverlayKey } from "../../map/layerTypes";
import { BASEMAPS, BASEMAP_KEYS } from "../../map/basemaps";
import { OVERLAYS, OVERLAY_KEYS } from "../../map/overlays";

interface MapLayerControlProps {
  basemap: BasemapKey;
  onBasemapChange: (key: BasemapKey) => void;
  visibleOverlays: Record<OverlayKey, boolean>;
  onToggleOverlay: (key: OverlayKey) => void;
}

export function MapLayerControl({
  basemap,
  onBasemapChange,
  visibleOverlays,
  onToggleOverlay,
}: MapLayerControlProps) {
  const [open, setOpen] = useState(false);

  return (
    <div className="absolute top-4 right-4 z-[1100] select-none">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="w-10 h-10 flex items-center justify-center bg-white rounded-lg shadow-lg border border-slate-200 hover:bg-slate-50 text-slate-600"
        title="Map layers"
      >
        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"
          />
        </svg>
      </button>

      {open && (
        <div className="mt-2 bg-white/95 backdrop-blur-sm rounded-lg shadow-xl border border-slate-200 w-56 overflow-hidden">
          <div className="px-3 py-2 border-b border-slate-100">
            <p className="text-[10px] font-bold text-slate-500 uppercase tracking-wider">
              Basemap
            </p>
            <div className="mt-1.5 space-y-1">
              {BASEMAP_KEYS.map((key) => (
                <label
                  key={key}
                  className="flex items-center gap-2 cursor-pointer text-sm text-slate-700 hover:text-slate-900"
                >
                  <input
                    type="radio"
                    name="basemap"
                    checked={basemap === key}
                    onChange={() => onBasemapChange(key)}
                    className="accent-primary-600"
                  />
                  {BASEMAPS[key].label}
                </label>
              ))}
            </div>
          </div>

          <div className="px-3 py-2">
            <p className="text-[10px] font-bold text-slate-500 uppercase tracking-wider">
              Overlays
            </p>
            <div className="mt-1.5 space-y-1">
              {OVERLAY_KEYS.map((key) => (
                <label
                  key={key}
                  className="flex items-center gap-2 cursor-pointer text-sm text-slate-700 hover:text-slate-900"
                >
                  <input
                    type="checkbox"
                    checked={visibleOverlays[key] ?? false}
                    onChange={() => onToggleOverlay(key)}
                    className="rounded border-slate-300 accent-primary-600"
                  />
                  {OVERLAYS[key].label}
                  {OVERLAYS[key].minZoom && (
                    <span className="text-[10px] text-slate-400 ml-auto">
                      z{OVERLAYS[key].minZoom}+
                    </span>
                  )}
                </label>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
