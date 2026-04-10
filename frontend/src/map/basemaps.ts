import type { BasemapKey, BasemapDef } from "./layerTypes";

export const BASEMAPS: Record<BasemapKey, BasemapDef> = {
  satellite: {
    key: "satellite",
    label: "Satellite",
    kind: "tile",
    url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    attribution:
      "Tiles &copy; Esri &mdash; Source: Esri, Maxar, Earthstar Geographics, USDA FSA, USGS, AeroGRID, IGN, the GIS User Community",
    maxZoom: 19,
    labelsUrl:
      "https://{s}.basemaps.cartocdn.com/rastertiles/voyager_only_labels/{z}/{x}/{y}{r}.png",
  },
  topo: {
    key: "topo",
    label: "Topo",
    kind: "tile",
    url: "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
    attribution:
      'Map data &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors, SRTM | Map style &copy; <a href="https://opentopomap.org">OpenTopoMap</a> (<a href="https://creativecommons.org/licenses/by-sa/3.0/">CC-BY-SA</a>)',
    maxZoom: 17,
  },
  streets: {
    key: "streets",
    label: "Streets",
    kind: "tile",
    url: "https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png",
    attribution:
      '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/">CARTO</a>',
    maxZoom: 20,
    detectRetina: true,
  },
};

export const BASEMAP_KEYS = Object.keys(BASEMAPS) as BasemapKey[];
