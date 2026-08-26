/**
 * MLRS scrape claim records table — same layout as Targets detail drilldown.
 */
import { ClaimPaymentBadge, ClaimPaymentEvidence, getClaimPaymentText } from "./claimPaymentBadge";

export type ClaimRecordsPayload = {
  fetched_at?: string | null;
  error?: string | null;
  claims?: Record<string, unknown>[] | null;
  [key: string]: unknown;
};

type ClaimRecordsMlrsPanelProps = {
  claimRecords: ClaimRecordsPayload;
  /** Optional header footnote (e.g. linked Target id). */
  subtitle?: string | null;
  onViewRaw?: (() => void) | null;
  onClear?: (() => void) | null;
  clearLoading?: boolean;
};

export function ClaimRecordsMlrsPanel({
  claimRecords,
  subtitle,
  onViewRaw,
  onClear,
  clearLoading = false,
}: ClaimRecordsMlrsPanelProps) {
  const claims = (claimRecords.claims ?? []) as Record<string, unknown>[];

  return (
    <div className="border border-emerald-200 rounded-lg overflow-hidden">
      <div className="px-3 py-2 bg-emerald-50 border-b border-emerald-100">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="min-w-0">
            <span className="text-xs font-semibold text-emerald-900 block">
              Claim Records from MLRS Scrape
            </span>
            {subtitle ? (
              <p className="mt-0.5 text-[11px] text-emerald-800/80">{subtitle}</p>
            ) : null}
            {onClear ? (
              <button
                type="button"
                disabled={clearLoading}
                onClick={onClear}
                className="mt-1 block text-left text-[11px] text-slate-600 hover:text-red-700 underline underline-offset-2 disabled:opacity-50"
              >
                {clearLoading ? "Clearing…" : "Clear all stored claims for this target"}
              </button>
            ) : null}
          </div>
          {onViewRaw ? (
            <button
              type="button"
              onClick={onViewRaw}
              className="text-[11px] text-emerald-700 hover:underline shrink-0 pt-0.5"
            >
              View Raw JSON
            </button>
          ) : null}
        </div>
      </div>
      <div className="px-3 py-2 space-y-1">
        {claimRecords.fetched_at && (
          <p className="text-[11px] text-slate-500">
            Fetched: {new Date(String(claimRecords.fetched_at)).toLocaleString()}
          </p>
        )}
        {claimRecords.error && (
          <p className="text-xs text-red-600">{String(claimRecords.error)}</p>
        )}
      </div>
      {claims.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="min-w-full text-[11px]">
            <thead className="bg-slate-50 text-slate-600 text-left">
              <tr>
                <th className="px-3 py-1.5 font-medium">Claim</th>
                <th className="px-3 py-1.5 font-medium">Serial</th>
                <th className="px-3 py-1.5 font-medium w-24">Payment</th>
                <th className="px-3 py-1.5 font-medium min-w-[16rem]">PLSS</th>
                <th className="px-3 py-1.5 font-medium">Links</th>
              </tr>
            </thead>
            <tbody>
              {claims.map((c, i) => {
                const nm = String(c.claim_name ?? c.CSE_NAME ?? "—");
                const sn = String(c.serial_number ?? c.CSE_NR ?? "—");
                const plss = String(c.plss ?? c.CSE_META ?? "—");
                const casePage = typeof c.case_page === "string" ? c.case_page : null;
                const pay = typeof c.payment_report === "string" ? c.payment_report : null;
                const payInfo = getClaimPaymentText(c);
                const rowCls =
                  payInfo.status === "unpaid"
                    ? "border-t border-blue-200 bg-blue-50"
                    : "border-t border-slate-100";
                return (
                  <tr key={`mlrs-${sn}-${i}`} className={rowCls}>
                    <td className="px-3 py-1.5 text-slate-800">{nm}</td>
                    <td className="px-3 py-1.5 font-mono text-slate-700">{sn}</td>
                    <td className="px-3 py-1.5 whitespace-nowrap">
                      <ClaimPaymentBadge status={payInfo.status} message={payInfo.message} />
                      <ClaimPaymentEvidence claim={c} />
                    </td>
                    <td
                      className="px-3 py-1.5 text-slate-600 min-w-[16rem] whitespace-normal break-words"
                      title={plss}
                    >
                      {plss}
                    </td>
                    <td className="px-3 py-1.5 space-x-2 whitespace-nowrap">
                      {casePage && (
                        <a
                          href={casePage}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-teal-700 hover:underline"
                        >
                          Case
                        </a>
                      )}
                      {pay && (
                        <a
                          href={pay}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-teal-700 hover:underline"
                        >
                          RAS
                        </a>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="px-3 py-2 text-xs text-slate-500">No claims returned.</p>
      )}
    </div>
  );
}
