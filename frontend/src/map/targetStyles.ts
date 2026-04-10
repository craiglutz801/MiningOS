export interface TargetStyle {
  color: string;
  fillColor: string;
  radius: number;
  weight: number;
  fillOpacity: number;
}

const COLOR_MAP: Record<string, string> = {
  ownership: "#059669",
  due_diligence: "#7c3aed",
  negotiation: "#2563eb",
  monitoring_high: "#dc2626",
  high: "#dc2626",
  monitoring_med: "#d97706",
  medium: "#d97706",
  monitoring_low: "#64748b",
  low: "#64748b",
};

const RADIUS_MAP: Record<string, number> = {
  ownership: 14,
  due_diligence: 13,
  negotiation: 12,
  monitoring_high: 12,
  high: 12,
  monitoring_med: 10,
  medium: 10,
  monitoring_low: 8,
  low: 8,
};

export function getTargetStyle(priority: string | undefined): TargetStyle {
  const key = (priority || "monitoring_low").toLowerCase();
  return {
    fillColor: COLOR_MAP[key] || "#64748b",
    color: "#fff",
    radius: RADIUS_MAP[key] || 8,
    weight: 2,
    fillOpacity: 0.92,
  };
}

export const TARGET_STATUS_LABELS: Record<string, string> = {
  monitoring_low: "Monitoring - Low",
  monitoring_med: "Monitoring - Med",
  monitoring_high: "Monitoring - High",
  negotiation: "Negotiation",
  due_diligence: "Due Diligence",
  ownership: "Ownership",
  low: "Monitoring - Low",
  medium: "Monitoring - Med",
  high: "Monitoring - High",
};

export function getTargetStatusLabel(priority: string | undefined): string {
  const key = (priority || "monitoring_low").toLowerCase();
  return TARGET_STATUS_LABELS[key] || key;
}

export const LEGEND_ITEMS = [
  { label: "Ownership", color: "#059669" },
  { label: "Due Diligence", color: "#7c3aed" },
  { label: "Negotiation", color: "#2563eb" },
  { label: "High Priority", color: "#dc2626" },
  { label: "Med Priority", color: "#d97706" },
  { label: "Low Priority", color: "#64748b" },
] as const;
