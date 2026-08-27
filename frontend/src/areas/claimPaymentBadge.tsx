/**
 * Visual badge for a BLM mining claim's maintenance-fee / due-date status.
 * Used by both the MLRS Scrape and LR2000 claim records tables on the Targets page.
 *
 * Paid / Unpaid are only shown when the backend had explicit payment or
 * nonpayment evidence. Due-date-only states are Current / Due today / Past due.
 */

export type ClaimPaymentStatus =
  | "paid"
  | "unpaid"
  | "current"
  | "due_today"
  | "past_due"
  | "closed"
  | "partial"
  | "unknown";

type ClaimPaymentBadgeProps = {
  status: unknown;
  message?: unknown;
};

const AUTHORITATIVE_PAID_CODES = new Set(["PAYMENT_RECORDED"]);
const AUTHORITATIVE_UNPAID_CODES = new Set(["NONPAYMENT_WARNING"]);
const RESOLVED_CURRENT_CODES = new Set(["NEXT_PAYMENT_DUE_CURRENT", "SMALL_MINER_WAIVER_CURRENT"]);
const RESOLVED_DUE_TODAY_CODES = new Set(["NEXT_PAYMENT_DUE_TODAY"]);
const RESOLVED_PAST_DUE_CODES = new Set(["NEXT_PAYMENT_DUE_PAST"]);
const RESOLVED_CLOSED_CODES = new Set(["CASE_CLOSED"]);

function canonicalClaimPaymentStatus(
  raw: string,
  evidenceCode: string | null,
): ClaimPaymentStatus {
  const code = evidenceCode || "";
  if (raw === "paid") return AUTHORITATIVE_PAID_CODES.has(code) ? "paid" : "unknown";
  if (raw === "unpaid") return AUTHORITATIVE_UNPAID_CODES.has(code) ? "unpaid" : "unknown";
  if (raw === "current") return RESOLVED_CURRENT_CODES.has(code) ? "current" : "unknown";
  if (raw === "due_today") return RESOLVED_DUE_TODAY_CODES.has(code) ? "due_today" : "unknown";
  if (raw === "past_due") return RESOLVED_PAST_DUE_CODES.has(code) ? "past_due" : "unknown";
  if (raw === "closed") return RESOLVED_CLOSED_CODES.has(code) ? "closed" : "unknown";
  if (raw === "partial") return "partial";
  return "unknown";
}

export function getClaimPaymentText(c: Record<string, unknown>): {
  status: ClaimPaymentStatus;
  message: string | null;
  evidenceText: string | null;
  evidenceCode: string | null;
  sourceUrl: string | null;
  checkedAt: string | null;
} {
  const raw = (c.payment_status ?? "").toString().trim().toLowerCase();
  const allowed: ClaimPaymentStatus[] = [
    "paid",
    "unpaid",
    "current",
    "due_today",
    "past_due",
    "closed",
    "partial",
    "unknown",
  ];
  const parsed: ClaimPaymentStatus = allowed.includes(raw as ClaimPaymentStatus)
    ? (raw as ClaimPaymentStatus)
    : "unknown";

  const messageRaw = c.payment_message;
  const message =
    typeof messageRaw === "string" && messageRaw.trim() ? messageRaw.trim() : null;

  const evidenceRaw = c.payment_evidence_text;
  const evidenceText =
    typeof evidenceRaw === "string" && evidenceRaw.trim() ? evidenceRaw.trim() : null;

  const codeRaw = c.payment_evidence_code;
  const evidenceCode =
    typeof codeRaw === "string" && codeRaw.trim() ? codeRaw.trim() : null;

  const status = canonicalClaimPaymentStatus(parsed, evidenceCode);

  const sourceRaw = c.payment_source_url;
  const sourceUrl =
    typeof sourceRaw === "string" && sourceRaw.trim() ? sourceRaw.trim() : null;

  const checkedRaw = c.payment_checked_at;
  const checkedAt =
    typeof checkedRaw === "string" && checkedRaw.trim() ? checkedRaw.trim() : null;

  return { status, message, evidenceText, evidenceCode, sourceUrl, checkedAt };
}

export function formatPaymentCheckedAt(checkedAt: string | null): string | null {
  if (!checkedAt) return null;
  const dt = new Date(checkedAt);
  if (Number.isNaN(dt.getTime())) return checkedAt;
  return dt.toLocaleString();
}

const BADGE_STYLES: Record<ClaimPaymentStatus, { label: string; cls: string }> = {
  paid: { label: "Paid", cls: "bg-emerald-100 text-emerald-800 border border-emerald-200" },
  unpaid: { label: "Unpaid", cls: "bg-red-100 text-red-800 border border-red-200" },
  current: { label: "Current", cls: "bg-sky-100 text-sky-800 border border-sky-200" },
  due_today: { label: "Due today", cls: "bg-amber-100 text-amber-900 border border-amber-200" },
  past_due: { label: "Past due", cls: "bg-orange-100 text-orange-900 border border-orange-200" },
  closed: { label: "Closed", cls: "bg-slate-200 text-slate-700 border border-slate-300" },
  partial: { label: "Partial", cls: "bg-violet-100 text-violet-800 border border-violet-200" },
  unknown: { label: "Unknown", cls: "bg-slate-100 text-slate-700 border border-slate-200" },
};

export function ClaimPaymentBadge({ status, message }: ClaimPaymentBadgeProps) {
  const value = (status ?? "").toString().trim().toLowerCase() as ClaimPaymentStatus;
  const style = BADGE_STYLES[value] ?? BADGE_STYLES.unknown;
  const tip = typeof message === "string" && message.trim() ? message.trim() : style.label;

  return (
    <span
      title={tip}
      className={`inline-flex items-center gap-1 rounded px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${style.cls}`}
    >
      {style.label}
    </span>
  );
}

type ClaimPaymentEvidenceProps = {
  claim: Record<string, unknown>;
  showUnpaidMessage?: boolean;
};

export function ClaimPaymentEvidence({
  claim,
  showUnpaidMessage = false,
}: ClaimPaymentEvidenceProps) {
  const payInfo = getClaimPaymentText(claim);
  const checkedLabel = formatPaymentCheckedAt(payInfo.checkedAt);
  const evidence = payInfo.evidenceText;
  const showMessage = showUnpaidMessage && payInfo.status === "unpaid" && payInfo.message;

  if (!evidence && !checkedLabel && !showMessage) return null;

  return (
    <div className="mt-0.5 space-y-0.5 max-w-[18rem]">
      {showMessage ? (
        <p className="text-[10px] text-blue-900 leading-tight">{payInfo.message}</p>
      ) : null}
      {evidence ? (
        <p className="text-[10px] text-slate-600 leading-tight" title={payInfo.evidenceCode ?? undefined}>
          {evidence}
        </p>
      ) : null}
      {checkedLabel ? (
        <p className="text-[10px] text-slate-500 leading-tight">Observed {checkedLabel}</p>
      ) : null}
    </div>
  );
}
