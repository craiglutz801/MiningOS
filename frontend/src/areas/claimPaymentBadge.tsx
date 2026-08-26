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
  evidenceText: string | null;
  evidenceCode: string | null;
  sourceUrl: string | null;
  checkedAt: string | null;
} {
  const raw = (c.payment_status ?? "").toString().trim().toLowerCase();
  let status: "paid" | "unpaid" | "unknown";
  if (raw === "paid") status = "paid";
  else if (raw === "unpaid") status = "unpaid";
  else status = "unknown";

  const messageRaw = c.payment_message;
  const message =
    typeof messageRaw === "string" && messageRaw.trim() ? messageRaw.trim() : null;

  const evidenceRaw = c.payment_evidence_text;
  const evidenceText =
    typeof evidenceRaw === "string" && evidenceRaw.trim() ? evidenceRaw.trim() : null;

  const codeRaw = c.payment_evidence_code;
  const evidenceCode =
    typeof codeRaw === "string" && codeRaw.trim() ? codeRaw.trim() : null;

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
