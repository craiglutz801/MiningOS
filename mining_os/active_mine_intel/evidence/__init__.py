"""Active Mine Search evidence model — taxonomy, provenance, reconciliation.

Scoring and payment-status rollup stay in the existing matcher / claim_rollup
modules. This package only classifies assertions, records provenance, and
fail-closes stale, missing, or contradictory evidence.
"""

from mining_os.active_mine_intel.evidence.classify import classify_site_evidence
from mining_os.active_mine_intel.evidence.taxonomy import (
    FACILITY_TYPES,
    OPERATIONAL_STATUSES,
    REGULATORY_STATUSES,
    TENURE_CLASSES,
    VERIFICATION_STATES,
)

__all__ = [
    "classify_site_evidence",
    "OPERATIONAL_STATUSES",
    "REGULATORY_STATUSES",
    "FACILITY_TYPES",
    "TENURE_CLASSES",
    "VERIFICATION_STATES",
]
