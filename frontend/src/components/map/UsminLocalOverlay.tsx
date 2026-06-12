import { useCallback, useEffect, useRef, useState } from "react";
import { Marker, Popup, Tooltip, useMap, useMapEvents } from "react-leaflet";
import MarkerClusterGroup from "react-leaflet-cluster";
import L from "leaflet";
import { OVERLAYS } from "../../map/overlays";
import { MAP_PANES } from "../../map/panes";
import { fetchUsminInBounds, usminDetailUrl, type UsminPoint } from "../../map/usminQuery";
import { getUsminPickIcon } from "../../map/usminPickIcon";

const MIN_ZOOM = OVERLAYS.usminLocal.minZoom ?? 9;
const DEBOUNCE_MS = 500;
const FETCH_LIMIT = 4000;

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function createUsminClusterIcon(cluster: any) {
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
    html: `<div class="usmin-cluster-inner">${count}</div>`,
    className: `usmin-marker-cluster usmin-marker-cluster-${size}`,
    iconSize: L.point(dim, dim),
  });
}

function UsminMarker({ point }: { point: UsminPoint }) {
  const name = point.name.trim();
  const type = point.type.trim();
  const heading = name || type || "USMIN feature";

  return (
    <Marker position={[point.lat, point.lon]} icon={getUsminPickIcon()} pane={MAP_PANES.usminLocal}>
      <Tooltip direction="top" offset={[0, -22]} opacity={1} className="usmin-tooltip">
        <div className="text-xs max-w-[220px]">
          <div className="font-semibold text-slate-900 leading-tight">{heading}</div>
          {name && type && <div className="text-slate-600 mt-0.5">{type}</div>}
          {!name && type && <div className="text-slate-500 mt-0.5 text-[11px]">Unnamed feature</div>}
        </div>
      </Tooltip>
      <Popup className="map-popup-container" maxWidth={260}>
        <div className="text-sm" style={{ fontFamily: "system-ui, sans-serif" }}>
          <div className="font-semibold text-slate-900">{heading}</div>
          {type && <div className="text-slate-600 text-xs mt-1">Feature type: {type}</div>}
          <div className="text-slate-400 text-[11px] mt-1">USGS USMIN (topo-mapped)</div>
          {point.usminId ? (
            <a
              href={usminDetailUrl(point.usminId)}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-block mt-2 text-blue-600 text-xs font-medium"
            >
              Open USMIN record →
            </a>
          ) : null}
        </div>
      </Popup>
    </Marker>
  );
}

interface UsminLocalOverlayProps {
  visible: boolean;
}

export function UsminLocalOverlay({ visible }: UsminLocalOverlayProps) {
  const map = useMap();
  const [points, setPoints] = useState<UsminPoint[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [capped, setCapped] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const runFetch = useCallback(() => {
    if (!visible || map.getZoom() < MIN_ZOOM) {
      setPoints([]);
      setLoadError(null);
      setCapped(false);
      return;
    }

    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    setLoadError(null);

    const b = map.getBounds();
    fetchUsminInBounds(b.getWest(), b.getSouth(), b.getEast(), b.getNorth(), FETCH_LIMIT, ac.signal)
      .then((res) => {
        if (ac.signal.aborted) return;
        setPoints(res.points);
        setCapped(res.capped);
      })
      .catch((err) => {
        if (ac.signal.aborted || (err instanceof DOMException && err.name === "AbortError")) return;
        setPoints([]);
        setCapped(false);
        setLoadError(err instanceof Error ? err.message : "Could not load USMIN data.");
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
  const showCapBanner = z >= MIN_ZOOM && !loadError && capped && points.length > 0;

  return (
    <>
      {showZoomBanner && (
        <div className="absolute bottom-14 left-1/2 -translate-x-1/2 z-[1000] pointer-events-none px-3 py-2 rounded-lg bg-slate-800/90 text-white text-xs max-w-sm text-center shadow-lg">
          Zoom to <strong>{MIN_ZOOM}+</strong> to load USMIN mine features (amber axes) for the visible area.
        </div>
      )}
      {showErrorBanner && (
        <div className="absolute bottom-14 left-1/2 -translate-x-1/2 z-[1000] pointer-events-none px-3 py-2 rounded-lg bg-red-50 border border-red-200 text-red-800 text-xs max-w-md text-center shadow-sm">
          {loadError}
        </div>
      )}
      {showCapBanner && (
        <div className="absolute bottom-14 left-1/2 -translate-x-1/2 z-[1000] pointer-events-none px-2 py-1 rounded-md bg-amber-50 border border-amber-200 text-amber-900 text-[10px] max-w-sm text-center shadow-sm">
          Showing the first <strong>{points.length}</strong> features in this view — zoom in to see the rest.
        </div>
      )}

      {z >= MIN_ZOOM && (
        <MarkerClusterGroup
          chunkedLoading
          maxClusterRadius={55}
          iconCreateFunction={createUsminClusterIcon}
          showCoverageOnHover={false}
          spiderfyOnMaxZoom
          disableClusteringAtZoom={15}
          pane={MAP_PANES.usminLocal}
        >
          {points.map((p, i) => {
            const key = p.usminId ? `usmin-${p.usminId}` : `usmin-${p.lon},${p.lat},${i}`;
            return <UsminMarker key={key} point={p} />;
          })}
        </MarkerClusterGroup>
      )}
    </>
  );
}
