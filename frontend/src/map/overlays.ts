import type { OverlayKey, OverlayDef } from "./layerTypes";
import { MAP_PANES } from "./panes";

export const OVERLAYS: Record<OverlayKey, OverlayDef> = {
  targets: {
    key: "targets",
    label: "My Targets",
    kind: "api-points",
    visibleByDefault: true,
    pane: MAP_PANES.targets,
  },
  plss: {
    key: "plss",
    label: "PLSS Grid",
    kind: "wms",
    visibleByDefault: false,
    pane: MAP_PANES.plss,
    url: "https://gis.blm.gov/arcgis/services/Cadastral/BLM_Natl_PLSS_CadNSDI/MapServer/WMSServer",
    layers: "1,2",
    format: "image/png",
    transparent: true,
    opacity: 0.55,
    minZoom: 9,
  },
  ownership: {
    key: "ownership",
    label: "Land ownership (SMA)",
    kind: "tile",
    visibleByDefault: false,
    pane: MAP_PANES.ownership,
    url: "https://gis.blm.gov/arcgis/rest/services/lands/BLM_Natl_SMA_Cached_with_PriUnk/MapServer/tile/{z}/{y}/{x}",
    opacity: 0.45,
    minZoom: 5,
  },
  knownMines: {
    key: "knownMines",
    label: "Known Mines (MRDS)",
    kind: "mrds-points",
    visibleByDefault: false,
    pane: MAP_PANES.mines,
    minZoom: 8,
  },
  // USGS USMIN — Prospect- and Mine-Related Features digitized from historical
  // USGS topo maps (~725k point/polygon symbols nationwide). Far more complete
  // than MRDS for physical workings. Rendered as WMS imagery so the FULL
  // inventory is shown (no client-side 2000-feature cap like MRDS). Layers are
  // listed bottom-to-top: polygons first so the point symbols draw on top.
  usminMines: {
    key: "usminMines",
    label: "USMIN Mine Features (USGS)",
    kind: "wms",
    visibleByDefault: false,
    pane: MAP_PANES.usminMines,
    url: "https://mrdata.usgs.gov/services/usmin",
    layers: "polygons,points",
    format: "image/png",
    transparent: true,
    opacity: 0.85,
    minZoom: 7,
  },
  // USGS USMIN Mineral Deposit Database — curated, authoritative critical-mineral
  // deposits. Sparse nationwide, so safe at low zoom. `sites` = one generalized
  // point per deposit (best zoomed out), `points` = detailed features.
  usminDeposits: {
    key: "usminDeposits",
    label: "USMIN Deposits (USGS)",
    kind: "wms",
    visibleByDefault: false,
    pane: MAP_PANES.usminDeposits,
    url: "https://mrdata.usgs.gov/services/deposit",
    layers: "polygons,points,sites",
    format: "image/png",
    transparent: true,
    opacity: 0.9,
    minZoom: 4,
  },
};

export const OVERLAY_KEYS = Object.keys(OVERLAYS) as OverlayKey[];
