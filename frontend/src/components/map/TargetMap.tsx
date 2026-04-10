import { useEffect, useRef, type MutableRefObject } from "react";
import {
  MapContainer,
  TileLayer,
  WMSTileLayer,
  CircleMarker,
  Tooltip,
  Popup,
  useMap,
} from "react-leaflet";
import MarkerClusterGroup from "react-leaflet-cluster";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import "react-leaflet-cluster/dist/assets/MarkerCluster.css";
import "react-leaflet-cluster/dist/assets/MarkerCluster.Default.css";

import { DEFAULT_CENTER, DEFAULT_ZOOM, MAX_FIT_ZOOM, DEFAULT_BOUNDS_PADDING } from "../../map/mapDefaults";
import { BASEMAPS } from "../../map/basemaps";
import { OVERLAYS } from "../../map/overlays";
import { ensureMapPanes, MAP_PANES } from "../../map/panes";
import { useMapPreferences } from "../../map/useMapPreferences";
import { useTargetsLayer, type MapTarget } from "../../map/useTargetsLayer";
import { getTargetStyle, getTargetStatusLabel } from "../../map/targetStyles";
import { FirstFitBounds } from "../../map/useFirstFitBounds";
import { TargetPopup } from "./TargetPopup";
import { MapLegend } from "./MapLegend";
import { MapLayerControl } from "./MapLayerControl";
import { LandOwnershipIdentify } from "./LandOwnershipIdentify";
import { MrdsMinesOverlay } from "./MrdsMinesOverlay";
import { UsStateBoundaries } from "./UsStateBoundaries";

interface TargetMapProps {
  selectedAreaId?: string | null;
}

function PaneInitializer() {
  const map = useMap();
  const initialized = useRef(false);
  useEffect(() => {
    if (!initialized.current) {
      ensureMapPanes(map);
      initialized.current = true;
    }
  }, [map]);
  return null;
}

/** Must be rendered inside MapContainer — assigns the Leaflet map for header toolbar buttons. */
function MapInstanceRef({ mapRef }: { mapRef: MutableRefObject<L.Map | null> }) {
  const map = useMap();
  useEffect(() => {
    mapRef.current = map;
    return () => {
      mapRef.current = null;
    };
  }, [map, mapRef]);
  return null;
}

function TargetMarker({ target }: { target: MapTarget }) {
  const style = getTargetStyle(target.priority);
  const statusLabel = getTargetStatusLabel(target.priority);
  const minerals = target.minerals.length > 0 ? target.minerals.join(", ") : "—";

  return (
    <CircleMarker
      center={[target.latitude, target.longitude]}
      pathOptions={{
        fillColor: style.fillColor,
        color: style.color,
        weight: style.weight,
        fillOpacity: style.fillOpacity,
      }}
      radius={style.radius}
      pane={MAP_PANES.targets}
    >
      <Tooltip direction="top" className="map-tooltip">
        <span>
          {target.name}
          <br />
          {minerals} &middot; {target.status}
          <br />
          <strong>{statusLabel}</strong>
        </span>
      </Tooltip>
      <Popup maxWidth={280} className="map-popup-container">
        <TargetPopup target={target} />
      </Popup>
    </CircleMarker>
  );
}

function SelectedTargetHighlight({ target }: { target: MapTarget }) {
  const style = getTargetStyle(target.priority);
  return (
    <>
      <CircleMarker
        center={[target.latitude, target.longitude]}
        pathOptions={{
          color: style.fillColor,
          fillColor: "transparent",
          weight: 3,
          opacity: 0.6,
          fillOpacity: 0,
          dashArray: "6 4",
        }}
        radius={style.radius + 10}
        pane={MAP_PANES.selectedTarget}
      />
      <CircleMarker
        center={[target.latitude, target.longitude]}
        pathOptions={{
          color: "#fff",
          fillColor: style.fillColor,
          weight: 3,
          fillOpacity: 1,
        }}
        radius={style.radius + 2}
        pane={MAP_PANES.selectedTarget}
      >
        <Tooltip direction="top" permanent className="map-tooltip">
          <strong>{target.name}</strong>
        </Tooltip>
        <Popup maxWidth={280} className="map-popup-container" autoClose={false}>
          <TargetPopup target={target} />
        </Popup>
      </CircleMarker>
    </>
  );
}

function FlyToSelected({ target, maxZoom }: { target: MapTarget | null; maxZoom: number }) {
  const map = useMap();
  const flownTo = useRef<number | null>(null);
  useEffect(() => {
    if (!target || flownTo.current === target.id) return;
    flownTo.current = target.id;
    const center: [number, number] = [target.latitude, target.longitude];
    // Defer past other map effects (e.g. any remaining fitBounds) so zoom isn't overwritten.
    const t = window.setTimeout(() => {
      map.flyTo(center, maxZoom, { duration: 1.0, animate: true });
    }, 0);
    return () => window.clearTimeout(t);
  }, [map, target, maxZoom]);
  return null;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function createClusterIcon(cluster: any) {
  const count = cluster.getChildCount();
  let size = "small";
  let dim = 36;
  if (count >= 50) { size = "large"; dim = 50; }
  else if (count >= 10) { size = "medium"; dim = 42; }

  return L.divIcon({
    html: `<div class="cluster-inner">${count}</div>`,
    className: `marker-cluster marker-cluster-${size}`,
    iconSize: L.point(dim, dim),
  });
}

function OverlayLayers({ visibleOverlays }: { visibleOverlays: Record<string, boolean> }) {
  return (
    <>
      {/* BLM Land — cached ArcGIS tile layer */}
      {visibleOverlays.ownership && OVERLAYS.ownership.url && (
        <TileLayer
          url={OVERLAYS.ownership.url}
          opacity={OVERLAYS.ownership.opacity ?? 0.45}
          pane={MAP_PANES.ownership}
          maxZoom={19}
        />
      )}

      {/* PLSS Grid — WMS from BLM Cadastral */}
      {visibleOverlays.plss && OVERLAYS.plss.url && (
        <WMSTileLayer
          url={OVERLAYS.plss.url}
          params={{
            layers: OVERLAYS.plss.layers || "",
            format: OVERLAYS.plss.format || "image/png",
            transparent: true,
            version: "1.1.1",
          }}
          opacity={OVERLAYS.plss.opacity ?? 0.55}
          pane={MAP_PANES.plss}
        />
      )}
    </>
  );
}

export function TargetMap({ selectedAreaId }: TargetMapProps) {
  const mapRef = useRef<L.Map | null>(null);
  const { basemap, setBasemap, visibleOverlays, toggleOverlay } = useMapPreferences();
  const { targets, isLoading, error, bounds, selectedTarget } = useTargetsLayer(selectedAreaId);

  const activeBm = BASEMAPS[basemap];

  if (isLoading) {
    return (
      <div className="min-h-[60vh] flex flex-col justify-center">
        <p className="text-slate-600">Loading map data…</p>
      </div>
    );
  }

  if (error) {
    return (
      <div>
        <div className="p-4 bg-red-50 border border-red-200 rounded-lg text-red-800">{error}</div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      <div className="flex flex-wrap items-center justify-between gap-4 mb-4">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">Map</h1>
          <p className="text-slate-500 text-sm mt-0.5">
            GIS exploration &middot; {targets.length} target{targets.length !== 1 ? "s" : ""} with coordinates
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => mapRef.current?.setView(DEFAULT_CENTER, DEFAULT_ZOOM)}
            className="px-3 py-2 rounded-lg border border-slate-200 bg-white text-slate-700 text-sm font-medium hover:bg-slate-50 shadow-sm"
          >
            Reset: Western US
          </button>
          {targets.length > 0 && (
            <button
              type="button"
              onClick={() => {
                const m = mapRef.current;
                if (!m) return;
                const b = L.latLngBounds(targets.map((t) => [t.latitude, t.longitude] as [number, number]));
                m.fitBounds(b, { padding: DEFAULT_BOUNDS_PADDING, maxZoom: MAX_FIT_ZOOM });
              }}
              className="px-3 py-2 rounded-lg border border-slate-200 bg-white text-slate-700 text-sm font-medium hover:bg-slate-50 shadow-sm"
            >
              Zoom to Targets
            </button>
          )}
        </div>
      </div>

      <div className="rounded-xl border border-slate-200 overflow-hidden shadow-card bg-white flex-1 min-h-[70vh] relative">
        <MapContainer
          center={DEFAULT_CENTER}
          zoom={DEFAULT_ZOOM}
          className="absolute inset-0 w-full h-full"
          zoomControl={true}
          attributionControl={true}
        >
          <MapInstanceRef mapRef={mapRef} />
          <PaneInitializer />

          <TileLayer
            key={activeBm.key}
            url={activeBm.url}
            attribution={activeBm.attribution}
            maxZoom={activeBm.maxZoom}
            detectRetina={activeBm.detectRetina}
          />

          {/* US state borders: high-contrast GeoJSON on satellite (label tiles are too faint for boundaries). */}
          {basemap === "satellite" && <UsStateBoundaries />}

          {/* City / road / place names on satellite */}
          {activeBm.labelsUrl && (
            <TileLayer
              key={activeBm.key + "-labels"}
              url={activeBm.labelsUrl}
              pane={MAP_PANES.labels}
              maxZoom={activeBm.maxZoom}
              zIndex={700}
              opacity={0.95}
            />
          )}

          <OverlayLayers visibleOverlays={visibleOverlays} />

          <MrdsMinesOverlay visible={visibleOverlays.knownMines ?? false} />

          <LandOwnershipIdentify enabled={visibleOverlays.ownership} />

          {visibleOverlays.targets && (
            <MarkerClusterGroup
              chunkedLoading
              maxClusterRadius={50}
              iconCreateFunction={createClusterIcon}
              showCoverageOnHover={false}
              spiderfyOnMaxZoom
              disableClusteringAtZoom={14}
            >
              {targets.map((t) => (
                <TargetMarker key={t.id} target={t} />
              ))}
            </MarkerClusterGroup>
          )}

          {selectedTarget && <SelectedTargetHighlight target={selectedTarget} />}
          <FlyToSelected target={selectedTarget} maxZoom={activeBm.maxZoom ?? 19} />

          {/* When opening /map?areaId=…, do not fit all targets (max z12) — that erased fly-to. */}
          <FirstFitBounds bounds={selectedAreaId ? null : bounds} />
        </MapContainer>

        <MapLayerControl
          basemap={basemap}
          onBasemapChange={setBasemap}
          visibleOverlays={visibleOverlays}
          onToggleOverlay={toggleOverlay}
        />

        <MapLegend />
      </div>

      {targets.length === 0 && (
        <p className="mt-3 text-slate-500 text-sm">
          No targets have coordinates yet. Add lat/lon to targets or ingest data with coordinates to see pins.
        </p>
      )}
    </div>
  );
}
