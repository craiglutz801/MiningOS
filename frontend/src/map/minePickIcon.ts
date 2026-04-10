import L from "leaflet";

/** Blue pickaxe marker for MRDS sites (SVG in divIcon). */
const PICK_SVG = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
  <path fill="#1d4ed8" stroke="#ffffff" stroke-width="1.1" stroke-linejoin="round"
    d="M14.2 3.5l1.4 1.4-3.2 3.2 1.1 1.1 3.2-3.2 1.4 1.4-3.9 3.9c.8.9 1 2.1.6 3.2l-.3.9-2.1-2.1-5.7 5.7-1.8-1.8 5.7-5.7-2.1-2.1.9-.3c1.1-.4 2.3-.2 3.2.6l3.9-3.9z"/>
</svg>`;

let _cached: L.DivIcon | null = null;

export function getMinePickIcon(): L.DivIcon {
  if (_cached) return _cached;
  _cached = L.divIcon({
    className: "mrds-mine-pick-icon",
    html: PICK_SVG,
    iconSize: [26, 28],
    iconAnchor: [13, 28],
    popupAnchor: [0, -26],
  });
  return _cached;
}
