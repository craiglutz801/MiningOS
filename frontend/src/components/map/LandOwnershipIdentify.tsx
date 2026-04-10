import { useRef, useCallback, useEffect } from "react";
import { useMap, useMapEvents } from "react-leaflet";
import L from "leaflet";
import { querySurfaceManagementAtPoint } from "../../map/smaQuery";
import { formatHoldAgency, formatManagingAgency } from "../../map/agencyLabels";

/** Minimum zoom to run identify (keeps requests reasonable; SMA is detailed at closer scales). */
const MIN_IDENTIFY_ZOOM = 10;

function escapeHtml(s: string): string {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

function buildPopupHtml(attrs: Record<string, unknown>): string {
  const a = attrs as import("../../map/agencyLabels").SmaAttributes;
  const headline = formatManagingAgency(a);
  const hold = formatHoldAgency(a);
  const rows: [string, string][] = [];

  if (a.ADMIN_UNIT_NAME) rows.push(["Unit / area", String(a.ADMIN_UNIT_NAME)]);
  if (a.ADMIN_UNIT_TYPE) rows.push(["Unit type", String(a.ADMIN_UNIT_TYPE)]);
  if (a.ADMIN_ST) rows.push(["State", String(a.ADMIN_ST)]);
  if (hold) rows.push(["Holding / interest", hold]);
  if (a.SMA_ID != null) rows.push(["SMA record ID", String(a.SMA_ID)]);

  const rowHtml = rows
    .map(
      ([k, v]) =>
        `<tr><td style="padding:2px 8px 2px 0;color:#64748b;vertical-align:top;white-space:nowrap">${escapeHtml(k)}</td><td style="padding:2px 0;font-weight:500;color:#0f172a">${escapeHtml(v)}</td></tr>`,
    )
    .join("");

  return `
    <div style="font-family:system-ui,sans-serif;font-size:13px;max-width:280px">
      <div style="font-weight:700;color:#0f172a;margin-bottom:6px;line-height:1.3">${escapeHtml(headline)}</div>
      <table style="border-collapse:collapse;width:100%;font-size:12px">${rowHtml}</table>
      <p style="margin:8px 0 0;font-size:10px;color:#94a3b8;line-height:1.35">
        Source: BLM National Surface Management Agency (SMA). Shows <strong>federal / state / local surface manager</strong>,
        not private deed holder names. For parcel owner names, use county assessor records.
      </p>
    </div>
  `;
}

interface LandOwnershipIdentifyProps {
  enabled: boolean;
}

export function LandOwnershipIdentify({ enabled }: LandOwnershipIdentifyProps) {
  const map = useMap();
  const activePopup = useRef<L.Popup | null>(null);
  const requestGen = useRef(0);

  const closeActive = useCallback(() => {
    if (activePopup.current) {
      map.removeLayer(activePopup.current);
      activePopup.current = null;
    }
  }, [map]);

  useEffect(() => {
    if (!enabled) closeActive();
  }, [enabled, closeActive]);

  useMapEvents({
    click: async (e) => {
      if (!enabled) return;

      const myGen = ++requestGen.current;
      closeActive();

      if (map.getZoom() < MIN_IDENTIFY_ZOOM) {
        const p = L.popup({ className: "sma-identify-popup", maxWidth: 300 })
          .setLatLng(e.latlng)
          .setContent(
            `<div style="font-size:13px;padding:4px">Zoom in closer (level <strong>${MIN_IDENTIFY_ZOOM}+</strong>) to look up surface land manager.</div>`,
          );
        p.openOn(map);
        activePopup.current = p;
        return;
      }

      const loading = L.popup({ className: "sma-identify-popup", maxWidth: 320 })
        .setLatLng(e.latlng)
        .setContent(`<div style="font-size:13px;padding:8px;color:#64748b">Loading land records…</div>`);
      loading.openOn(map);
      activePopup.current = loading;

      const { lat, lng } = e.latlng;
      const result = await querySurfaceManagementAtPoint(lat, lng);

      if (myGen !== requestGen.current) return;
      if (!activePopup.current || !map.hasLayer(activePopup.current)) return;

      if (result.ok) {
        activePopup.current.setContent(buildPopupHtml(result.attributes as unknown as Record<string, unknown>));
      } else {
        activePopup.current.setContent(
          `<div style="font-size:13px;padding:4px;color:#b91c1c">${escapeHtml(result.message)}</div>`,
        );
      }
    },
  });

  return null;
}
