#!/usr/bin/env python3
"""Live smoke for the MLRS Aura payment-status truth layer.

Does not write to the database. Prints classification + schema health for one
public MLRS case URL. Use known cases:

  python scripts/smoke_mlrs_payment_truth.py \\
    https://mlrs.blm.gov/s/blm-case/a02t000000593dSAAQ/UT101527746

Expect:
  - HTTPS mlrs.blm.gov URLs only (anything else exits unknown/invalid)
  - Schema health reports whether Serial_Number__c / Case_Status__c /
    Next_Payment_Due_Date__c were present on the Aura record
  - Labels are current / due_today / past_due / unpaid / closed / unknown
    — Aura never emits Paid (no verified receipt field). Unpaid only from the
    BLM nonpayment warning phrase. Never Paid from a future due date.
"""

from __future__ import annotations

import json
import sys

from mining_os.services.mlrs_payment_truth import payment_from_mlrs_aura, validate_mlrs_case_url


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    url = argv[1]
    parsed = validate_mlrs_case_url(url)
    print("url_validation:", json.dumps(parsed, indent=2))
    serial = parsed.get("serial_slug") if parsed.get("ok") else None
    result = payment_from_mlrs_aura(url, expected_serial=serial)
    keep = [
        "payment_status",
        "payment_evidence_code",
        "payment_evidence_text",
        "payment_due_date",
        "payment_due_indicator",
        "payment_case_status",
        "payment_record_serial",
        "payment_source_health",
        "payment_check_source",
        "payment_checked_at",
        "payment_check_error",
        "payment_waiver",
        "payment_receipt_field",
        "payment_unverified_fields",
    ]
    print("classification:", json.dumps({k: result.get(k) for k in keep}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
