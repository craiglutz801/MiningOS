import type L from "leaflet";

export const MAP_PANES = {
  ownership: "ownership-pane",
  stateOutlines: "state-outlines-pane",
  plss: "plss-pane",
  mines: "mines-pane",
  targets: "targets-pane",
  selectedTarget: "selected-target-pane",
  labels: "labels-pane",
} as const;

const PANE_Z: Record<string, number> = {
  [MAP_PANES.ownership]: 300,
  [MAP_PANES.stateOutlines]: 335,
  [MAP_PANES.plss]: 350,
  [MAP_PANES.mines]: 400,
  [MAP_PANES.targets]: 500,
  [MAP_PANES.selectedTarget]: 650,
  [MAP_PANES.labels]: 700,
};

export function ensureMapPanes(map: L.Map) {
  for (const [paneName, zIndex] of Object.entries(PANE_Z)) {
    if (!map.getPane(paneName)) {
      const pane = map.createPane(paneName);
      pane.style.zIndex = String(zIndex);
      if (
        paneName === MAP_PANES.ownership ||
        paneName === MAP_PANES.stateOutlines ||
        paneName === MAP_PANES.plss
      ) {
        pane.style.pointerEvents = "none";
      }
    }
  }
}
