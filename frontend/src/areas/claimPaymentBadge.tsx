/**
 * Visual badge for a BLM mining claim's maintenance-fee payment status.
 * Used by both the MLRS Scrape and LR2000 claim records tables on the Targets page.
 */

export type ClaimUnknownKind = "not_scraped" | "timed_out" | "unknown";

type ClaimPaymentBadgeProps = {
  status: unknown;
  message?: unknown;
  unknownKind?: ClaimUnknownKind;
};

export function getClaimPaymentText(c: Record<string, unknown>): {
  status: "paid" | "unpaid" | "unknown";
  message: string | null;
  unknownKind?: ClaimUnknownKind;
} {
  const raw = (c.payment_status ?? "").toString().trim().toLowerCase();
  let status: "paid" | "unpaid" | "unknown";
  if (raw === "paid") status = "paid";
  else if (raw === "unpaid") status = "unpaid";
  else status = "unknown";

  const messageRaw = c.payment_message;
  const message =
    typeof messageRaw === "string" && messageRaw.trim() ? messageRaw.trim() : null;

  let unknownKind: ClaimUnknownKind | undefined;
  if (status === "unknown") {
    const err = (c.payment_check_error ?? "").toString().trim().toLowerCase();
    const checked = c.payment_checked_at;
    if (err === "timed_out") unknownKind = "timed_out";
    else if (!checked) unknownKind = "not_scraped";
    else unknownKind = "unknown";
  }

  return { status, message, unknownKind };
}

export function ClaimPaymentBadge({ status, message, unknownKind }: ClaimPaymentBadgeProps) {
  const value = (status ?? "").toString().trim().toLowerCase();

  let label: string;
  let cls: string;
  if (value === "paid") {
    label = "Paid";
    cls = "bg-emerald-100 text-emerald-800 border border-emerald-200";
  } else if (value === "unpaid") {
    label = "Unpaid";
    cls = "bg-red-100 text-red-800 border border-red-200";
  } else if (unknownKind === "timed_out") {
    label = "Timed out";
    cls = "bg-amber-100 text-amber-900 border border-amber-200";
  } else if (unknownKind === "not_scraped") {
    label = "Not scraped";
    cls = "bg-slate-50 text-slate-600 border border-dashed border-slate-300";
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
