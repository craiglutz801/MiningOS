"""Utah DOGM coverage diagnostics — full-minerals vs uranium gaps.

Diagnoses the selected DOGM layer against discovery candidates. Does not
invent missing layers: gaps are reported when candidate titles/fields or
loaded commodities omit uranium / full-minerals coverage.
"""

from __future__ import annotations

from typing import Any

URANIUM_TOKENS = ("uranium", "u3o8", "u-238", "u 308", "yellowcake")
FULL_MINERAL_TOKENS = (
    "mineral",
    "metallic",
    "metal",
    "industrial",
    "nonmetallic",
    "non-metal",
    "limestone",
    "gypsum",
    "potash",
    "phosphate",
)
COAL_TOKENS = ("coal",)
OIL_GAS_TOKENS = ("oil", "gas", "petroleum")


def _blob(values: list[Any]) -> str:
    return " ".join(str(v or "").lower() for v in values)


def _has_any(text: str, tokens: tuple[str, ...]) -> bool:
    return any(tok in text for tok in tokens)


def diagnose_dogm_coverage(
    *,
    selected_title: str | None,
    selected_url: str | None,
    candidate_titles: list[str] | None = None,
    candidate_fields: list[str] | None = None,
    commodities: list[str] | None = None,
    record_count: int = 0,
    source_status: str | None = None,
) -> dict[str, Any]:
    """Return a coverage diagnostic object for QC / UI.

    ``gaps`` lists named holes. ``source_failed`` is True when the adapter
    could not load DOGM at all (distinct from a valid empty minerals layer).
    """
    titles = [selected_title or ""] + list(candidate_titles or [])
    title_blob = _blob(titles)
    field_blob = _blob(candidate_fields or [])
    commodity_blob = _blob(commodities or [])
    combined = f"{title_blob} {field_blob} {commodity_blob}"

    uranium_in_candidates = _has_any(title_blob, URANIUM_TOKENS) or _has_any(
        field_blob, URANIUM_TOKENS
    )
    uranium_in_records = _has_any(commodity_blob, URANIUM_TOKENS)
    minerals_in_selected = _has_any(str(selected_title or "").lower(), FULL_MINERAL_TOKENS) or _has_any(
        commodity_blob, FULL_MINERAL_TOKENS
    )
    coal_only = _has_any(str(selected_title or "").lower(), COAL_TOKENS) and not minerals_in_selected

    gaps: list[dict[str, str]] = []
    if source_status in {"failed", "unavailable", "stale"}:
        return {
            "source_id": "utah_dogm",
            "selected_title": selected_title,
            "selected_url": selected_url,
            "record_count": int(record_count or 0),
            "source_failed": source_status != "stale",
            "source_stale": source_status == "stale",
            "valid_empty": False,
            "uranium_in_selected_records": False,
            "uranium_layer_seen_in_candidates": uranium_in_candidates,
            "full_minerals_indicated": False,
            "gaps": [
                {
                    "code": "dogm_source_unusable",
                    "detail": f"Utah DOGM coverage cannot be assessed ({source_status}).",
                }
            ],
            "candidate_titles": list(candidate_titles or []),
        }

    valid_empty = source_status in {"success", "cached", "empty"} and int(record_count or 0) == 0

    if not uranium_in_records:
        if uranium_in_candidates:
            gaps.append(
                {
                    "code": "uranium_layer_not_selected",
                    "detail": "A uranium-related DOGM candidate layer was seen but is not in the selected minerals extract.",
                }
            )
        else:
            gaps.append(
                {
                    "code": "uranium_not_in_coverage",
                    "detail": "Selected DOGM minerals extract has no uranium commodities and no uranium candidate layer was discovered.",
                }
            )

    if coal_only:
        gaps.append(
            {
                "code": "full_minerals_gap",
                "detail": "Selected layer appears coal-focused; full metallic/industrial minerals coverage was not confirmed.",
            }
        )
    elif not minerals_in_selected and not commodity_blob:
        gaps.append(
            {
                "code": "full_minerals_unconfirmed",
                "detail": "Could not confirm full-minerals coverage from layer title or commodity values.",
            }
        )

    if _has_any(str(selected_title or "").lower(), OIL_GAS_TOKENS):
        gaps.append(
            {
                "code": "oil_gas_layer",
                "detail": "Selected layer title looks like oil/gas rather than minerals.",
            }
        )

    return {
        "source_id": "utah_dogm",
        "selected_title": selected_title,
        "selected_url": selected_url,
        "record_count": int(record_count or 0),
        "source_failed": False,
        "source_stale": False,
        "valid_empty": valid_empty,
        "uranium_in_selected_records": uranium_in_records,
        "uranium_layer_seen_in_candidates": uranium_in_candidates,
        "full_minerals_indicated": minerals_in_selected and not coal_only,
        "gaps": gaps,
        "candidate_titles": list(candidate_titles or []),
        "combined_scan": combined[:500],
    }
