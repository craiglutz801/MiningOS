"""Areas of focus: list, add, ingest from data_files, BLM status."""

from __future__ import annotations

import csv
import json
import logging
import math
import re
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, ProgrammingError

from mining_os.db import get_engine

log = logging.getLogger("mining_os.areas_of_focus")

DATA_FILES_DIR = Path(__file__).resolve().parents[2] / "data_files"


def _display_trs(val: str | None, kind: str = "tr") -> str | None:
    """Strip zero-padding from stored PLSS components for display.
    Township/Range: '0300S' → '30S', '0040S' → '4S'.
    Section: '010' → '10', '002' → '2'.
    """
    if not val:
        return val
    if kind == "sec":
        return str(int(val)) if val.isdigit() else val
    m = re.match(r"^(\d+)([NSEW])$", val)
    if m:
        return f"{int(m.group(1)) // 10}{m.group(2)}"
    return val


def _format_area_display(row: dict) -> dict:
    """Apply display formatting to township/range/section."""
    row["township"] = _display_trs(row.get("township"), "tr")
    row["range"] = _display_trs(row.get("range"), "tr")
    row["section"] = _display_trs(row.get("section"), "sec")
    return row


RETRIEVAL_TYPE_KNOWN_MINE = "Known Mine"
RETRIEVAL_TYPE_USER_ADDED = "User Added"


def _normalize_retrieval_type(value: str | None, source: str | None = None) -> str:
    """Normalize retrieval_type to the canonical UI labels."""
    v = (value or "").strip().lower()
    if v in {"known mine", "known_mine", "known-mine"}:
        return RETRIEVAL_TYPE_KNOWN_MINE
    if v in {"user added", "user_added", "user-added", "manual"}:
        return RETRIEVAL_TYPE_USER_ADDED
    if (source or "").strip().lower() == "mrds_auto":
        return RETRIEVAL_TYPE_KNOWN_MINE
    return RETRIEVAL_TYPE_USER_ADDED


def _parse_coords(s: str) -> tuple[float | None, float | None]:
    """Parse 'lat, lon' or '38.5136, -113.2622' -> (lat, lon)."""
    if not s or not isinstance(s, str):
        return None, None
    s = s.strip()
    parts = [p.strip() for p in re.split(r"[,;\s]+", s) if p.strip()]
    if len(parts) >= 2:
        try:
            return float(parts[0]), float(parts[1])
        except ValueError:
            pass
    return None, None


_TITLE_CASE_STOPWORDS = {"and", "of", "the", "in", "on", "to", "for", "with"}


def _clean_mineral_name(raw: str) -> str | None:
    """Clean a single mineral name: strip numbers, parens, title-case."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or s.lower() == "none":
        return None
    s = re.sub(r"\(.*?\)", "", s)          # remove (parenthesized notes)
    s = re.sub(r"[^a-zA-Z\s\-]", "", s)   # strip numbers and special chars
    s = re.sub(r"\s+", " ", s).strip()     # collapse whitespace
    if len(s) < 2:
        return None
    words = s.split(" ")
    out_words: list[str] = []
    for i, w in enumerate(words):
        lw = w.lower()
        out_words.append(lw if (i > 0 and lw in _TITLE_CASE_STOPWORDS) else w.title())
    return " ".join(out_words)


# ---------------------------------------------------------------------------
# Mineral / commodity code → canonical full name
# ---------------------------------------------------------------------------
# Strict rule: NEVER store a 1-2 letter chemical symbol or a USGS MRDS
# commodity abbreviation in `areas_of_focus.minerals`. Always store the
# full, title-cased name. The map below covers (a) all chemical elements
# that show up in MRDS commodity strings and (b) the common MRDS 3-5
# character commodity codes (Sdg, Cly, Lst, Stnc, Stnd, Gyp, Pum, Sil, Vol,
# Stn, Gem, Geo, Oilsa, Lstd, Clyk, Clyfr, Qtz, etc.). All keys are
# lower-cased; lookup is case-insensitive.

_MINERAL_CODE_TO_NAME: dict[str, str] = {
    # --- Chemical elements (MRDS uses chemical symbols heavily) ----------
    "ag": "Silver", "al": "Aluminum", "as": "Arsenic", "au": "Gold",
    "b": "Boron", "ba": "Barium", "be": "Beryllium", "bi": "Bismuth",
    "br": "Bromine", "c": "Carbon", "ca": "Calcium", "cd": "Cadmium",
    "ce": "Cerium", "cl": "Chlorine", "co": "Cobalt", "cr": "Chromium",
    "cs": "Cesium", "cu": "Copper", "dy": "Dysprosium", "er": "Erbium",
    "eu": "Europium", "f": "Fluorine", "fe": "Iron", "ga": "Gallium",
    "gd": "Gadolinium", "ge": "Germanium", "h": "Hydrogen", "he": "Helium",
    "hf": "Hafnium", "hg": "Mercury", "ho": "Holmium", "i": "Iodine",
    "in": "Indium", "ir": "Iridium", "k": "Potassium", "la": "Lanthanum",
    "li": "Lithium", "lu": "Lutetium", "mg": "Magnesium", "mn": "Manganese",
    "mo": "Molybdenum", "n": "Nitrogen", "na": "Sodium", "nb": "Niobium",
    "nd": "Neodymium", "ne": "Neon", "ni": "Nickel", "o": "Oxygen",
    "os": "Osmium", "p": "Phosphorus", "pb": "Lead", "pd": "Palladium",
    "pr": "Praseodymium", "pt": "Platinum", "rb": "Rubidium", "re": "Rhenium",
    "rh": "Rhodium", "ru": "Ruthenium", "s": "Sulfur", "sb": "Antimony",
    "sc": "Scandium", "se": "Selenium", "si": "Silicon", "sm": "Samarium",
    "sn": "Tin", "sr": "Strontium", "ta": "Tantalum", "tb": "Terbium",
    "te": "Tellurium", "th": "Thorium", "ti": "Titanium", "tl": "Thallium",
    "tm": "Thulium", "u": "Uranium", "v": "Vanadium", "w": "Tungsten",
    "y": "Yttrium", "yb": "Ytterbium", "zn": "Zinc", "zr": "Zirconium",

    # --- USGS MRDS commodity abbreviations (industrial / non-metal) ------
    "sdg": "Sand and Gravel",
    "cly": "Clay",
    "clyk": "Kaolin Clay",
    "clyfr": "Refractory Clay",
    "clyb": "Bentonite Clay",
    "lst": "Limestone",
    "lstd": "Dolomitic Limestone",
    "stnc": "Crushed Stone",
    "stnd": "Dimension Stone",
    "stn": "Stone",
    "gyp": "Gypsum",
    "pum": "Pumice",
    "sil": "Silica",
    "vol": "Volcanic Material",
    "gem": "Gemstone",
    "geo": "Geothermal",
    "oilsa": "Oil Sand",
    "qtz": "Quartz",
    "asb": "Asbestos",
    "bar": "Barite",
    "bx": "Bauxite",
    "cem": "Cement",
    "coa": "Coal",
    "diam": "Diamond",
    "dol": "Dolomite",
    "fld": "Feldspar",
    "fls": "Fluorspar",
    "gar": "Garnet",
    "gra": "Graphite",
    "mag": "Magnetite",
    "mar": "Marble",
    "mca": "Mica",
    "olv": "Olivine",
    "per": "Perlite",
    "pyr": "Pyrite",
    "sal": "Salt",
    "sap": "Sapphire",
    "slr": "Sulfur",
    "tlc": "Talc",
    "trn": "Turquoise",
    "ver": "Vermiculite",
    "wol": "Wollastonite",
    "zeo": "Zeolite",
    "phs": "Phosphate",
    "phr": "Phosphate Rock",
    "pot": "Potash",
    "soda": "Soda Ash",
    "ree": "Rare Earth Elements",
    "pge": "Platinum Group Elements",
    "pgm": "Platinum Group Metals",
    "ind": "Industrial Minerals",
    "agg": "Aggregate",
    "tng": "Tungsten",
    # additional MRDS abbreviations seen in the wild
    "dit": "Diatomite",
    "nah": "Nahcolite",
    "mbl": "Marble",
    "nas": "Sodium Sulfate",
    "abrg": "Abrasive Garnet",
    "lwa": "Lightweight Aggregate",
    "stnf": "Field Stone",
    "sla": "Slate",
    "grt": "Garnet",
    "grf": "Graphite",
    "sp": "Specimen",
    "spc": "Specimen",
    "mic": "Mica",
    "lstc": "Crushed Limestone",
    "abr": "Abrasive",
    "kyn": "Kyanite",
    # --- MRDS PGE / rare-earth / clay / heavy-mineral codes (seen on prod) ---
    "pgept": "Platinum",
    "pgerh": "Rhodium",
    "pgepd": "Palladium",
    "pgeir": "Iridium",
    "pgeos": "Osmium",
    "pgeru": "Ruthenium",
    "reey": "Yttrium",
    "reela": "Lanthanum",
    "reece": "Cerium",
    "gemsp": "Gemstone",
    "clybn": "Bentonite",
    "clybk": "Ball Clay",
    "tim": "Ilmenite",
    "oilr": "Petroleum",
    "ra": "Radium",
    "coal": "Coal",
    "sa": "Salt",
    "pea": "Peat",
    "coas": "Coastal Sand",
    "ja": "Jade",
}


# If a space-separated token contains any of these words, treat the whole
# string as a human phrase (not an MRDS code run). Prevents e.g. "Gas Co"
# from becoming Natural Gas + Cobalt.
_MINERAL_PHRASE_TRIGGER_WORDS: frozenset[str] = frozenset(
    {
        # NOTE: Do not include two-letter English words that double as element
        # symbols (e.g. "as" = arsenic, "in" = indium). Rely on longer triggers
        # so MRDS code runs like "Au Cu … As …" still expand correctly.
        "and",
        "or",
        "the",
        "of",
        "for",
        "with",
        "from",
        "sand",
        "gravel",
        "stone",
        "clay",
        "rare",
        "earth",
        "elements",
        "group",
        "metals",
        "oil",
        "gas",
        "natural",
        "crushed",
        "dimension",
        "volcanic",
        "field",
        "lightweight",
        "refractory",
        "dolomitic",
        "kaolin",
        "specimen",
        "reservoir",
        "petroleum",
        "aggregate",
        "industrial",
        "minerals",
        "coastal",
        "oil",
        "sand",
    }
)


def _expand_mineral_codes(token: str) -> List[str]:
    """
    Expand a single token into one or more full mineral names.

    Rules (in order):
      1. If the whole token is itself a known code (e.g. "Be", "Sdg"), expand to its name.
      2. If the token splits on whitespace into pieces and EVERY piece is a known code
         (e.g. "Pb Ag Zn"), expand each piece.
      3. If the token splits into short alphanumeric MRDS-style tokens and at least
         one maps (e.g. "Au Ag Pgept" where PGEpt was missing before), expand each
         known piece and drop unknowns only if we produced at least one name; otherwise
         fall through.
      4. If any piece looks like natural language (phrase trigger words), keep the
         whole string as one cleaned mineral name ("Sand and Gravel", "Gas Co").
      5. Otherwise treat the token as a real mineral name via _clean_mineral_name.
    """
    s = (token or "").strip()
    if not s:
        return []
    key = s.lower()
    if key in _MINERAL_CODE_TO_NAME:
        return [_MINERAL_CODE_TO_NAME[key]]
    pieces = re.split(r"[\s\-]+", s)
    pieces = [p for p in pieces if p]
    if len(pieces) >= 2:
        plowers = [p.lower() for p in pieces]
        if any(p in _MINERAL_PHRASE_TRIGGER_WORDS for p in plowers):
            cleaned = _clean_mineral_name(s)
            return [cleaned] if cleaned else []
        if all(p.lower() in _MINERAL_CODE_TO_NAME for p in pieces):
            return [_MINERAL_CODE_TO_NAME[p.lower()] for p in pieces]
        if all(re.fullmatch(r"[A-Za-z0-9]{1,6}", p) for p in pieces):
            out: list[str] = []
            for p in pieces:
                pl = p.lower()
                if pl in _MINERAL_CODE_TO_NAME:
                    nm = _MINERAL_CODE_TO_NAME[pl]
                    if nm not in out:
                        out.append(nm)
            if out:
                return out
    cleaned = _clean_mineral_name(s)
    return [cleaned] if cleaned else []


def _normalize_minerals(cell: Any) -> List[str]:
    """Accept a string or list, return full-name, title-cased, deduplicated mineral names.

    Also expands USGS MRDS chemical symbols (Au, Be, F, Pb, ...) and commodity
    abbreviations (Sdg, Cly, Lst, ...) to canonical full names. Codes are NEVER
    stored — see `_MINERAL_CODE_TO_NAME` and `_expand_mineral_codes`.
    """
    if cell is None:
        return []
    if isinstance(cell, list):
        parts = cell
    elif isinstance(cell, str) and cell.strip():
        parts = re.split(r"[,;/|]", cell)
    else:
        return []
    out: list[str] = []
    for raw in parts:
        for name in _expand_mineral_codes(str(raw)):
            if name and name not in out:
                out.append(name)
    return out


def _normalize_plss(plss: str | None, default_state: str | None = None) -> str | None:
    """
    Normalize PLSS to a section-level (sector) unique key: State + Township + Range + Section.
    default_state is used when the PLSS string does not start with a 2-letter state code (e.g. CSV State column).
    Returns None if empty.
    """
    if plss is None or not isinstance(plss, str):
        return None
    s = plss.strip()
    if not s:
        return None
    try:
        from mining_os.services.blm_plss import parse_plss_string
        parsed = parse_plss_string(s, default_state=default_state or "UT")
        if parsed:
            state = (parsed.get("state") or "").strip().upper() or "XX"
            twp = parsed.get("township") or ""
            rng = parsed.get("range") or ""
            sec = parsed.get("section") or ""
            if twp and rng:
                key = f"{state} {twp} {rng}".strip()
                if sec:
                    key = f"{key} {sec}"
                return key if key else None
    except Exception:
        pass
    # Fallback: trim, uppercase, collapse whitespace (e.g. county names or non-standard PLSS)
    s = re.sub(r"\s+", " ", s).upper().strip()
    return s if s else None


def _parse_plss_to_components(location_plss: str | None, default_state: str = "UT") -> dict | None:
    """
    Parse location_plss into state_abbr, township, range, section (sector), and meridian.
    Returns dict with keys state_abbr, township, range, section, meridian; section/meridian may be None.
    Returns None if PLSS is empty or unparseable.
    """
    if not location_plss or not isinstance(location_plss, str) or not location_plss.strip():
        return None
    try:
        from mining_os.services.blm_plss import parse_plss_string
        parsed = parse_plss_string(location_plss.strip(), default_state=default_state)
        if not parsed or not parsed.get("township") or not parsed.get("range"):
            return None
        state = (parsed.get("state") or default_state).strip().upper()[:2]
        township = (parsed.get("township") or "").strip() or None
        range_val = (parsed.get("range") or "").strip() or None
        section = (parsed.get("section") or "").strip() or None
        if not township or not range_val:
            return None
        try:
            from mining_os.services.fetch_claim_records import STATE_MERIDIAN, DEFAULT_MERIDIAN
            meridian = STATE_MERIDIAN.get(state, DEFAULT_MERIDIAN)
        except Exception:
            meridian = "26"
        return {
            "state_abbr": state,
            "township": township,
            "range": range_val,
            "section": section or None,
            "meridian": meridian,
        }
    except Exception:
        return None


def backfill_plss_components() -> int:
    """
    Backfill state_abbr, township, range, section, meridian from location_plss for rows
    where township is null and location_plss is set. Returns number of rows updated.
    """
    eng = get_engine()
    updated = 0
    try:
        with eng.begin() as conn:
            rows = conn.execute(
                text("""
                SELECT id, location_plss, state_abbr FROM areas_of_focus
                WHERE location_plss IS NOT NULL AND TRIM(location_plss) != ''
                  AND (township IS NULL OR state_abbr IS NULL)
                """),
            ).mappings().all()
            for row in rows:
                comp = _parse_plss_to_components(row["location_plss"], default_state=(row.get("state_abbr") or "UT"))
                if not comp:
                    continue
                conn.execute(
                    text("""
                    UPDATE areas_of_focus SET
                      state_abbr = :state_abbr, township = :township, "range" = :range_val, section = :section,
                      meridian = :meridian, updated_at = now()
                    WHERE id = :id
                    """),
                    {
                        "id": row["id"],
                        "state_abbr": comp["state_abbr"],
                        "township": comp["township"],
                        "range_val": comp["range"],
                        "section": comp["section"],
                        "meridian": comp["meridian"],
                    },
                )
                updated += 1
    except Exception as e:
        if "column" in str(e).lower() and ("township" in str(e).lower() or "section" in str(e).lower()):
            log.debug("backfill_plss_components skipped (columns may not exist yet): %s", e)
            return 0
        raise
    if updated:
        log.info("Backfilled PLSS components (state, township, range, section) for %s rows", updated)
    return updated


def _condense_rows_by_plss(rows: List[dict]) -> List[dict]:
    """Group rows by normalized PLSS, merge minerals/report_links; return one dict per PLSS for upsert."""
    by_plss: Dict[str, list] = {}
    for r in rows:
        key = _normalize_plss(r.get("location_plss")) or "__no_plss__"
        if key not in by_plss:
            by_plss[key] = []
        by_plss[key].append(r)
    out = []
    for key, group in by_plss.items():
        all_minerals = sum((g.get("minerals") or [] for g in group), [])
        all_links = sum((g.get("report_links") or [] for g in group), [])
        merged = {
            "name": (group[0].get("name") or "").strip() or "Unknown",
            "location_plss": next((g.get("location_plss") for g in group if g.get("location_plss")), None),
            "location_coords": next((g.get("location_coords") for g in group if g.get("location_coords")), None),
            "latitude": next((g.get("latitude") for g in group if g.get("latitude") is not None), None),
            "longitude": next((g.get("longitude") for g in group if g.get("longitude") is not None), None),
            "minerals": list(dict.fromkeys(all_minerals)),
            "status": next((g.get("status") for g in group if g.get("status")), "unknown"),
            "report_links": list(dict.fromkeys(all_links)),
            "report_summary": next((g.get("report_summary") for g in group if g.get("report_summary")), None),
            "validity_notes": next((g.get("validity_notes") for g in group if g.get("validity_notes")), None),
            "source": group[0].get("source") or "manual",
            "external_id": next((g.get("external_id") for g in group if g.get("external_id")), None),
            "blm_case_url": next((g.get("blm_case_url") for g in group if g.get("blm_case_url")), None),
            "blm_serial_number": next((g.get("blm_serial_number") for g in group if g.get("blm_serial_number")), None),
            "roi_score": max((g.get("roi_score") or 0 for g in group), default=None),
        }
        out.append(merged)
    return out


def backfill_plss_normalized_to_section() -> int:
    """
    Update plss_normalized for all rows to section-level (sector) key. Call after 008 so
    uniqueness is at State+Township+Range+Section. Returns number of rows updated.
    """
    eng = get_engine()
    updated = 0
    with eng.begin() as conn:
        rows = conn.execute(
            text("SELECT id, location_plss FROM areas_of_focus WHERE location_plss IS NOT NULL AND TRIM(location_plss) != ''"),
        ).mappings().all()
        for row in rows:
            new_key = _normalize_plss(row["location_plss"])
            if new_key is None:
                continue
            conn.execute(
                text("UPDATE areas_of_focus SET plss_normalized = :key, updated_at = now() WHERE id = :id"),
                {"key": new_key, "id": row["id"]},
            )
            updated += 1
    if updated:
        log.info("Backfilled plss_normalized to section-level for %s rows", updated)
    return updated


def _normalize_plss_filter_component(value: str | None, kind: str) -> str | None:
    """Normalize a single PLSS filter value (township, range, or section) for WHERE match."""
    if not value or not isinstance(value, str):
        return None
    s = re.sub(r"^\s*(T(OWNSHIP)?|R(ANGE)?|S(EC(TION)?)?)\s*", "", value.strip(), flags=re.I).strip() or value.strip()
    if not s:
        return None
    try:
        from mining_os.services.blm_plss import _normalize_township, _normalize_range, _normalize_section
        if kind == "township":
            return _normalize_township(s) or s.strip().upper()
        if kind == "range":
            return _normalize_range(s) or s.strip().upper()
        if kind == "section":
            return _normalize_section(s) or (s.zfill(3) if s.isdigit() and 1 <= int(s) <= 36 else None)
    except Exception:
        pass
    return s.strip().upper() if s else None


def list_areas(
    mineral: str | None = None,
    status: str | None = None,
    target_status: str | None = None,
    state_abbr: str | None = None,
    claim_type: str | None = None,
    retrieval_type: str | None = None,
    township: str | None = None,
    range_val: str | None = None,
    sector: str | None = None,
    name: str | None = None,
    limit: int = 5000,
) -> List[dict]:
    eng = get_engine()
    filters = ["1=1"]
    params: dict = {"limit": limit}
    normalized_priority_sql = (
        "CASE "
        "WHEN COALESCE(a.priority, 'monitoring_low') = 'low' THEN 'monitoring_low' "
        "WHEN COALESCE(a.priority, 'monitoring_low') = 'medium' THEN 'monitoring_med' "
        "WHEN COALESCE(a.priority, 'monitoring_low') = 'high' THEN 'monitoring_high' "
        "ELSE COALESCE(a.priority, 'monitoring_low') "
        "END"
    )
    if name:
        filters.append("name ILIKE :name_pat")
        params["name_pat"] = f"%{name.strip()}%"
    if mineral:
        filters.append(":mineral = ANY(minerals)")
        params["mineral"] = mineral.strip()
    if status:
        filters.append("status = :status")
        params["status"] = status.strip().lower()
    if target_status and target_status.strip():
        raw_target_status = target_status.strip().lower()
        if raw_target_status in VALID_TARGET_STATUSES:
            filters.append(f"{normalized_priority_sql} = :target_status")
            params["target_status"] = _normalize_target_status(raw_target_status)
    if state_abbr:
        filters.append("(state_abbr = :state_abbr OR (state_abbr IS NULL AND :state_abbr = ''))")
        params["state_abbr"] = state_abbr.strip().upper()
    if claim_type:
        filters.append("(claim_type = :claim_type OR (claim_type IS NULL AND :claim_type = ''))")
        params["claim_type"] = claim_type.strip().lower()
    if retrieval_type:
        filters.append("COALESCE(retrieval_type, :retrieval_type_default) = :retrieval_type")
        params["retrieval_type"] = _normalize_retrieval_type(retrieval_type)
        params["retrieval_type_default"] = RETRIEVAL_TYPE_USER_ADDED
    # Advanced search: PLSS at section (sector) level. plss_normalized format: "STATE TWP RNG" or "STATE TWP RNG SEC"
    t_norm = _normalize_plss_filter_component(township, "township")
    r_norm = _normalize_plss_filter_component(range_val, "range")
    s_norm = _normalize_plss_filter_component(sector, "section")
    if t_norm:
        filters.append("(plss_normalized IS NOT NULL AND TRIM(SPLIT_PART(TRIM(plss_normalized), ' ', 2)) = :township_norm)")
        params["township_norm"] = t_norm
    if r_norm:
        filters.append("(plss_normalized IS NOT NULL AND TRIM(SPLIT_PART(TRIM(plss_normalized), ' ', 3)) = :range_norm)")
        params["range_norm"] = r_norm
    if s_norm:
        filters.append("(plss_normalized IS NOT NULL AND TRIM(SPLIT_PART(TRIM(plss_normalized), ' ', 4)) = :sector_norm)")
        params["sector_norm"] = s_norm
    where = " AND ".join(filters)
    # report_count = focus_reports count + report_links array length; magnitude = roi_score + report bonus
    sql = f"""
    WITH area_report_counts AS (
      SELECT a.id,
             (SELECT COUNT(*) FROM focus_reports fr WHERE fr.area_id = a.id)
             + COALESCE(array_length(a.report_links, 1), 0) AS report_count
      FROM areas_of_focus a
      WHERE {where}
    )
    SELECT a.id, a.name, a.location_plss, a.location_coords, a.latitude, a.longitude,
           a.minerals, a.status, a.status_checked_at, a.report_links, a.report_summary,
           a.validity_notes, a.source, a.external_id, a.blm_case_url, a.blm_serial_number,
           COALESCE(a.priority, 'low') AS priority, a.state_abbr, a.meridian, a.claim_type, COALESCE(a.retrieval_type, 'User Added') AS retrieval_type, a.created_at, a.updated_at,
           a.township, a."range", a.section, COALESCE(a.is_uploaded, false) AS is_uploaded,
           COALESCE(arc.report_count, 0)::int AS report_count,
           (COALESCE(a.roi_score, 0) + COALESCE(arc.report_count, 0) * 5)::int AS magnitude_score
    FROM areas_of_focus a
    LEFT JOIN area_report_counts arc ON arc.id = a.id
    WHERE {where}
    ORDER BY CASE COALESCE(a.priority, 'monitoring_low') WHEN 'ownership' THEN 1 WHEN 'due_diligence' THEN 2 WHEN 'negotiation' THEN 3 WHEN 'monitoring_high' THEN 4 WHEN 'high' THEN 4 WHEN 'monitoring_med' THEN 5 WHEN 'medium' THEN 5 WHEN 'monitoring_low' THEN 6 WHEN 'low' THEN 6 ELSE 7 END,
             (COALESCE(a.roi_score, 0) + COALESCE(arc.report_count, 0) * 5) DESC NULLS LAST, a.updated_at DESC
    LIMIT :limit
    """
    with eng.begin() as conn:
        try:
            rows = conn.execute(text(sql), params).mappings().all()
            return [_format_area_display(dict(r)) for r in rows]
        except Exception as e:
            err = str(e).lower()
            if "priority" in err and ("column" in err or "does not exist" in err):
                sql_fallback = f"""
                WITH area_report_counts AS (
                  SELECT a.id,
                         (SELECT COUNT(*) FROM focus_reports fr WHERE fr.area_id = a.id)
                         + COALESCE(array_length(a.report_links, 1), 0) AS report_count
                  FROM areas_of_focus a
                  WHERE {where}
                )
                SELECT a.id, a.name, a.location_plss, a.location_coords, a.latitude, a.longitude,
                       a.minerals, a.status, a.status_checked_at, a.report_links, a.report_summary,
                       a.validity_notes, a.source, a.external_id, a.blm_case_url, a.blm_serial_number,
                       'monitoring_low' AS priority, a.state_abbr, a.meridian, a.claim_type, COALESCE(a.retrieval_type, 'User Added') AS retrieval_type, a.created_at, a.updated_at,
                       a.township, a."range", a.section, COALESCE(a.is_uploaded, false) AS is_uploaded,
                       COALESCE(arc.report_count, 0)::int AS report_count,
                       (COALESCE(a.roi_score, 0) + COALESCE(arc.report_count, 0) * 5)::int AS magnitude_score
                FROM areas_of_focus a
                LEFT JOIN area_report_counts arc ON arc.id = a.id
                WHERE {where}
                ORDER BY (COALESCE(a.roi_score, 0) + COALESCE(arc.report_count, 0) * 5) DESC NULLS LAST, a.updated_at DESC
                LIMIT :limit
                """
                rows = conn.execute(text(sql_fallback), params).mappings().all()
                return [_format_area_display(dict(r)) for r in rows]
            err = str(e).lower()
            if "township" in err or "range" in err or "section" in err or "is_uploaded" in err:
                sql_no_plss_cols = f"""
                WITH area_report_counts AS (
                  SELECT a.id,
                         (SELECT COUNT(*) FROM focus_reports fr WHERE fr.area_id = a.id)
                         + COALESCE(array_length(a.report_links, 1), 0) AS report_count
                  FROM areas_of_focus a
                  WHERE {where}
                )
                SELECT a.id, a.name, a.location_plss, a.location_coords, a.latitude, a.longitude,
                       a.minerals, a.status, a.status_checked_at, a.report_links, a.report_summary,
                       a.validity_notes, a.source, a.external_id, a.blm_case_url, a.blm_serial_number,
                       COALESCE(a.priority, 'low') AS priority, a.state_abbr, a.meridian, a.claim_type, COALESCE(a.retrieval_type, 'User Added') AS retrieval_type, a.created_at, a.updated_at,
                       COALESCE(arc.report_count, 0)::int AS report_count,
                       (COALESCE(a.roi_score, 0) + COALESCE(arc.report_count, 0) * 5)::int AS magnitude_score
                FROM areas_of_focus a
                LEFT JOIN area_report_counts arc ON arc.id = a.id
                WHERE {where}
                ORDER BY CASE COALESCE(a.priority, 'monitoring_low') WHEN 'ownership' THEN 1 WHEN 'due_diligence' THEN 2 WHEN 'negotiation' THEN 3 WHEN 'monitoring_high' THEN 4 WHEN 'high' THEN 4 WHEN 'monitoring_med' THEN 5 WHEN 'medium' THEN 5 WHEN 'monitoring_low' THEN 6 WHEN 'low' THEN 6 ELSE 7 END,
                         (COALESCE(a.roi_score, 0) + COALESCE(arc.report_count, 0) * 5) DESC NULLS LAST, a.updated_at DESC
                LIMIT :limit
                """
                rows = conn.execute(text(sql_no_plss_cols), params).mappings().all()
                out = [dict(r) for r in rows]
                for o in out:
                    o["township"] = None
                    o["range"] = None
                    o["section"] = None
                    o["is_uploaded"] = False
                return out
            log.exception("list_areas failed: %s", e)
            raise


def areas_summary() -> dict:
    """Return uncapped dashboard counts for targets by normalized target status."""
    eng = get_engine()
    normalized_priority_sql = (
        "CASE "
        "WHEN COALESCE(a.priority, 'monitoring_low') = 'low' THEN 'monitoring_low' "
        "WHEN COALESCE(a.priority, 'monitoring_low') = 'medium' THEN 'monitoring_med' "
        "WHEN COALESCE(a.priority, 'monitoring_low') = 'high' THEN 'monitoring_high' "
        "ELSE COALESCE(a.priority, 'monitoring_low') "
        "END"
    )
    sql = f"""
    SELECT
      COUNT(*)::int AS total_count,
      COUNT(*) FILTER (WHERE {normalized_priority_sql} = 'monitoring_high')::int AS monitoring_high,
      COUNT(*) FILTER (WHERE {normalized_priority_sql} = 'monitoring_med')::int AS monitoring_med,
      COUNT(*) FILTER (WHERE {normalized_priority_sql} = 'monitoring_low')::int AS monitoring_low,
      COUNT(*) FILTER (WHERE {normalized_priority_sql} = 'negotiation')::int AS negotiation,
      COUNT(*) FILTER (WHERE {normalized_priority_sql} = 'due_diligence')::int AS due_diligence,
      COUNT(*) FILTER (WHERE {normalized_priority_sql} = 'ownership')::int AS ownership
    FROM areas_of_focus a
    """
    with eng.begin() as conn:
        try:
            row = conn.execute(text(sql)).mappings().first() or {}
            return {
                "total_count": int(row.get("total_count") or 0),
                "target_status_counts": {
                    "monitoring_high": int(row.get("monitoring_high") or 0),
                    "monitoring_med": int(row.get("monitoring_med") or 0),
                    "monitoring_low": int(row.get("monitoring_low") or 0),
                    "negotiation": int(row.get("negotiation") or 0),
                    "due_diligence": int(row.get("due_diligence") or 0),
                    "ownership": int(row.get("ownership") or 0),
                },
            }
        except Exception as e:
            err = str(e).lower()
            if "priority" in err and ("column" in err or "does not exist" in err):
                total = int(conn.execute(text("SELECT COUNT(*)::int FROM areas_of_focus")).scalar() or 0)
                return {
                    "total_count": total,
                    "target_status_counts": {
                        "monitoring_high": 0,
                        "monitoring_med": 0,
                        "monitoring_low": total,
                        "negotiation": 0,
                        "due_diligence": 0,
                        "ownership": 0,
                    },
                }
            raise


def list_distinct_minerals() -> List[str]:
    """Return sorted list of distinct mineral names that appear in any target (for autocomplete)."""
    eng = get_engine()
    with eng.begin() as conn:
        rows = conn.execute(
            text("""
            SELECT DISTINCT TRIM(unnest(minerals)) AS name
            FROM areas_of_focus
            WHERE minerals IS NOT NULL AND array_length(minerals, 1) > 0
            ORDER BY 1
            """),
        ).fetchall()
    return [r[0] for r in rows if r[0]]


def get_area(id: int) -> dict | None:
    eng = get_engine()
    with eng.begin() as conn:
        try:
            row = conn.execute(
                text("""
                SELECT id, name, location_plss, location_coords, latitude, longitude,
                       minerals, status, status_checked_at, report_links, report_summary,
                       validity_notes, source, external_id, blm_case_url, blm_serial_number,
                       priority, roi_score, characteristics, state_abbr, meridian,
                       township, "range", section, COALESCE(is_uploaded, false) AS is_uploaded,
                       plss_normalized, claim_type, COALESCE(retrieval_type, 'User Added') AS retrieval_type, created_at, updated_at
                FROM areas_of_focus WHERE id = :id
                """),
                {"id": id},
            ).mappings().first()
        except Exception as e:
            err = str(e).lower()
            if "township" in err or "range" in err or "section" in err or "is_uploaded" in err:
                row = conn.execute(
                    text("""
                    SELECT id, name, location_plss, location_coords, latitude, longitude,
                           minerals, status, status_checked_at, report_links, report_summary,
                           validity_notes, source, external_id, blm_case_url, blm_serial_number,
                           priority, roi_score, characteristics, state_abbr, meridian,
                           plss_normalized, claim_type, COALESCE(retrieval_type, 'User Added') AS retrieval_type, created_at, updated_at
                    FROM areas_of_focus WHERE id = :id
                    """),
                    {"id": id},
                ).mappings().first()
                if row:
                    row = dict(row)
                    row["township"] = None
                    row["range"] = None
                    row["section"] = None
                    row["is_uploaded"] = False
                    if row.get("characteristics") is None:
                        row["characteristics"] = {}
                    row.setdefault("plss_normalized", None)
                    row.setdefault("claim_type", None)
                    row.setdefault("retrieval_type", RETRIEVAL_TYPE_USER_ADDED)
                    return row
                return None
            if "priority" in err and "column" in err:
                row = conn.execute(
                    text("""
                    SELECT id, name, location_plss, location_coords, latitude, longitude,
                           minerals, status, status_checked_at, report_links, report_summary,
                           validity_notes, source, external_id, blm_case_url, blm_serial_number,
                           roi_score, created_at, updated_at
                    FROM areas_of_focus WHERE id = :id
                    """),
                    {"id": id},
                ).mappings().first()
                if row:
                    row = dict(row)
                    row["priority"] = "monitoring_low"
                    row["characteristics"] = row.get("characteristics") or {}
                    row.setdefault("township", None)
                    row.setdefault("range", None)
                    row.setdefault("section", None)
                    row.setdefault("is_uploaded", False)
                    row.setdefault("plss_normalized", None)
                    row.setdefault("claim_type", None)
                    row.setdefault("retrieval_type", RETRIEVAL_TYPE_USER_ADDED)
                    return row
                return None
            if "characteristics" in err and "column" in err:
                row = conn.execute(
                    text("""
                    SELECT id, name, location_plss, location_coords, latitude, longitude,
                           minerals, status, status_checked_at, report_links, report_summary,
                           validity_notes, source, external_id, blm_case_url, blm_serial_number,
                           priority, roi_score, created_at, updated_at
                    FROM areas_of_focus WHERE id = :id
                    """),
                    {"id": id},
                ).mappings().first()
                if row:
                    row = dict(row)
                    row["characteristics"] = {}
                    row.setdefault("township", None)
                    row.setdefault("range", None)
                    row.setdefault("section", None)
                    row.setdefault("is_uploaded", False)
                    row.setdefault("plss_normalized", None)
                    row.setdefault("claim_type", None)
                    row.setdefault("retrieval_type", RETRIEVAL_TYPE_USER_ADDED)
                    return row
                return None
            raise
    if row:
        row = dict(row)
        if row.get("characteristics") is None:
            row["characteristics"] = {}
        row.setdefault("plss_normalized", None)
        row.setdefault("claim_type", None)
        row.setdefault("retrieval_type", RETRIEVAL_TYPE_USER_ADDED)
        _format_area_display(row)
    return row


def merge_area_characteristics(area_id: int, updates: Dict[str, Any]) -> bool:
    """Merge updates into areas_of_focus.characteristics (JSONB). Returns True if updated."""
    eng = get_engine()
    with eng.begin() as conn:
        try:
            r = conn.execute(
                text("""
                UPDATE areas_of_focus
                SET characteristics = COALESCE(characteristics, '{}'::jsonb) || CAST(:updates AS jsonb),
                    updated_at = now()
                WHERE id = :id
                """),
                {"id": area_id, "updates": json.dumps(updates)},
            )
            return r.rowcount > 0
        except Exception as e:
            if "column" in str(e).lower() and "characteristics" in str(e).lower():
                return False
            raise


# Keys the UI may delete via explicit "clear snapshot" actions (never arbitrary paths).
_REMOVABLE_CHARACTERISTIC_KEYS = frozenset({"claim_records", "lr2000_geographic_index"})


def remove_area_characteristic_keys(area_id: int, keys: list[str]) -> bool:
    """
    Remove one or more top-level keys from ``characteristics`` using PostgreSQL jsonb ``-``.
    Only whitelisted keys are applied. Returns True if a row was updated.
    """
    safe = [k for k in keys if k in _REMOVABLE_CHARACTERISTIC_KEYS]
    if not safe:
        return False
    expr = "COALESCE(characteristics, '{}'::jsonb)"
    for k in safe:
        expr = f"({expr} - '{k}')"
    eng = get_engine()
    with eng.begin() as conn:
        r = conn.execute(
            text(f"""
            UPDATE areas_of_focus
            SET characteristics = {expr},
                updated_at = now()
            WHERE id = :id
            """),
            {"id": area_id},
        )
        return r.rowcount > 0


VALID_TARGET_STATUSES = (
    "monitoring_low", "monitoring_med", "monitoring_high",
    "negotiation", "due_diligence", "ownership",
    "low", "medium", "high",  # legacy values still accepted
)


def _normalize_target_status(val: str | None) -> str:
    """Map legacy priority values and normalize target status."""
    if not val:
        return "monitoring_low"
    v = val.strip().lower()
    legacy_map = {"low": "monitoring_low", "medium": "monitoring_med", "high": "monitoring_high"}
    return legacy_map.get(v, v) if v in legacy_map or v in VALID_TARGET_STATUSES else "monitoring_low"


def update_area_priority(id: int, priority: str) -> bool:
    """Set target status (stored in priority column). Returns True if row was updated."""
    normalized = _normalize_target_status(priority)
    if normalized not in VALID_TARGET_STATUSES:
        return False
    eng = get_engine()
    with eng.begin() as conn:
        r = conn.execute(
            text("UPDATE areas_of_focus SET priority = :priority, updated_at = now() WHERE id = :id"),
            {"id": id, "priority": normalized},
        )
    return r.rowcount > 0


def update_area_notes(id: int, notes: str | None) -> bool:
    """Update the validity_notes (Notes) field on a target."""
    eng = get_engine()
    with eng.begin() as conn:
        r = conn.execute(
            text("UPDATE areas_of_focus SET validity_notes = :notes, updated_at = now() WHERE id = :id"),
            {"id": id, "notes": notes.strip() if notes else None},
        )
    return r.rowcount > 0


def update_area_claim_type(id: int, claim_type: str | None) -> bool:
    """Set the claim_type field on a target. Returns True if row was updated."""
    eng = get_engine()
    with eng.begin() as conn:
        r = conn.execute(
            text("UPDATE areas_of_focus SET claim_type = :claim_type, updated_at = now() WHERE id = :id"),
            {"id": id, "claim_type": claim_type.strip() if claim_type else None},
        )
    return r.rowcount > 0


def update_area_minerals(id: int, minerals: List[str] | None) -> bool:
    """Set the minerals field on a target. Returns True if row was updated."""
    cleaned = _normalize_minerals(minerals or [])
    eng = get_engine()
    with eng.begin() as conn:
        r = conn.execute(
            text("UPDATE areas_of_focus SET minerals = :minerals, updated_at = now() WHERE id = :id"),
            {"id": id, "minerals": cleaned},
        )
    return r.rowcount > 0


def update_area_coordinates(area_id: int, latitude: float | None, longitude: float | None) -> bool:
    """Set WGS84 latitude/longitude on a target. Returns True if a row was updated."""
    eng = get_engine()
    with eng.begin() as conn:
        r = conn.execute(
            text("""
            UPDATE areas_of_focus
            SET latitude = :lat, longitude = :lon, updated_at = now()
            WHERE id = :id
            """),
            {"id": area_id, "lat": latitude, "lon": longitude},
        )
    return (r.rowcount or 0) > 0


def update_area_name(area_id: int, name: str | None) -> bool:
    """
    Rename a target. Returns True when a row was updated. An empty or
    whitespace-only name is rejected (targets must always have a name).
    """
    nm = (name or "").strip()
    if not nm:
        return False
    eng = get_engine()
    with eng.begin() as conn:
        r = conn.execute(
            text("UPDATE areas_of_focus SET name = :name, updated_at = now() WHERE id = :id"),
            {"id": area_id, "name": nm[:500]},
        )
    return (r.rowcount or 0) > 0


def update_area_plss(
    area_id: int,
    location_plss: str | None,
    *,
    regeocode_coordinates: bool = True,
) -> dict[str, Any]:
    """
    User-initiated edit of a target's PLSS location.

    Parses ``location_plss`` into state/township/range/section/meridian and
    overwrites those columns (unlike ``apply_plss_lookup_result`` which
    preserves existing values via COALESCE — that's the right call for AI /
    reverse-geocode paths but wrong for a human edit where clearing a
    section is a deliberate action).

    When ``regeocode_coordinates`` is True (default) and the PLSS parses
    cleanly, we also re-derive lat/lon from the new PLSS via BLM Cadastral
    and overwrite them. This is the fix for "target has wrong PLSS and
    wrong coords" scenarios (e.g. Spor Mountain) — the user corrects the
    PLSS and the coords follow automatically.

    Passing an empty/None ``location_plss`` clears the PLSS and all parsed
    components (coordinates are preserved).

    Returns a dict::

        {
          "ok": bool,
          "error": str | None,
          "location_plss": str | None,
          "state_abbr": str | None,
          "township": str | None,
          "range": str | None,
          "section": str | None,
          "meridian": str | None,
          "latitude": float | None,
          "longitude": float | None,
          "regeocoded": bool,
          # on duplicate_plss error:
          "conflicting_id": int | None,
          "conflicting_name": str | None,
        }
    """
    area = get_area(area_id)
    if not area:
        return {"ok": False, "error": "not_found"}

    lp = (location_plss or "").strip()

    if not lp:
        eng = get_engine()
        with eng.begin() as conn:
            r = conn.execute(
                text("""
                UPDATE areas_of_focus SET
                  location_plss = NULL,
                  plss_normalized = NULL,
                  state_abbr = NULL,
                  township = NULL,
                  "range" = NULL,
                  section = NULL,
                  meridian = NULL,
                  updated_at = now()
                WHERE id = :id
                """),
                {"id": area_id},
            )
            if (r.rowcount or 0) == 0:
                return {"ok": False, "error": "not_found"}
        return {
            "ok": True,
            "location_plss": None,
            "state_abbr": None,
            "township": None,
            "range": None,
            "section": None,
            "meridian": None,
            "latitude": area.get("latitude"),
            "longitude": area.get("longitude"),
            "regeocoded": False,
        }

    comp = _parse_plss_to_components(lp, default_state=(area.get("state_abbr") or "UT"))
    if not comp:
        return {
            "ok": False,
            "error": "unparseable_plss",
            "location_plss": lp,
        }

    st = comp.get("state_abbr")
    twp = comp.get("township")
    rng = comp.get("range")
    sec = comp.get("section")
    mer = comp.get("meridian")

    plss_key = _normalize_plss(lp, default_state=st or "UT")
    if not plss_key:
        plss_key = re.sub(r"\s+", " ", lp).upper().strip()

    eng = get_engine()
    new_lat: float | None = area.get("latitude")
    new_lon: float | None = area.get("longitude")
    geocoded = False

    if regeocode_coordinates:
        try:
            from mining_os.services.plss_geocode import geocode_plss
            geo = geocode_plss(state=st, township=twp, range_val=rng, section=sec, meridian=None)
            if geo:
                new_lat = geo["latitude"]
                new_lon = geo["longitude"]
                geocoded = True
        except Exception as e:
            log.warning("update_area_plss: geocode_plss failed for area %s: %s", area_id, e)

    with eng.begin() as conn:
        conflict = conn.execute(
            text("SELECT id, name FROM areas_of_focus WHERE plss_normalized = :k AND id <> :id LIMIT 1"),
            {"k": plss_key, "id": area_id},
        ).mappings().first()
        if conflict:
            return {
                "ok": False,
                "error": "duplicate_plss",
                "conflicting_id": int(conflict["id"]),
                "conflicting_name": (conflict.get("name") or "").strip() or f"#{conflict['id']}",
                "location_plss": lp,
            }

        r = conn.execute(
            text("""
            UPDATE areas_of_focus SET
              location_plss = :location_plss,
              plss_normalized = :plss_normalized,
              state_abbr = :state_abbr,
              township = :township,
              "range" = :range_val,
              section = :section,
              meridian = :meridian,
              latitude = :lat,
              longitude = :lon,
              updated_at = now()
            WHERE id = :id
            """),
            {
                "id": area_id,
                "location_plss": lp,
                "plss_normalized": plss_key,
                "state_abbr": st,
                "township": twp,
                "range_val": rng,
                "section": sec,
                "meridian": mer,
                "lat": new_lat,
                "lon": new_lon,
            },
        )
        if (r.rowcount or 0) == 0:
            return {"ok": False, "error": "not_found"}

    return {
        "ok": True,
        "location_plss": lp,
        "state_abbr": st,
        "township": twp,
        "range": rng,
        "section": sec,
        "meridian": mer,
        "latitude": new_lat,
        "longitude": new_lon,
        "regeocoded": geocoded,
    }


def update_area_plss_components(
    area_id: int,
    *,
    state_abbr: str | None,
    township: str | None,
    range_val: str | None,
    section: str | None,
    meridian: str | None = None,
    regeocode_coordinates: bool = True,
) -> dict[str, Any]:
    """
    User-initiated edit of PLSS by *individual component* (Township, Range,
    Section, State, optional Meridian) rather than a raw PLSS string.

    Each input is run through :func:`blm_plss.normalize_plss_field` which
    tolerates messy user input (``T12S``, ``12S``, ``Township 12 South``,
    ``t.12.s``, ``0120S`` — all become ``0120S``). State + Township + Range
    are required; Section is optional but recommended. Meridian defaults
    from the state when blank.

    The canonical ``location_plss`` string is rebuilt from the normalized
    components as ``"<STATE> <T##D> <R##D> Sec <###>"`` (e.g.
    ``"UT T12S R12W Sec 035"``) so the rest of the system (including the
    Fetch Claim Records pipeline) sees a clean, parseable value.

    Behaviour otherwise mirrors :func:`update_area_plss`: duplicate-section
    guard, coords re-geocoded from the new PLSS when ``regeocode_coordinates``
    is true, and the same response shape on success/failure.
    """
    from mining_os.services.blm_plss import normalize_plss_field

    area = get_area(area_id)
    if not area:
        return {"ok": False, "error": "not_found"}

    from mining_os.services.fetch_claim_records import STATE_MERIDIAN, DEFAULT_MERIDIAN

    st = normalize_plss_field(state_abbr, "state") or (area.get("state_abbr") or "UT")
    twp = normalize_plss_field(township, "township")
    rng = normalize_plss_field(range_val, "range")
    sec = normalize_plss_field(section, "section") if section not in (None, "") else None
    mer = normalize_plss_field(meridian, "meridian") if meridian not in (None, "") else None
    if not mer:
        mer = STATE_MERIDIAN.get(st, DEFAULT_MERIDIAN)

    missing = [k for k, v in (("township", twp), ("range", rng)) if not v]
    if missing:
        return {
            "ok": False,
            "error": "invalid_components",
            "detail": (
                f"Could not parse {', '.join(missing)}. "
                "Township must look like '12S' or 'T12S'; Range like '12W' or 'R12W'."
            ),
        }

    def _compact_tr(encoded: str) -> str:
        m = re.match(r"^0*(\d+)([NSEW])$", encoded)
        return f"{m.group(1)}{m.group(2)}" if m else encoded

    parts = [st, f"T{_compact_tr(twp)}", f"R{_compact_tr(rng)}"]
    if sec:
        parts.append(f"Sec {sec}")
    lp = " ".join(parts)
    plss_key = _normalize_plss(lp, default_state=st) or re.sub(r"\s+", " ", lp).upper().strip()

    new_lat: float | None = area.get("latitude")
    new_lon: float | None = area.get("longitude")
    geocoded = False
    if regeocode_coordinates:
        try:
            from mining_os.services.plss_geocode import geocode_plss
            geo = geocode_plss(state=st, township=twp, range_val=rng, section=sec, meridian=None)
            if geo:
                new_lat = geo["latitude"]
                new_lon = geo["longitude"]
                geocoded = True
        except Exception as e:
            log.warning("update_area_plss_components: geocode_plss failed for area %s: %s", area_id, e)

    eng = get_engine()
    with eng.begin() as conn:
        conflict = conn.execute(
            text("SELECT id, name FROM areas_of_focus WHERE plss_normalized = :k AND id <> :id LIMIT 1"),
            {"k": plss_key, "id": area_id},
        ).mappings().first()
        if conflict:
            return {
                "ok": False,
                "error": "duplicate_plss",
                "conflicting_id": int(conflict["id"]),
                "conflicting_name": (conflict.get("name") or "").strip() or f"#{conflict['id']}",
                "location_plss": lp,
            }

        r = conn.execute(
            text("""
            UPDATE areas_of_focus SET
              location_plss = :location_plss,
              plss_normalized = :plss_normalized,
              state_abbr = :state_abbr,
              township = :township,
              "range" = :range_val,
              section = :section,
              meridian = :meridian,
              latitude = :lat,
              longitude = :lon,
              updated_at = now()
            WHERE id = :id
            """),
            {
                "id": area_id,
                "location_plss": lp,
                "plss_normalized": plss_key,
                "state_abbr": st,
                "township": twp,
                "range_val": rng,
                "section": sec,
                "meridian": mer,
                "lat": new_lat,
                "lon": new_lon,
            },
        )
        if (r.rowcount or 0) == 0:
            return {"ok": False, "error": "not_found"}

    return {
        "ok": True,
        "location_plss": lp,
        "state_abbr": st,
        "township": twp,
        "range": rng,
        "section": sec,
        "meridian": mer,
        "latitude": new_lat,
        "longitude": new_lon,
        "regeocoded": geocoded,
    }


def update_area_status(id: int, status: str, blm_serial_number: str | None = None, blm_case_url: str | None = None) -> bool:
    eng = get_engine()
    with eng.begin() as conn:
        r = conn.execute(
            text("""
            UPDATE areas_of_focus
            SET status = :status, status_checked_at = now(), updated_at = now(),
                blm_serial_number = COALESCE(:blm_serial_number, blm_serial_number),
                blm_case_url = COALESCE(:blm_case_url, blm_case_url)
            WHERE id = :id
            """),
            {"id": id, "status": status, "blm_serial_number": blm_serial_number or None, "blm_case_url": blm_case_url or None},
        )
    return r.rowcount > 0


def county_from_validity_notes(notes: str | None) -> str:
    """Extract ``County: Name`` from validity_notes (batch imports often store county this way)."""
    if not notes:
        return ""
    m = re.search(r"County:\s*([^.\n]+)", notes, re.I)
    return (m.group(1).strip() if m else "").strip()


class ApplyPlssLookupResult(NamedTuple):
    """Result of apply_plss_lookup_result."""

    applied: bool
    reason: str | None = None
    conflicting_id: int | None = None
    conflicting_name: str | None = None


def plss_lookup_would_conflict(
    area_id: int,
    *,
    location_plss: str,
    state_abbr: str | None,
    township: str | None = None,
    range_val: str | None = None,
    section: str | None = None,
) -> dict[str, Any]:
    """
    Read-only: check whether saving this PLSS on area_id would hit duplicate plss_normalized.
    Does not read or write validity_notes. Used for preview-before-apply PLSS AI flow.
    """
    lp = (location_plss or "").strip()
    if not lp:
        return {"ok": False, "reason": "empty", "plss_key": None, "conflicting_id": None, "conflicting_name": None}
    st = (state_abbr or "").strip().upper()[:2] or None
    comp = _parse_plss_to_components(lp, default_state=st or "UT")
    if comp:
        if not st:
            st = comp.get("state_abbr")
        township = township or comp.get("township")
        range_val = range_val or comp.get("range")
        section = section or comp.get("section")
    plss_key = _normalize_plss(lp, default_state=st or "UT")
    if not plss_key:
        plss_key = re.sub(r"\s+", " ", lp).upper().strip()
    eng = get_engine()
    with eng.begin() as conn:
        row = conn.execute(
            text("SELECT id FROM areas_of_focus WHERE id = :id"),
            {"id": area_id},
        ).mappings().first()
        if not row:
            return {"ok": False, "reason": "not_found", "plss_key": plss_key, "conflicting_id": None, "conflicting_name": None}
        conflict = conn.execute(
            text(
                "SELECT id, name FROM areas_of_focus WHERE plss_normalized = :k AND id <> :id LIMIT 1"
            ),
            {"k": plss_key, "id": area_id},
        ).mappings().first()
        if conflict:
            return {
                "ok": False,
                "reason": "duplicate_plss",
                "plss_key": plss_key,
                "conflicting_id": int(conflict["id"]),
                "conflicting_name": (conflict.get("name") or "").strip() or f"#{conflict['id']}",
            }
    return {"ok": True, "reason": None, "plss_key": plss_key, "conflicting_id": None, "conflicting_name": None}


def apply_plss_lookup_result(
    area_id: int,
    *,
    location_plss: str,
    state_abbr: str | None,
    township: str | None,
    range_val: str | None,
    section: str | None,
    latitude: float | None,
    longitude: float | None,
    notes_append: str | None,
    meridian: str | None = None,
) -> ApplyPlssLookupResult:
    """Persist AI-inferred or reverse-geocoded PLSS (and optional lat/long) on a target."""
    lp = (location_plss or "").strip()
    if not lp:
        return ApplyPlssLookupResult(False, "empty")
    st = (state_abbr or "").strip().upper()[:2] or None
    comp = _parse_plss_to_components(lp, default_state=st or "UT")
    if comp:
        if not st:
            st = comp.get("state_abbr")
        township = township or comp.get("township")
        range_val = range_val or comp.get("range")
        section = section or comp.get("section")
    plss_key = _normalize_plss(lp, default_state=st or "UT")
    if not plss_key:
        plss_key = re.sub(r"\s+", " ", lp).upper().strip()

    eng = get_engine()
    with eng.begin() as conn:
        row = conn.execute(
            text("SELECT validity_notes FROM areas_of_focus WHERE id = :id"),
            {"id": area_id},
        ).mappings().first()
        if not row:
            return ApplyPlssLookupResult(False, "not_found")
        old_notes = row.get("validity_notes") or ""
        ai_block = (old_notes + "\n\n" + notes_append).strip() if notes_append else old_notes

        mer = (meridian or "").strip() or None
        params = {
            "id": area_id,
            "location_plss": lp,
            "plss_normalized": plss_key,
            "state_abbr": st,
            "township": township,
            "range_val": range_val,
            "section": section,
            "meridian": mer,
            "lat": latitude,
            "lon": longitude,
            "validity_notes": ai_block,
        }
        conflict = conn.execute(
            text(
                "SELECT id, name FROM areas_of_focus WHERE plss_normalized = :k AND id <> :id LIMIT 1"
            ),
            {"k": plss_key, "id": area_id},
        ).mappings().first()
        def persist_duplicate_notes(other_id: int, other_name: str) -> ApplyPlssLookupResult:
            skip_note = (
                f"\n\n[Skipped PLSS — duplicate location] Inferred PLSS ({lp} → {plss_key}) "
                f"is already assigned to target \"{other_name}\" (id {other_id})."
            )
            new_notes = (ai_block + skip_note).strip()
            conn.execute(
                text(
                    "UPDATE areas_of_focus SET validity_notes = :validity_notes, updated_at = now() WHERE id = :id"
                ),
                {"id": area_id, "validity_notes": new_notes},
            )
            return ApplyPlssLookupResult(False, "duplicate_plss", other_id, other_name)

        if conflict:
            return persist_duplicate_notes(
                int(conflict["id"]),
                (conflict.get("name") or "").strip() or f"#{conflict['id']}",
            )

        rc = 0
        dup_race = False
        prog_fallback = False
        with conn.begin_nested():
            try:
                r = conn.execute(
                    text("""
                    UPDATE areas_of_focus SET
                      location_plss = :location_plss,
                      plss_normalized = :plss_normalized,
                      state_abbr = COALESCE(:state_abbr, state_abbr),
                      township = COALESCE(:township, township),
                      "range" = COALESCE(:range_val, "range"),
                      section = COALESCE(:section, section),
                      meridian = COALESCE(:meridian, meridian),
                      latitude = COALESCE(:lat, latitude),
                      longitude = COALESCE(:lon, longitude),
                      validity_notes = :validity_notes,
                      updated_at = now()
                    WHERE id = :id
                    """),
                    params,
                )
                rc = r.rowcount or 0
            except IntegrityError:
                dup_race = True
            except ProgrammingError as pe:
                msg = str(pe).lower()
                if "township" in msg or "plss_normalized" in msg:
                    prog_fallback = True
                else:
                    raise
        if dup_race:
            conf2 = conn.execute(
                text(
                    "SELECT id, name FROM areas_of_focus WHERE plss_normalized = :k AND id <> :id LIMIT 1"
                ),
                {"k": plss_key, "id": area_id},
            ).mappings().first()
            if conf2:
                return persist_duplicate_notes(
                    int(conf2["id"]),
                    (conf2.get("name") or "").strip() or f"#{conf2['id']}",
                )
            return ApplyPlssLookupResult(False, "no_update")
        if prog_fallback:
            r2 = conn.execute(
                text("""
                UPDATE areas_of_focus SET
                  location_plss = :location_plss,
                  validity_notes = :validity_notes,
                  state_abbr = COALESCE(:state_abbr, state_abbr),
                  meridian = COALESCE(:meridian, meridian),
                  latitude = COALESCE(:lat, latitude),
                  longitude = COALESCE(:lon, longitude),
                  updated_at = now()
                WHERE id = :id
                """),
                {
                    "id": area_id,
                    "location_plss": lp,
                    "validity_notes": ai_block,
                    "state_abbr": st,
                    "meridian": mer,
                    "lat": latitude,
                    "lon": longitude,
                },
            )
            rc = r2.rowcount or 0
        return ApplyPlssLookupResult(True) if rc > 0 else ApplyPlssLookupResult(False, "no_update")


def reverse_plss_from_coordinates_for_area(area_id: int) -> dict[str, Any]:
    """Resolve PLSS from stored lat/lon via BLM Cadastral and persist. Preserves existing coordinates."""
    area = get_area(area_id)
    if not area:
        return {"ok": False, "error": "not_found"}
    lat, lon = area.get("latitude"), area.get("longitude")
    if lat is None or lon is None:
        return {"ok": False, "error": "missing_coordinates"}
    try:
        latf = float(lat)
        lonf = float(lon)
    except (TypeError, ValueError):
        return {"ok": False, "error": "invalid_coordinates"}
    if not math.isfinite(latf) or not math.isfinite(lonf):
        return {"ok": False, "error": "invalid_coordinates"}
    pn = (area.get("plss_normalized") or "").strip()
    if pn:
        return {"ok": False, "error": "already_has_plss", "plss_normalized": pn}

    from mining_os.services.plss_geocode import reverse_geocode_plss

    rev = reverse_geocode_plss(latf, lonf)
    if not rev:
        return {"ok": False, "error": "no_plss_feature"}

    applied = apply_plss_lookup_result(
        area_id,
        location_plss=rev["location_plss"],
        state_abbr=rev.get("state_abbr"),
        township=rev.get("township"),
        range_val=rev.get("range"),
        section=rev.get("section"),
        latitude=None,
        longitude=None,
        notes_append=None,
        meridian=rev.get("meridian"),
    )
    if applied.applied:
        return {"ok": True, "location_plss": rev["location_plss"], "plssid": rev.get("plssid")}
    if applied.reason == "duplicate_plss":
        return {
            "ok": False,
            "error": "duplicate_plss",
            "conflicting_id": applied.conflicting_id,
            "conflicting_name": applied.conflicting_name,
        }
    return {"ok": False, "error": applied.reason or "not_applied"}


def batch_reverse_plss_from_coordinates() -> dict[str, Any]:
    """For all targets with coordinates and no plss_normalized, resolve PLSS from BLM."""
    eng = get_engine()
    with eng.begin() as conn:
        rows = conn.execute(
            text("""
            SELECT id FROM areas_of_focus
            WHERE latitude IS NOT NULL AND longitude IS NOT NULL
              AND (plss_normalized IS NULL OR TRIM(COALESCE(plss_normalized, '')) = '')
            ORDER BY id
            """),
        ).fetchall()

    results: List[dict[str, Any]] = []
    updated = 0
    for tup in rows:
        aid = int(tup[0])
        r = reverse_plss_from_coordinates_for_area(aid)
        if r.get("ok"):
            updated += 1
        results.append({"id": aid, **r})
    return {"updated": updated, "total": len(rows), "results": results}


def update_area_state_meridian(area_id: int, state_abbr: str, meridian: str) -> bool:
    """Set state_abbr and meridian on a target. Returns True if updated."""
    eng = get_engine()
    with eng.begin() as conn:
        try:
            r = conn.execute(
                text("""
                UPDATE areas_of_focus
                SET state_abbr = :state_abbr, meridian = :meridian, updated_at = now()
                WHERE id = :id
                """),
                {"id": area_id, "state_abbr": state_abbr, "meridian": meridian},
            )
            return r.rowcount > 0
        except Exception as e:
            if "meridian" in str(e).lower() and "column" in str(e).lower():
                r = conn.execute(
                    text("UPDATE areas_of_focus SET state_abbr = :state_abbr, updated_at = now() WHERE id = :id"),
                    {"id": area_id, "state_abbr": state_abbr},
                )
                return r.rowcount > 0
            raise


def delete_areas_by_source(source: str) -> int:
    """Delete all areas with the given source. Returns number of rows deleted."""
    eng = get_engine()
    with eng.begin() as conn:
        r = conn.execute(text("DELETE FROM areas_of_focus WHERE source = :source"), {"source": source})
    return r.rowcount


def delete_area(area_id: int) -> bool:
    """Delete one target by id. Returns True if a row was deleted."""
    eng = get_engine()
    with eng.begin() as conn:
        r = conn.execute(text("DELETE FROM areas_of_focus WHERE id = :id"), {"id": area_id})
    return r.rowcount > 0


def get_clean_preview() -> Dict[str, Any]:
    """
    Find targets with no PLSS and duplicate groups by PLSS.
    Returns { no_plss: [area, ...], duplicates: [ { plss, plss_normalized, targets: [area, ...] }, ... ] }.
    """
    no_plss: List[dict] = []
    duplicates: List[Dict[str, Any]] = []
    try:
        eng = get_engine()
        cols = (
            "id, name, location_plss, plss_normalized, minerals, report_links, status, priority, "
            "state_abbr, validity_notes, latitude, longitude"
        )
        # No PLSS: no plss_normalized (includes missing or empty location_plss)
        with eng.begin() as conn:
            try:
                rows = conn.execute(
                    text(f"""
                    SELECT {cols}
                    FROM areas_of_focus
                    WHERE (plss_normalized IS NULL OR TRIM(COALESCE(plss_normalized, '')) = '')
                    ORDER BY name
                    """),
                ).mappings().all()
                no_plss = [dict(r) for r in rows]
            except Exception as e:
                err = str(e).lower()
                if "column" in err and "priority" in err:
                    rows = conn.execute(
                        text("""
                        SELECT id, name, location_plss, plss_normalized, minerals, report_links, status,
                               state_abbr, validity_notes, latitude, longitude
                        FROM areas_of_focus
                        WHERE (plss_normalized IS NULL OR TRIM(COALESCE(plss_normalized, '')) = '')
                        ORDER BY name
                        """),
                    ).mappings().all()
                    no_plss = [dict(r) for r in rows]
                    for a in no_plss:
                        a["priority"] = "monitoring_low"
                elif "plss_normalized" in err or "column" in err:
                    # Schema may not have plss_normalized: fallback to location_plss only
                    try:
                        rows = conn.execute(
                            text("""
                            SELECT id, name, location_plss, minerals, report_links, status,
                                   state_abbr, validity_notes
                            FROM areas_of_focus
                            WHERE (location_plss IS NULL OR TRIM(COALESCE(location_plss, '')) = '')
                            ORDER BY name
                            """),
                        ).mappings().all()
                        no_plss = [dict(r) for r in rows]
                        for a in no_plss:
                            a["plss_normalized"] = None
                            a["priority"] = a.get("priority") or "monitoring_low"
                    except Exception:
                        pass
                else:
                    raise

            # Duplicates: plss_normalized with count > 1
            dup_plss: List[tuple] = []
            try:
                dup_plss = conn.execute(
                    text("""
                    SELECT plss_normalized
                    FROM areas_of_focus
                    WHERE plss_normalized IS NOT NULL AND TRIM(plss_normalized) != ''
                    GROUP BY plss_normalized
                    HAVING COUNT(*) > 1
                    """),
                ).fetchall()
            except Exception:
                pass

        for row in dup_plss:
            plss_norm = row[0] if isinstance(row, (tuple, list)) else row
            with eng.begin() as conn:
                try:
                    rows = conn.execute(
                        text(f"""
                        SELECT {cols}
                        FROM areas_of_focus
                        WHERE plss_normalized = :key
                        ORDER BY id
                        """),
                        {"key": plss_norm},
                    ).mappings().all()
                except Exception:
                    rows = conn.execute(
                        text("""
                        SELECT id, name, location_plss, plss_normalized, minerals, report_links, status,
                               state_abbr, validity_notes
                        FROM areas_of_focus
                        WHERE plss_normalized = :key
                        ORDER BY id
                        """),
                        {"key": plss_norm},
                    ).mappings().all()
                targets = [dict(r) for r in rows]
                for t in targets:
                    t.setdefault("priority", "monitoring_low")
            duplicates.append({
                "plss": plss_norm,
                "plss_normalized": plss_norm,
                "targets": targets,
            })
    except Exception as e:
        log.exception("get_clean_preview failed: %s", e)
        no_plss = []
        duplicates = []

    for a in no_plss:
        a["county"] = county_from_validity_notes(a.get("validity_notes"))
    for g in duplicates:
        for t in g.get("targets") or []:
            t["county"] = county_from_validity_notes(t.get("validity_notes"))

    return {"no_plss": no_plss, "duplicates": duplicates}


def consolidate_duplicates(keep_id: int, merge_ids: List[int]) -> Dict[str, Any]:
    """
    Merge merge_ids into the target keep_id (combine minerals, report_links), then delete merge_ids.
    Returns { kept: keep_id, deleted: merge_ids, error?: str }.
    """
    if keep_id in merge_ids:
        merge_ids = [i for i in merge_ids if i != keep_id]
    if not merge_ids:
        return {"kept": keep_id, "deleted": []}
    eng = get_engine()
    keep = get_area(keep_id)
    if not keep:
        return {"kept": keep_id, "deleted": [], "error": "Target to keep not found"}
    all_ids = [keep_id] + merge_ids
    merged_minerals: List[str] = list(keep.get("minerals") or [])
    merged_links: List[str] = list(keep.get("report_links") or [])
    with eng.begin() as conn:
        for mid in merge_ids:
            row = conn.execute(
                text("SELECT minerals, report_links FROM areas_of_focus WHERE id = :id"),
                {"id": mid},
            ).mappings().first()
            if row:
                for m in row.get("minerals") or []:
                    if m and m not in merged_minerals:
                        merged_minerals.append(m)
                for r in row.get("report_links") or []:
                    if r and r not in merged_links:
                        merged_links.append(r)
        conn.execute(
            text("UPDATE areas_of_focus SET minerals = :minerals, report_links = :report_links, updated_at = now() WHERE id = :kid"),
            {"minerals": merged_minerals, "report_links": merged_links, "kid": keep_id},
        )
        for mid in merge_ids:
            conn.execute(text("DELETE FROM areas_of_focus WHERE id = :id"), {"id": mid})
    return {"kept": keep_id, "deleted": merge_ids}


def upsert_area(
    name: str,
    location_plss: str | None = None,
    location_coords: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    minerals: List[str] | None = None,
    status: str | None = None,
    report_links: List[str] | None = None,
    report_summary: str | None = None,
    validity_notes: str | None = None,
    source: str | None = None,
    external_id: str | None = None,
    blm_case_url: str | None = None,
    blm_serial_number: str | None = None,
    roi_score: int | None = None,
    priority: str | None = None,
    state_abbr: str | None = None,
    township: str | None = None,
    range_val: str | None = None,
    section: str | None = None,
    meridian: str | None = None,
    is_uploaded: bool | None = None,
    retrieval_type: str | None = None,
    skip_plss_geocode: bool = False,
) -> int:
    """Insert or update by PLSS (Target). Parses location_plss into state, township, range, section; sets is_uploaded when provided."""
    eng = get_engine()
    minerals = _normalize_minerals(",".join(minerals)) if minerals else []
    report_links = report_links or []
    plss_norm = _normalize_plss(location_plss, default_state=state_abbr)
    name_trim = (name[:500] if len(name) > 500 else name) if name else "Unknown"

    # Parse PLSS into components when we have location_plss and any component is missing
    if location_plss and (state_abbr is None or township is None or range_val is None or section is None):
        comp = _parse_plss_to_components(location_plss, default_state=state_abbr or "UT")
        if comp:
            if state_abbr is None:
                state_abbr = comp["state_abbr"]
            if township is None:
                township = comp["township"]
            if range_val is None:
                range_val = comp["range"]
            if section is None:
                section = comp.get("section")
            if meridian is None:
                meridian = comp.get("meridian")

    if not state_abbr or not township or not range_val or not section:
        log.warning(
            "upsert_area: target %r missing required PLSS fields (state=%s twp=%s rng=%s sec=%s)",
            name_trim, state_abbr, township, range_val, section,
        )

    if (
        not skip_plss_geocode
        and (latitude is None or longitude is None)
        and township
        and range_val
    ):
        try:
            from mining_os.services.plss_geocode import geocode_plss
            geo = geocode_plss(state_abbr or "UT", township, range_val, section)
            if geo:
                latitude = geo["latitude"]
                longitude = geo["longitude"]
                log.info("Auto-geocoded %r -> %.6f, %.6f", name_trim, latitude, longitude)
        except Exception:
            log.debug("Auto-geocode failed for %r, skipping", name_trim, exc_info=True)

    retrieval_type = _normalize_retrieval_type(retrieval_type, source)

    with eng.begin() as conn:
        if plss_norm:
            existing = conn.execute(
                text("SELECT id, minerals, report_links FROM areas_of_focus WHERE plss_normalized = :key"),
                {"key": plss_norm},
            ).mappings().first()
            if existing:
                existing_minerals = _normalize_minerals(existing.get("minerals") or [])
                existing_links = list(existing.get("report_links") or [])
                merged_minerals = list(dict.fromkeys(existing_minerals + minerals))
                merged_links = list(dict.fromkeys(existing_links + report_links))
                conn.execute(
                    text("""
                    UPDATE areas_of_focus SET
                      name = COALESCE(:name, name),
                      location_plss = COALESCE(:location_plss, location_plss),
                      location_coords = COALESCE(:location_coords, location_coords),
                      latitude = COALESCE(:lat, latitude),
                      longitude = COALESCE(:lon, longitude),
                      minerals = :minerals,
                      status = COALESCE(:status, status),
                      report_links = :report_links,
                      report_summary = COALESCE(:report_summary, report_summary),
                      validity_notes = COALESCE(:validity_notes, validity_notes),
                      source = COALESCE(:source, source),
                      external_id = COALESCE(:external_id, external_id),
                      blm_case_url = COALESCE(:blm_case_url, blm_case_url),
                      blm_serial_number = COALESCE(:blm_serial_number, blm_serial_number),
                      roi_score = COALESCE(:roi_score, roi_score),
                      state_abbr = COALESCE(:state_abbr, state_abbr),
                      township = COALESCE(:township, township),
                      "range" = COALESCE(:range_val, "range"),
                      section = COALESCE(:section, section),
                      meridian = COALESCE(:meridian, meridian),
                      retrieval_type = COALESCE(:retrieval_type, retrieval_type),
                      is_uploaded = CASE WHEN :is_uploaded IS TRUE THEN TRUE ELSE is_uploaded END,
                      updated_at = now()
                    WHERE id = :id
                    """),
                    {
                        "id": existing["id"],
                        "name": name_trim,
                        "location_plss": location_plss,
                        "location_coords": location_coords,
                        "lat": latitude,
                        "lon": longitude,
                        "minerals": merged_minerals,
                        "status": status,
                        "report_links": merged_links,
                        "report_summary": report_summary,
                        "validity_notes": validity_notes,
                        "source": source or "manual",
                        "external_id": external_id,
                        "blm_case_url": blm_case_url,
                        "blm_serial_number": blm_serial_number,
                        "roi_score": roi_score,
                        "state_abbr": state_abbr,
                        "township": township,
                        "range_val": range_val,
                        "section": section,
                        "meridian": meridian,
                        "retrieval_type": retrieval_type,
                        "is_uploaded": is_uploaded,
                    },
                )
                if merged_minerals:
                    try:
                        from mining_os.services.minerals import ensure_minerals_exist
                        ensure_minerals_exist(merged_minerals)
                    except Exception:
                        log.debug("ensure_minerals_exist failed for update of %r", name_trim, exc_info=True)
                return existing["id"]

        # Insert new target (no existing PLSS or no PLSS)
        pri = _normalize_target_status(priority)
        row = conn.execute(
            text("""
            INSERT INTO areas_of_focus (
              name, location_plss, location_coords, plss_normalized, latitude, longitude,
              minerals, status, report_links, report_summary, validity_notes,
              source, external_id, blm_case_url, blm_serial_number, roi_score, priority,
              state_abbr, township, "range", section, meridian, retrieval_type, is_uploaded
            ) VALUES (
              :name, :location_plss, :location_coords, :plss_normalized, :lat, :lon,
              :minerals, :status, :report_links, :report_summary, :validity_notes,
              :source, :external_id, :blm_case_url, :blm_serial_number, :roi_score, :priority,
              :state_abbr, :township, :range_val, :section, :meridian, :retrieval_type, :is_uploaded
            )
            RETURNING id
            """),
            {
                "name": name_trim,
                "location_plss": location_plss,
                "location_coords": location_coords,
                "plss_normalized": plss_norm,
                "lat": latitude,
                "lon": longitude,
                "minerals": minerals,
                "status": status,
                "report_links": report_links,
                "report_summary": report_summary,
                "validity_notes": validity_notes,
                "source": source or "manual",
                "external_id": external_id,
                "blm_case_url": blm_case_url,
                "blm_serial_number": blm_serial_number,
                "roi_score": roi_score,
                "priority": pri,
                "state_abbr": state_abbr,
                "township": township,
                "range_val": range_val,
                "section": section,
                "meridian": meridian,
                "retrieval_type": retrieval_type,
                "is_uploaded": is_uploaded if is_uploaded is not None else False,
            },
        ).first()

    # Make sure every mineral now on this target also exists on the Minerals
    # page. Failure here must NEVER block the target write.
    if minerals:
        try:
            from mining_os.services.minerals import ensure_minerals_exist
            ensure_minerals_exist(minerals)
        except Exception:
            log.debug("ensure_minerals_exist failed for target %r", name_trim, exc_info=True)
    return row[0] if row else 0


def ingest_from_data_files() -> dict:
    """Load CSVs from data_files into targets. Requires name + PLSS per row; skips rows missing either. Returns counts."""
    if not DATA_FILES_DIR.exists():
        log.warning("data_files dir not found: %s", DATA_FILES_DIR)
        return {"files": 0, "rows": 0, "skipped": 0, "errors": [], "message": "No data_files folder. Add CSVs there or use Import CSV for uploads."}

    counts = {"files": 0, "rows": 0, "skipped": 0, "errors": []}

    def _require_plss_and_name(rows: List[dict]) -> tuple[List[dict], int]:
        """Keep only rows with non-empty name and location_plss; return (valid, skipped_count)."""
        valid, skipped = [], 0
        for r in rows:
            name = (r.get("name") or "").strip()
            plss = (r.get("location_plss") or "").strip() if r.get("location_plss") else ""
            if not name or not plss:
                skipped += 1
                continue
            r["location_plss"] = plss or None
            valid.append(r)
        return valid, skipped

    # 1) Utah Mine Dockets - requires County (used as PLSS) + Name
    dockets_path = DATA_FILES_DIR / "Utah Mine Dockets w Coordinates - Full-Master.csv"
    if dockets_path.exists():
        try:
            rows = []
            with open(dockets_path, newline="", encoding="utf-8", errors="replace") as f:
                reader = csv.DictReader(f)
                for r in reader:
                    name = (r.get("Mine Name") or r.get("Property name") or "").strip()
                    if not name or name.startswith("#"):
                        continue
                    county = (r.get("County") or "").strip()
                    if not county:
                        counts["skipped"] += 1
                        continue
                    coords = (r.get("Approx. Coordinates") or "").strip()
                    lat, lon = _parse_coords(coords)
                    minerals = _normalize_minerals(r.get("Commodity") or r.get("All commodities"))
                    docket = (r.get("Docket") or "").strip()
                    external_id = f"{county}-{docket}" if docket else None
                    report_link = "https://ugspub.nr.utah.gov/" if docket else None
                    report_links = [report_link] if report_link else []
                    rows.append({
                        "name": name,
                        "location_plss": county,
                        "location_coords": coords if coords else None,
                        "latitude": lat,
                        "longitude": lon,
                        "minerals": minerals,
                        "status": "unknown",
                        "report_links": report_links,
                        "source": "data_files_utah_dockets",
                        "external_id": external_id,
                        "roi_score": min(100, 20 + len(minerals) * 10) if minerals else 10,
                    })
            valid, sk = _require_plss_and_name(rows)
            counts["skipped"] += sk
            for merged in _condense_rows_by_plss(valid):
                upsert_area(
                    name=merged["name"],
                    location_plss=merged["location_plss"],
                    location_coords=merged["location_coords"],
                    latitude=merged["latitude"],
                    longitude=merged["longitude"],
                    minerals=merged["minerals"],
                    status=merged["status"],
                    report_links=merged["report_links"],
                    source=merged["source"],
                    external_id=merged["external_id"],
                    roi_score=merged["roi_score"],
                )
                counts["rows"] += 1
            counts["files"] += 1
        except Exception as e:
            counts["errors"].append(f"Utah Dockets: {e}")
            log.exception("Ingest Utah Dockets")

    # 2) PerspectiveMines - requires Name + (State + Township/Range/Section) as PLSS
    perspective_path = DATA_FILES_DIR / "HINKINITE - MINE CLAIMS PERSPECTIVE -- MASTER - PerspectiveMines.csv"
    if perspective_path.exists():
        try:
            rows = []
            with open(perspective_path, newline="", encoding="utf-8", errors="replace") as f:
                reader = csv.DictReader(f)
                for r in reader:
                    name = (r.get("Mine/Claim Name") or "").strip()
                    if not name:
                        continue
                    twp, rng, sec = r.get("Township") or "", r.get("Range") or "", r.get("Section") or ""
                    plss = " ".join(p for p in [twp, rng, sec] if p).strip()
                    state = (r.get("State") or "").strip()
                    if state and plss:
                        location_plss = f"{state} {plss}"
                    else:
                        location_plss = plss
                    if not location_plss:
                        counts["skipped"] += 1
                        continue
                    minerals = _normalize_minerals(r.get("Mineral(s)") or "")
                    status_raw = (r.get("Status") or "").strip().upper()
                    status = "paid" if status_raw == "PAID" else "unpaid" if status_raw == "UNPAID" else "unknown"
                    rows.append({
                        "name": name,
                        "location_plss": location_plss,
                        "minerals": minerals,
                        "status": status,
                        "source": "data_files_perspective",
                        "roi_score": 50 if "unpaid" in status_raw.lower() and minerals else 30,
                    })
            for merged in _condense_rows_by_plss(rows):
                upsert_area(
                    name=merged["name"],
                    location_plss=merged["location_plss"],
                    minerals=merged["minerals"],
                    status=merged["status"],
                    source=merged["source"],
                    roi_score=merged["roi_score"],
                )
                counts["rows"] += 1
            counts["files"] += 1
        except Exception as e:
            counts["errors"].append(f"PerspectiveMines: {e}")
            log.exception("Ingest PerspectiveMines")

    # 3) Areas for Bryson Review - requires Name + Location (PLSS)
    bryson_path = DATA_FILES_DIR / "HINKINITE - MINE CLAIMS PERSPECTIVE -- MASTER - Areas for Bryson Review.csv"
    if bryson_path.exists():
        try:
            rows = []
            with open(bryson_path, newline="", encoding="utf-8", errors="replace") as f:
                reader = csv.DictReader(f)
                for r in reader:
                    name_cell = (r.get("Name") or "").strip()
                    if not name_cell:
                        continue
                    name = name_cell.split("\n")[0].strip()
                    location_plss = (r.get("Location") or "").strip()
                    if not location_plss:
                        counts["skipped"] += 1
                        continue
                    status_raw = (r.get("Status") or "").strip().lower()
                    status = "unpaid" if "unpaid" in status_raw or "waiting" in status_raw else "unknown"
                    notes = (r.get("BRYSON NOTES") or "").strip()
                    report_links = []
                    if "http" in name_cell:
                        for word in name_cell.split():
                            if word.startswith("http"):
                                report_links.append(word.strip(".,)"))
                    rows.append({
                        "name": name[:500],
                        "location_plss": location_plss,
                        "minerals": [],
                        "status": status,
                        "report_links": report_links,
                        "validity_notes": notes or None,
                        "source": "data_files_bryson",
                        "roi_score": 40 if status == "unpaid" else 20,
                    })
            for merged in _condense_rows_by_plss(rows):
                upsert_area(
                    name=merged["name"],
                    location_plss=merged["location_plss"],
                    minerals=merged["minerals"],
                    status=merged["status"],
                    report_links=merged["report_links"],
                    validity_notes=merged["validity_notes"],
                    source=merged["source"],
                    roi_score=merged["roi_score"],
                )
                counts["rows"] += 1
            counts["files"] += 1
        except Exception as e:
            counts["errors"].append(f"Bryson Review: {e}")
            log.exception("Ingest Bryson Review")

    if counts["files"] == 0 and not counts["errors"]:
        counts["message"] = "No CSV files found in data_files. Add CSVs (with Name and PLSS columns) or use Import CSV to upload."
    return counts


def _get_csv_cell(r: dict, *keys: str) -> str:
    """Case-insensitive lookup: try exact keys first, then any key whose lower() matches.
    Handles BOM (\\ufeff) and extra whitespace in headers."""
    _norm = lambda h: (h or "").lstrip("\ufeff").strip().lower()
    key_lowers = {k.lower(): k for k in keys}
    for k in keys:
        v = r.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    # Fallback: match any header that lower-cased equals one of the target lower names
    for header, val in r.items():
        if header and val is not None and str(val).strip():
            if _norm(header) in key_lowers or _norm(header).replace(" ", "") in key_lowers:
                return str(val).strip()
    return ""


def _csv_row_to_target(r: dict) -> tuple[dict | None, str | None]:
    """
    Extract name, State, and PLSS from a CSV row (flexible, case-insensitive column names).
    Accepts rows even when PLSS can't be fully parsed into T/R/S — stores raw PLSS and
    leaves township/range/section as None for later backfill.
    Returns (row_dict, None) on success, or (None, reason_string) on skip.
    """
    name = (
        _get_csv_cell(r, "name", "Name", "Mine/Claim Name", "Property name")
        or (r.get("name") or r.get("Name") or r.get("Mine/Claim Name") or r.get("Property name") or "").strip()
    )
    state_cell = (
        _get_csv_cell(r, "State", "state", "STATE", "state_abbr", "State_abbr", "ST", "State Code")
        or (r.get("State") or r.get("state") or r.get("state_abbr") or r.get("State_abbr") or "").strip()
    )
    state_abbr = (state_cell.upper()[:2] if state_cell else "UT")
    plss = (
        _get_csv_cell(r, "plss", "PLSS", "location_plss", "Location", "PLSS/Location")
        or (r.get("plss") or r.get("PLSS") or r.get("location_plss") or r.get("Location") or "").strip()
    )
    if not plss:
        twp = _get_csv_cell(r, "Township", "TWP", "Twp") or (r.get("Township") or r.get("Township ") or "").strip()
        rng = _get_csv_cell(r, "Range", "RNG", "Rng") or (r.get("Range") or r.get("Range ") or "").strip()
        sec = _get_csv_cell(r, "Section", "Sec", "Sect") or (r.get("Section") or r.get("Section ") or "").strip()
        plss = " ".join(p for p in [state_abbr, twp, rng, sec] if p)

    row_label = name or "(unnamed row)"
    if not name:
        return None, "Row skipped: no Name value found"
    if not plss or plss.strip() == state_abbr:
        return None, f'"{row_label}": no PLSS / location data found'

    comp = _parse_plss_to_components(plss, default_state=state_abbr)
    township = comp["township"] if comp and comp.get("township") else None
    range_val = comp["range"] if comp and comp.get("range") else None
    section = comp["section"] if comp and comp.get("section") else None
    final_state = (comp.get("state_abbr") if comp else None) or state_abbr
    if not final_state or len(final_state) != 2:
        final_state = state_abbr or "UT"
    meridian = None
    if comp:
        meridian = comp.get("meridian")
    if not meridian:
        try:
            from mining_os.services.fetch_claim_records import STATE_MERIDIAN, DEFAULT_MERIDIAN
            meridian = STATE_MERIDIAN.get(final_state, DEFAULT_MERIDIAN)
        except Exception:
            meridian = "26"

    if not township or not range_val:
        log.info("Row %r: PLSS %r did not fully parse (twp=%s, rng=%s, sec=%s) — importing with raw PLSS",
                 name, plss, township, range_val, section)

    report_links: List[str] = []
    for col in ("report", "report_url", "report_urls", "Report", "Report URL"):
        url = _get_csv_cell(r, col) or (r.get(col) or "").strip()
        if url and url.startswith("http"):
            report_links.append(url)

    lat_str = _get_csv_cell(r, "latitude", "Latitude", "lat", "Lat", "LAT")
    lon_str = _get_csv_cell(r, "longitude", "Longitude", "lon", "Lon", "LON", "long", "Long")
    latitude = None
    longitude = None
    if lat_str:
        try:
            latitude = float(lat_str)
        except (ValueError, TypeError):
            pass
    if lon_str:
        try:
            longitude = float(lon_str)
        except (ValueError, TypeError):
            pass

    return {
        "name": name[:500],
        "location_plss": plss,
        "state_abbr": final_state,
        "township": township,
        "range_val": range_val,
        "section": section,
        "meridian": meridian,
        "latitude": latitude,
        "longitude": longitude,
        "minerals": _normalize_minerals(r.get("minerals") or r.get("Mineral(s)") or r.get("Commodity") or ""),
        "status": (r.get("status") or r.get("Status") or "unknown").strip().lower() or "unknown",
        "report_links": report_links,
        "source": "csv_import",
    }, None


def get_existing_plss_map() -> Dict[str, dict]:
    """Return dict of plss_normalized -> {id, name} for all targets with PLSS."""
    eng = get_engine()
    with eng.begin() as conn:
        rows = conn.execute(
            text("SELECT id, name, plss_normalized FROM areas_of_focus WHERE plss_normalized IS NOT NULL AND TRIM(plss_normalized) != ''"),
        ).mappings().all()
    return {r["plss_normalized"]: {"id": r["id"], "name": r["name"]} for r in rows}


def _norm_csv_header(h: str | None) -> str:
    return (h or "").lstrip("\ufeff").strip().lower()


def guess_csv_column_mapping(headers: List[str]) -> Dict[str, str]:
    """Suggest which CSV column maps to each canonical import field (for UI defaults)."""
    if not headers:
        return {}
    nh = [_norm_csv_header(h) for h in headers]
    out: Dict[str, str] = {}

    def first_match(pred) -> None:
        for i, n in enumerate(nh):
            if pred(n, headers[i]):
                return headers[i]
        return None

    # Name: avoid overly generic "id" alone
    for i, n in enumerate(nh):
        if any(k in n for k in ("mine name", "claim name", "property name", "site name")):
            out["name"] = headers[i]
            break
    if "name" not in out:
        for i, n in enumerate(nh):
            if n in ("name", "target", "mine", "claim", "property", "site", "title"):
                out["name"] = headers[i]
                break
    if "name" not in out:
        for i, n in enumerate(nh):
            if "name" in n and "range" not in n and "township" not in n:
                out["name"] = headers[i]
                break

    for i, n in enumerate(nh):
        if n in ("state", "st", "state_abbr", "state code", "statecode"):
            out["state"] = headers[i]
            break
    if "state" not in out:
        for i, n in enumerate(nh):
            if n == "state" or n.startswith("state "):
                out["state"] = headers[i]
                break

    for i, n in enumerate(nh):
        if any(k in n for k in ("plss", "location plss", "sec/twp/rge", "sec twp")):
            out["plss"] = headers[i]
            break
    if "plss" not in out:
        for i, n in enumerate(nh):
            if n in ("location", "legal description", "legal desc", "location description"):
                out["plss"] = headers[i]
                break

    for i, n in enumerate(nh):
        if "township" in n or n in ("twp", "twn", "town"):
            out["township"] = headers[i]
            break
    for i, n in enumerate(nh):
        if n in ("range", "rng", "rge") or (n.startswith("range") and "meridian" not in n):
            out["range"] = headers[i]
            break
    for i, n in enumerate(nh):
        if n in ("section", "sec", "sect", "sctn"):
            out["section"] = headers[i]
            break

    for i, n in enumerate(nh):
        if any(k in n for k in ("mineral", "commodity", "commodities")):
            out["minerals"] = headers[i]
            break
    for i, n in enumerate(nh):
        if n == "status" or n.endswith(" status"):
            out["status"] = headers[i]
            break
    for i, n in enumerate(nh):
        if any(k in n for k in ("report url", "report_link", "pdf", "url")) and "lr2000" not in n:
            out["report_url"] = headers[i]
            break
    for i, n in enumerate(nh):
        if n in ("latitude", "lat", "y"):
            out["latitude"] = headers[i]
            break
    for i, n in enumerate(nh):
        if n in ("longitude", "lon", "long", "x"):
            out["longitude"] = headers[i]
            break

    return out


def validate_csv_column_mapping(m: Optional[Dict[str, Any]]) -> List[str]:
    """Return human-readable errors if mapping cannot produce rows."""
    err: List[str] = []
    if not m:
        err.append("Choose column mappings for Name, State, and location (PLSS or Township+Range+Section).")
        return err

    def g(key: str) -> str:
        v = m.get(key)
        return (str(v).strip() if v is not None else "")

    if not g("name"):
        err.append("Map a CSV column to Name.")
    if not g("state"):
        err.append("Map a CSV column to State (2-letter code).")
    has_plss = bool(g("plss"))
    has_trs = bool(g("township")) and bool(g("range")) and bool(g("section"))
    if not has_plss and not has_trs:
        err.append("Map PLSS / Location, or map Township, Range, and Section together.")
    return err


def _norm_csv_header(h: str) -> str:
    """Match DictReader keys across BOM, spacing, and case (browser preview vs server parse)."""
    return (h or "").lstrip("\ufeff").strip().lower()


def _apply_user_column_mapping(original: dict, mapping: Dict[str, Any]) -> dict:
    """Build a row dict using standard keys expected by _csv_row_to_target."""

    by_norm: Dict[str, str] = {}
    for k, v in original.items():
        nk = _norm_csv_header(k or "")
        if nk and nk not in by_norm:
            by_norm[nk] = str(v).strip() if v is not None else ""

    def pick(key: str) -> str:
        h = mapping.get(key)
        if not h or not isinstance(h, str):
            return ""
        if h in original:
            v = original.get(h)
            return str(v).strip() if v is not None else ""
        return by_norm.get(_norm_csv_header(h), "")

    out: Dict[str, Any] = {}
    if pick("name"):
        out["Name"] = pick("name")
    if pick("state"):
        out["State"] = pick("state")
    if pick("plss"):
        out["Location"] = pick("plss")
    if pick("township"):
        out["Township"] = pick("township")
    if pick("range"):
        out["Range"] = pick("range")
    if pick("section"):
        out["Section"] = pick("section")
    if pick("minerals"):
        out["minerals"] = pick("minerals")
    if pick("status"):
        out["status"] = pick("status")
    if pick("report_url"):
        out["Report URL"] = pick("report_url")
    if pick("latitude"):
        out["latitude"] = pick("latitude")
    if pick("longitude"):
        out["longitude"] = pick("longitude")
    return out


def inspect_csv_import(content: str) -> dict:
    """
    Parse CSV and return headers, first rows for preview, and suggested column mapping.
    """
    import io

    headers: List[str] = []
    sample_rows: List[dict] = []
    try:
        reader = csv.DictReader(io.StringIO(content), delimiter=",")
        headers = list(reader.fieldnames or [])
        for i, r in enumerate(reader):
            if i >= 12:
                break
            sample_rows.append({k: ("" if v is None else str(v)) for k, v in r.items()})
    except Exception as e:
        log.exception("inspect_csv_import")
        return {"headers": [], "sample_rows": [], "suggested_mapping": {}, "error": str(e)}
    sugg = guess_csv_column_mapping(headers)
    return {"headers": headers, "sample_rows": sample_rows, "suggested_mapping": sugg}


def preview_csv_import(
    content: str,
    bulk_priority: str | None = None,
    bulk_report_url: str | None = None,
    bulk_mineral: str | None = None,
    column_mapping: Optional[Dict[str, Any]] = None,
) -> dict:
    """
    Parse CSV and return valid rows plus conflicts (PLSS already exists).
    Without column_mapping: expects recognizable columns (Name, State, PLSS/Location, or T/R/S).
    With column_mapping: each row is remapped from user-chosen headers first.
    Returns {valid_rows, conflicts, skipped, errors}.
    """
    import io
    valid_rows: List[dict] = []
    conflicts: List[dict] = []
    skipped = 0
    errors: List[str] = []
    existing = get_existing_plss_map()
    if column_mapping is not None:
        errors.extend(validate_csv_column_mapping(column_mapping))
        if errors:
            return {"valid_rows": [], "conflicts": [], "skipped": 0, "errors": errors, "skip_reasons": [], "source_row_count": 0}
    skip_reasons: List[str] = []
    source_row_count = 0
    debug_first_row: Dict[str, Any] = {}
    try:
        reader = csv.DictReader(io.StringIO(content), delimiter=",")
        fieldnames = (reader.fieldnames or [])
        log.info("preview_csv_import: fieldnames=%s, column_mapping=%s", fieldnames, column_mapping)
        _fh = lambda h: (h or "").lstrip("\ufeff").strip().lower()
        if column_mapping is None:
            state_cols = [c for c in fieldnames if c and _fh(c) in ("state", "state_abbr")]
            if not state_cols and fieldnames:
                state_any = any(_fh(f or "").startswith("state") for f in fieldnames)
                if not state_any:
                    errors.append("CSV must include a 'State' column (2-letter state code). Rows without State are skipped.")
        for r in reader:
            source_row_count += 1
            if column_mapping is not None:
                mr = _apply_user_column_mapping(r, column_mapping)
                if source_row_count == 1:
                    debug_first_row = {"original_keys": list(r.keys()), "original_vals": {k: str(v)[:80] for k, v in r.items()}, "mapped": {k: str(v)[:80] for k, v in mr.items()}}
                    log.info("preview_csv_import row1: original=%s mapped=%s", dict(r), mr)
                row, reason = _csv_row_to_target(mr)
            else:
                if source_row_count == 1:
                    debug_first_row = {"original_keys": list(r.keys()), "original_vals": {k: str(v)[:80] for k, v in r.items()}}
                    log.info("preview_csv_import row1 (no mapping): %s", dict(r))
                row, reason = _csv_row_to_target(r)
            if source_row_count == 1:
                debug_first_row["parsed_ok"] = row is not None
                debug_first_row["skip_reason"] = reason
                if row:
                    debug_first_row["parsed_name"] = row.get("name")
                    debug_first_row["parsed_plss"] = row.get("location_plss")
                    debug_first_row["parsed_state"] = row.get("state_abbr")
            if row is None:
                skipped += 1
                if reason and len(skip_reasons) < 20:
                    skip_reasons.append(reason)
                log.info("preview_csv_import: row %d skipped: %s", source_row_count, reason)
                continue
            plss_norm = _normalize_plss(row["location_plss"], default_state=row.get("state_abbr"))
            if not plss_norm:
                skipped += 1
                if len(skip_reasons) < 20:
                    skip_reasons.append(
                        f'"{row.get("name", "?")}": could not build a location key from PLSS "{row.get("location_plss", "")}" '
                        f'(try a fuller PLSS string, or check State matches the location)'
                    )
                continue
            if bulk_priority:
                row["priority"] = bulk_priority.lower()
            if bulk_report_url:
                links = list(row.get("report_links") or [])
                if bulk_report_url not in links:
                    links.append(bulk_report_url)
                row["report_links"] = links
            if bulk_mineral:
                bm = _clean_mineral_name(bulk_mineral)
                if bm:
                    minerals = _normalize_minerals(row.get("minerals") or [])
                    if bm not in minerals:
                        minerals.append(bm)
                    row["minerals"] = minerals
            if plss_norm in existing:
                conflicts.append({
                    "plss": row["location_plss"],
                    "plss_normalized": plss_norm,
                    "existing_id": existing[plss_norm]["id"],
                    "existing_name": existing[plss_norm]["name"],
                    "new_name": row["name"],
                })
            valid_rows.append(row)
    except Exception as e:
        errors.append(str(e))
        log.exception("preview_csv_import")
    log.info("preview_csv_import: source_rows=%d valid=%d skipped=%d errors=%s", source_row_count, len(valid_rows), skipped, errors)
    return {
        "valid_rows": valid_rows,
        "conflicts": conflicts,
        "skipped": skipped,
        "errors": errors,
        "skip_reasons": skip_reasons,
        "source_row_count": source_row_count,
        "debug_first_row": debug_first_row,
    }


def apply_csv_import(
    rows: List[dict],
    conflict_strategy: str,
    bulk_priority: str | None = None,
    bulk_report_url: str | None = None,
    bulk_mineral: str | None = None,
) -> dict:
    """
    Apply imported rows. conflict_strategy: merge (into existing), use_old (skip), use_new (overwrite existing).
    bulk_priority, bulk_report_url, bulk_mineral applied to each row.
    Returns {applied, merged, skipped, errors, applied_names, merged_names}.
    """
    applied, merged, skipped = 0, 0, 0
    errors: List[str] = []
    applied_names: List[str] = []
    merged_names: List[str] = []
    existing = get_existing_plss_map()
    # Diagnostic: log first row's PLSS components to trace why state/township/range/section may not persist
    if rows:
        r0 = rows[0]
        log.info(
            "CSV import apply: first row name=%r plss=%r state_abbr=%r township=%r range_val=%r section=%r",
            r0.get("name"),
            r0.get("location_plss"),
            r0.get("state_abbr"),
            r0.get("township"),
            r0.get("range_val"),
            r0.get("section"),
        )
    for row in rows:
        plss_norm = _normalize_plss(row.get("location_plss"), default_state=row.get("state_abbr"))
        if not plss_norm:
            skipped += 1
            continue
        priority = _normalize_target_status(bulk_priority or row.get("priority"))
        report_links = list(row.get("report_links") or [])
        if bulk_report_url and bulk_report_url not in report_links:
            report_links.append(bulk_report_url)
        minerals = _normalize_minerals(row.get("minerals") or [])
        if bulk_mineral:
            bm = _clean_mineral_name(bulk_mineral)
            if bm and bm not in minerals:
                minerals.append(bm)
        if conflict_strategy == "use_old" and plss_norm in existing:
            skipped += 1
            continue
        if conflict_strategy == "use_new" and plss_norm in existing:
            # Update existing row with new data; use State and parsed PLSS components from row
            state_abbr = (row.get("state_abbr") or "").strip().upper()[:2] or None
            township = row.get("township")
            range_val = row.get("range_val")
            section = row.get("section")
            meridian = row.get("meridian")
            try:
                eng = get_engine()
                with eng.begin() as conn:
                    conn.execute(
                        text("""
                        UPDATE areas_of_focus SET
                          name = :name, location_plss = COALESCE(:location_plss, location_plss),
                          minerals = :minerals, status = COALESCE(:status, status),
                          report_links = :report_links, priority = :priority, source = COALESCE(:source, source),
                          state_abbr = COALESCE(:state_abbr, state_abbr),
                          township = COALESCE(:township, township),
                          "range" = COALESCE(:range_val, "range"),
                          section = COALESCE(:section, section),
                          meridian = COALESCE(:meridian, meridian),
                          latitude = COALESCE(:lat, latitude),
                          longitude = COALESCE(:lon, longitude),
                          is_uploaded = true, updated_at = now()
                        WHERE plss_normalized = :plss_norm
                        """),
                        {
                            "name": (row.get("name") or "Unknown")[:500],
                            "location_plss": row.get("location_plss"),
                            "minerals": minerals,
                            "status": row.get("status") or None,
                            "report_links": report_links,
                            "priority": priority,
                            "source": row.get("source") or "csv_import",
                            "plss_norm": plss_norm,
                            "state_abbr": state_abbr,
                            "township": township,
                            "range_val": range_val,
                            "section": section,
                            "meridian": meridian,
                            "lat": row.get("latitude"),
                            "lon": row.get("longitude"),
                        },
                    )
                merged += 1
                merged_names.append((row.get("name") or "Unknown")[:80])
            except Exception as e:
                errors.append(f"PLSS {row.get('location_plss')}: {e}")
            continue
        # merge or new: upsert with State and parsed township, range, section from row
        try:
            upsert_area(
                name=(row.get("name") or "Unknown")[:500],
                location_plss=row.get("location_plss"),
                state_abbr=(row.get("state_abbr") or "").strip().upper()[:2] or None,
                township=row.get("township"),
                range_val=row.get("range_val"),
                section=row.get("section"),
                meridian=row.get("meridian"),
                latitude=row.get("latitude"),
                longitude=row.get("longitude"),
                minerals=minerals,
                status=row.get("status") or "unknown",
                report_links=report_links,
                source=row.get("source") or "csv_import",
                priority=priority,
                is_uploaded=True,
            )
            if plss_norm in existing:
                merged += 1
                merged_names.append((row.get("name") or "Unknown")[:80])
            else:
                applied += 1
                applied_names.append((row.get("name") or "Unknown")[:80])
        except Exception as e:
            errors.append(f"{row.get('name')} ({row.get('location_plss')}): {e}")
    return {
        "applied": applied,
        "merged": merged,
        "skipped": skipped,
        "errors": errors,
        "applied_names": applied_names,
        "merged_names": merged_names,
    }


def _pg_array(lst: List[str]) -> str:
    if not lst:
        return "{}"

    def esc(s: str) -> str:
        return str(s).replace("\\", "\\\\").replace('"', '\\"')

    return "{" + ",".join(f'"{esc(x)}"' for x in lst) + "}"
