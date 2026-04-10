import { useState } from "react";
import { LEGEND_ITEMS } from "../../map/targetStyles";

export function MapLegend() {
  /** Collapsed by default so the top-right Layers control stays visible. */
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="absolute bottom-4 left-4 z-[1000] bg-white/95 backdrop-blur-sm rounded-lg shadow-lg border border-slate-200 text-xs select-none max-w-[200px]">
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className={`w-full flex items-center justify-between px-3 py-2 font-semibold text-slate-700 hover:bg-slate-50 ${
          expanded ? "rounded-t-lg" : "rounded-lg"
        }`}
        aria-expanded={expanded}
        title={expanded ? "Hide legend" : "Show legend"}
      >
        <span>Legend</span>
        <span className="text-slate-400 text-sm" aria-hidden>
          {expanded ? "▾" : "▸"}
        </span>
      </button>
      {expanded && (
        <div className="px-3 pb-2.5 space-y-1.5 border-t border-slate-100">
          <p className="text-[10px] font-semibold text-slate-500 uppercase tracking-wide pt-2">
            Target Status
          </p>
          {LEGEND_ITEMS.map((item) => (
            <div key={item.label} className="flex items-center gap-2">
              <span
                className="inline-block w-3 h-3 rounded-full border border-white shadow-sm shrink-0"
                style={{ backgroundColor: item.color }}
              />
              <span className="text-slate-600">{item.label}</span>
            </div>
          ))}
          <p className="text-[10px] text-slate-500 pt-2 mt-2 border-t border-slate-100 leading-snug">
            <span className="font-semibold text-slate-600">Land ownership (SMA):</span> turn the layer on, zoom to{" "}
            <span className="font-mono">10+</span>, then <strong>click the map</strong> for surface manager (BLM /
            USFS / private / state — not deed names).
          </p>
        </div>
      )}
    </div>
  );
}
