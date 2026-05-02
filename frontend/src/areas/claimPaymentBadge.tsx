/**
 * Visual badge for a BLM mining claim's maintenance-fee payment status.
 * Used by both the MLRS Scrape and LR2000 claim records tables on the Targets page.
 */

type ClaimPaymentBadgeProps = {
  status: unknown;
  message?: unknown;
};

export function getClaimPaymentText(c: Record<string, unknown>): {
  status: "paid" | "unpaid" | "unknown";
  message: string | null;
} {
  const raw = (c.payment_status ?? "").toString().trim().toLowerCase();
  let status: "paid" | "unpaid" | "unknown";
  if (raw === "paid") status = "paid";
  else if (raw === "unpaid") status = "unpaid";
  else status = "unknown";

  const messageRaw = c.payment_message;
  const message =
    typeof messageRaw === "string" && messageRaw.trim() ? messageRaw.trim() : null;

  return { status, message };
}

export function ClaimPaymentBadge({ status, message }: ClaimPaymentBadgeProps) {
  const value = (status ?? "").toString().trim().toLowerCase();

  let label: string;
  let cls: string;
  if (value === "paid") {
    label = "Paid";
    cls = "bg-emerald-100 text-emerald-800 border border-emerald-200";
  } else if (value === "unpaid") {
    label = "Unpaid";
    cls = "bg-red-100 text-red-800 border border-red-200";
  } else {
    label = "Unknown";
    cls = "bg-slate-100 text-slate-700 border border-slate-200";
  }

  const tip = typeof message === "string" && message.trim() ? message.trim() : label;

  return (
    <span
      title={tip}
      className={`inline-flex items-center gap-1 rounded px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${cls}`}
    >
      {label}
    </span>
  );
}
