"""Controlled vocabularies for Active Mine Search evidence dimensions.

Operational status, regulatory status, facility type, tenure, payment status,
and verification state are stored as separate fields. Payment status is never
derived here — it remains the Fetch Claim Records / claim_rollup contract.
"""

from __future__ import annotations

from typing import Final

# Operational status — what the mine appears to be doing. Never inferred from
# a permit, claim polygon, MSHA/BMRR status, inspection, or hours alone.
OPERATIONAL_STATUSES: Final[tuple[str, ...]] = (
    "Producing",
    "Permitted",
    "Exploration",
    "Mill/processor",
    "Care-and-maintenance",
    "Reclamation",
    "Unknown",
)

# Regulator-facing case/permit status. Independent of operational status.
REGULATORY_STATUSES: Final[tuple[str, ...]] = (
    "Active",
    "Approved",
    "Expired",
    "Closed",
    "Reclamation",
    "Unknown",
)

FACILITY_TYPES: Final[tuple[str, ...]] = (
    "Mine",
    "Mill/processor",
    "Exploration",
    "Waste/tailings",
    "Unknown",
)

TENURE_CLASSES: Final[tuple[str, ...]] = (
    "Unpatented",
    "Patented",
    "Mixed",
    "Unknown",
)

# Human Verified is never auto-assigned; it requires dated checklist evidence.
VERIFICATION_STATES: Final[tuple[str, ...]] = (
    "Candidate",
    "Cross-source confirmed",
    "Human Verified",
)

# Evidence that is allowed to support Producing. Everything else is
# corroboration, regulatory, tenure, or facility typing.
PRODUCING_SOURCE_IDS: Final[frozenset[str]] = frozenset(
    {
        "nevada_production",
        "utah_dogm_production_indicator",
    }
)

MSHA_CORROBORATION_SOURCE_IDS: Final[frozenset[str]] = frozenset(
    {
        "msha_mines",
        "msha_inspections",
        "msha_quarterly",
    }
)

BMRR_SOURCE_IDS: Final[frozenset[str]] = frozenset(
    {
        "ndep_bmrr_regulation",
        "ndep_bmrr_reclamation",
    }
)

TENURE_ONLY_SOURCE_IDS: Final[frozenset[str]] = frozenset(
    {
        "blm_claims",
    }
)


def is_known(value: str | None, vocabulary: tuple[str, ...]) -> bool:
    return bool(value) and value in vocabulary
