import L from "leaflet";

/**
 * Amber pickaxe marker for local USMIN topo mine features — intentionally a
 * different color from the blue MRDS pick so the two mine layers are
 * distinguishable when both are on.
 */
const PICK_SVG = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
  <path fill="#b45309" stroke="#ffffff" stroke-width="1.1" stroke-linejoin="round"
    d="M14.2 3.5l1.4 1.4-3.2 3.2 1.1 1.1 3.2-3.2 1.4 1.4-3.9 3.9c.8.9 1 2.1.6 3.2l-.3.9-2.1-2.1-5.7 5.7-1.8-1.8 5.7-5.7-2.1-2.1.9-.3c1.1-.4 2.3-.2 3.2.6l3.9-3.9z"/>
</svg>`;

let _cached: L.DivIcon | null = null;

export function getUsminPickIcon(): L.DivIcon {
  if (_cached) return _cached;
  _cached = L.divIcon({
    className: "usmin-pick-icon",
    html: PICK_SVG,
    iconSize: [24, 26],
    iconAnchor: [12, 26],
    popupAnchor: [0, -24],
  });
  return _cached;
}
