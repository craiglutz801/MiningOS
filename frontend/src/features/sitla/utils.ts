export function formatMoney(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n)) return "—";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  }).format(n);
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

export function daysUntil(value: string | null | undefined): string {
  if (!value) return "";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "";
  const diff = Math.ceil((d.getTime() - Date.now()) / (1000 * 60 * 60 * 24));
  if (diff < 0) return "past";
  if (diff === 0) return "today";
  if (diff === 1) return "1 day";
  return `${diff} days`;
}

export function tierBadgeClass(tier: string): string {
  switch ((tier || "").toUpperCase()) {
    case "A":
      return "bg-rose-600 text-white";
    case "B":
      return "bg-orange-500 text-white";
    case "C":
      return "bg-amber-500 text-white";
    case "D":
      return "bg-slate-500 text-white";
    default:
      return "bg-slate-300 text-slate-800";
  }
}

export function statusLabel(status: string): string {
  return (status || "UNKNOWN").replace(/_/g, " ");
}

export function healthClass(health: string): string {
  switch ((health || "").toUpperCase()) {
    case "HEALTHY":
      return "text-emerald-700 bg-emerald-50";
    case "DEGRADED":
      return "text-amber-800 bg-amber-50";
    case "STALE":
      return "text-orange-800 bg-orange-50";
    case "FAILED":
      return "text-rose-800 bg-rose-50";
    case "MANUAL":
      return "text-sky-800 bg-sky-50";
    case "DISABLED":
      return "text-slate-500 bg-slate-100";
    case "UNCONFIGURED":
      return "text-slate-500 bg-slate-100";
    default:
      return "text-slate-600 bg-slate-50";
  }
}
