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
};

export const OVERLAY_KEYS = Object.keys(OVERLAYS) as OverlayKey[];
