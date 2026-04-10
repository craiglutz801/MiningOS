/** Human-readable labels for BLM Surface Management Agency (SMA) codes. */

const AGENCY_BY_CODE: Record<string, string> = {
  BLM: "Bureau of Land Management (BLM)",
  USFS: "U.S. Forest Service (USFS)",
  NPS: "National Park Service (NPS)",
  USFW: "U.S. Fish & Wildlife Service (FWS)",
  USFWS: "U.S. Fish & Wildlife Service (FWS)",
  BOR: "Bureau of Reclamation",
  USBR: "Bureau of Reclamation",
  BIA: "Bureau of Indian Affairs (BIA)",
  DOD: "Department of Defense (DOD)",
  PVT: "Private or other (not federal surface)",
  UNK: "Unknown / unsurveyed classification",
  STA: "State-managed surface",
  LOC: "Local (county/municipal) surface",
};

const DEPT_BY_CODE: Record<string, string> = {
  DOI: "Department of the Interior",
  USDA: "U.S. Department of Agriculture",
  DOD: "Department of Defense",
  DOE: "Department of Energy",
  PVT: "Non-federal (private / other)",
  STA: "State",
  LOC: "Local government",
};

export interface SmaAttributes {
  OBJECTID?: number;
  SMA_ID?: number;
  HOLD_ID?: number | null;
  ADMIN_ST?: string | null;
  FAU_ID?: number | null;
  ADMIN_UNIT_NAME?: string | null;
  ADMIN_UNIT_TYPE?: string | null;
  ADMIN_DEPT_CODE?: string | null;
  ADMIN_AGENCY_CODE?: string | null;
  HOLD_DEPT_CODE?: string | null;
  HOLD_AGENCY_CODE?: string | null;
}

export function formatManagingAgency(a: SmaAttributes): string {
  const dept = a.ADMIN_DEPT_CODE?.trim();
  const ag = a.ADMIN_AGENCY_CODE?.trim()?.toUpperCase();

  if (dept === "PVT" && ag === "PVT") {
    return "Private or other non-federal land (per BLM SMA)";
  }

  const deptLabel = (dept && DEPT_BY_CODE[dept]) || dept;
  const agLabel = (ag && AGENCY_BY_CODE[ag]) || (ag && AGENCY_BY_CODE[ag.replace(/\s/g, "")]) || ag;

  if (deptLabel && agLabel) return `${agLabel} (${deptLabel})`;
  if (agLabel) return agLabel;
  if (deptLabel) return deptLabel;
  if (a.ADMIN_UNIT_NAME) return a.ADMIN_UNIT_NAME;
  return "Surface manager (see details below)";
}

export function formatHoldAgency(a: SmaAttributes): string | null {
  const hd = a.HOLD_DEPT_CODE?.trim();
  const ha = a.HOLD_AGENCY_CODE?.trim();
  if (!hd && !ha) return null;
  const parts = [hd && (DEPT_BY_CODE[hd] || hd), ha && (AGENCY_BY_CODE[ha.toUpperCase()] || ha)].filter(Boolean);
  return parts.length ? parts.join(" · ") : null;
}
