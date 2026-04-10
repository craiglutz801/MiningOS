import { useCallback, useEffect, useRef, useState } from "react";
import { Marker, Popup, Tooltip, useMap, useMapEvents } from "react-leaflet";
import MarkerClusterGroup from "react-leaflet-cluster";
import L from "leaflet";
import { OVERLAYS } from "../../map/overlays";
import { MAP_PANES } from "../../map/panes";
import { fetchMrdsInBounds, type MrdsFeature } from "../../map/mrdsQuery";
import { getMinePickIcon } from "../../map/minePickIcon";

const MIN_ZOOM = OVERLAYS.knownMines.minZoom ?? 8;
const DEBOUNCE_MS = 500;

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function createMineClusterIcon(cluster: any) {
  const count = cluster.getChildCount();
  let size = "small";
  let dim = 36;
  if (count >= 80) {
    size = "large";
    dim = 50;
  } else if (count >= 20) {
    size = "medium";
    dim = 42;
  }
  return L.divIcon({
    html: `<div class="mine-cluster-inner">${count}</div>`,
    className: `mine-marker-cluster mine-marker-cluster-${size}`,
    iconSize: L.point(dim, dim),
  });
}

function MrdsMineMarker({ feature }: { feature: MrdsFeature }) {
  const coords = feature.geometry?.coordinates;
  if (!coords || coords.length < 2) return null;
  const [lng, lat] = coords;
  const p = feature.properties || {};
  const name = (p.SITE_NAME || "Unnamed site").trim();
  const dev = (p.DEV_STAT || "").trim();
  const codes = (p.CODE_LIST || "").trim();
  const grade = (p.Grade || "").trim();
  const url = (p.URL || "").trim();
  const depId = (p.DEP_ID || "").trim();

  return (
    <Marker position={[lat, lng]} icon={getMinePickIcon()} pane={MAP_PANES.mines}>
      <Tooltip direction="top" offset={[0, -24]} opacity={1} className="mrds-mine-tooltip">
        <div className="text-xs max-w-[220px]">
          <div className="font-semibold text-slate-900 leading-tight">{name}</div>
          {dev && <div className="text-slate-600 mt-0.5">{dev}</div>}
          {codes && (
            <div className="text-slate-500 mt-1 text-[11px] leading-snug">
              Commodities: {codes.replace(/\s+/g, " ").trim()}
            </div>
          )}
          {grade && <div className="text-slate-500 mt-0.5 text-[11px]">Data grade: {grade}</div>}
          {depId && <div className="text-slate-400 mt-0.5 text-[10px] font-mono">ID {depId}</div>}
        </div>
      </Tooltip>
      <Popup className="map-popup-container" maxWidth={280}>
        <div className="text-sm" style={{ fontFamily: "system-ui, sans-serif" }}>
          <div className="font-semibold text-slate-900">{name}</div>
          {dev && <div className="text-slate-600 text-xs mt-1">{dev}</div>}
          {codes && <div className="text-slate-500 text-xs mt-1">Commodities: {codes.trim()}</div>}
          {grade && <div className="text-slate-500 text-xs">Grade: {grade}</div>}
          {url ? (
            <a
              href={url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-block mt-2 text-blue-600 text-xs font-medium"
            >
              Open MRDS record →
            </a>
          ) : null}
        </div>
      </Popup>
    </Marker>
  );
}

interface MrdsMinesOverlayProps {
  visible: boolean;
}

export function MrdsMinesOverlay({ visible }: MrdsMinesOverlayProps) {
  const map = useMap();
  const [features, setFeatures] = useState<MrdsFeature[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [capped, setCapped] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const runFetch = useCallback(() => {
    if (!visible) {
      setFeatures([]);
      setLoadError(null);
      setCapped(false);
      return;
    }
    if (map.getZoom() < MIN_ZOOM) {
      setFeatures([]);
      setLoadError(null);
      setCapped(false);
      return;
    }

    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    setLoadError(null);

    const b = map.getBounds();
    fetchMrdsInBounds(b.getWest(), b.getSouth(), b.getEast(), b.getNorth(), ac.signal)
      .then((fc) => {
        if (ac.signal.aborted) return;
        const pts = fc.features.filter(
          (f): f is MrdsFeature =>
            f.geometry?.type === "Point" && Array.isArray((f.geometry as GeoJSON.Point).coordinates),
        );
        setFeatures(pts);
        const root = fc as { properties?: { exceededTransferLimit?: boolean } };
        setCapped(!!root.properties?.exceededTransferLimit);
      })
      .catch((err) => {
        if (ac.signal.aborted || (err instanceof DOMException && err.name === "AbortError")) return;
        setFeatures([]);
        setCapped(false);
        setLoadError(err instanceof Error ? err.message : "Could not load MRDS data.");
      });
  }, [visible, map]);

  useEffect(() => {
    runFetch();
  }, [runFetch]);

  useMapEvents({
    moveend: () => {
      if (!visible) return;
      if (debounceRef.current) clearTimeout(debounceRef.current);
      debounceRef.current = setTimeout(runFetch, DEBOUNCE_MS);
    },
    zoomend: () => {
      if (!visible) return;
      if (debounceRef.current) clearTimeout(debounceRef.current);
      debounceRef.current = setTimeout(runFetch, DEBOUNCE_MS);
    },
  });

  useEffect(() => {
    return () => {
      abortRef.current?.abort();
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, []);

  if (!visible) return null;

  const z = map.getZoom();
  const showZoomBanner = z < MIN_ZOOM;
  const showErrorBanner = z >= MIN_ZOOM && loadError;
  const showCapBanner = z >= MIN_ZOOM && !loadError && capped && features.length > 0;

  return (
    <>
      {showZoomBanner && (
        <div className="absolute bottom-14 left-1/2 -translate-x-1/2 z-[1000] pointer-events-none px-3 py-2 rounded-lg bg-slate-800/90 text-white text-xs max-w-sm text-center shadow-lg">
          Zoom to <strong>{MIN_ZOOM}+</strong> to load USGS MRDS mine picks (blue axes) for the visible area.
        </div>
      )}
      {showErrorBanner && (
        <div className="absolute bottom-14 left-1/2 -translate-x-1/2 z-[1000] pointer-events-none px-3 py-2 rounded-lg bg-red-50 border border-red-200 text-red-800 text-xs max-w-md text-center shadow-sm">
          {loadError}
        </div>
      )}
      {showCapBanner && (
        <div className="absolute bottom-14 left-1/2 -translate-x-1/2 z-[1000] pointer-events-none px-2 py-1 rounded-md bg-blue-50 border border-blue-200 text-blue-900 text-[10px] max-w-sm text-center shadow-sm">
          Showing up to <strong>2000</strong> deposits in this view — zoom in to narrow the list.
        </div>
      )}

      {z >= MIN_ZOOM && (
        <MarkerClusterGroup
          chunkedLoading
          maxClusterRadius={55}
          iconCreateFunction={createMineClusterIcon}
          showCoverageOnHover={false}
          spiderfyOnMaxZoom
          disableClusteringAtZoom={14}
          pane={MAP_PANES.mines}
        >
          {features.map((f, i) => {
            const dep = f.properties?.DEP_ID;
            const c = f.geometry as GeoJSON.Point;
            const key = dep ? `mrds-${dep}` : `mrds-${c.coordinates[0]},${c.coordinates[1]},${i}`;
            return <MrdsMineMarker key={key} feature={f} />;
          })}
        </MarkerClusterGroup>
      )}
    </>
  );
}
