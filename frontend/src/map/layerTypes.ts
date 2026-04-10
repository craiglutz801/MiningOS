export type BasemapKey = "satellite" | "topo" | "streets";

export type OverlayKey = "targets" | "plss" | "ownership" | "knownMines";

export type LayerKind = "tile" | "wms" | "geojson" | "api-points" | "mrds-points";

export interface BasemapDef {
  key: BasemapKey;
  label: string;
  kind: "tile";
  url: string;
  attribution: string;
  maxZoom?: number;
  subdomains?: string[];
  detectRetina?: boolean;
  labelsUrl?: string;
}

export interface OverlayDef {
  key: OverlayKey;
  label: string;
  kind: LayerKind;
  visibleByDefault: boolean;
  pane: string;
  minZoom?: number;
  maxZoom?: number;
  opacity?: number;
  url?: string;
  layers?: string;
  format?: string;
  transparent?: boolean;
  tiled?: boolean;
}
