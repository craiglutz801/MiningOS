"""Central registry of every external data source.

All URLs live here (or in .env overrides) — never inline in adapter code.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class SourceDefinition:
    source_id: str
    display_name: str
    authority: str
    landing_page: str
    service_url: str | None
    required: bool
    state_scope: str  # "common", "NV", "UT"
    geometry_type: str  # "polygon", "point", "table", "mixed"
    cache_ttl_hours: int = 24
    notes: str = ""
    discovery: str | None = None  # "arcgis_item" | "msha_portal" | None
    item_id: str | None = None
    env_override: str | None = None
    fatality: str = "supporting_nonfatal"
    # fatality: core_fatal | core_nonfatal | state_primary_nonfatal | supporting_nonfatal

    def resolved_override(self) -> str | None:
        if self.env_override:
            value = os.environ.get(self.env_override, "").strip()
            if value:
                return value
        return None


SOURCES: dict[str, SourceDefinition] = {
    "blm_claims": SourceDefinition(
        source_id="blm_claims",
        display_name="BLM Active Mining Claims",
        authority="Bureau of Land Management (MLRS)",
        landing_page="https://gis.blm.gov/nlsdb/rest/services/Mining_Claims/MiningClaims/MapServer",
        service_url="https://gis.blm.gov/nlsdb/rest/services/Mining_Claims/MiningClaims/MapServer/1",
        required=True,
        state_scope="common",
        geometry_type="polygon",
        cache_ttl_hours=24,
        fatality="core_fatal",
        notes=(
            "Active Mining Claims polygon layer. Geometry is generated from legal land "
            "descriptions / PLSS and is approximate, not a surveyed boundary."
        ),
    ),
    "blm_plans": SourceDefinition(
        source_id="blm_plans",
        display_name="BLM Locatable Plans of Operations",
        authority="Bureau of Land Management (MLRS)",
        landing_page=(
            "https://www.blm.gov/programs/energy-and-minerals/mining-and-minerals/"
            "locatable-minerals/access-mining-notices-and-plans-operations"
        ),
        service_url=(
            "https://gis.blm.gov/nlsdb/rest/services/HUB/"
            "BLM_Natl_MLRS_Locatable_Plans_Of_Operations/FeatureServer/0"
        ),
        required=True,
        state_scope="common",
        geometry_type="polygon",
        cache_ttl_hours=24,
        fatality="supporting_nonfatal",
        notes="Plan-level locatable mineral cases; required but nonfatal.",
    ),
    "blm_notices": SourceDefinition(
        source_id="blm_notices",
        display_name="BLM Locatable Notices",
        authority="Bureau of Land Management (MLRS)",
        landing_page=(
            "https://www.blm.gov/programs/energy-and-minerals/mining-and-minerals/"
            "locatable-minerals/access-mining-notices-and-plans-operations"
        ),
        service_url=(
            "https://gis.blm.gov/nlsdb/rest/services/HUB/"
            "BLM_Natl_MLRS_Locatable_Notices/FeatureServer/0"
        ),
        required=True,
        state_scope="common",
        geometry_type="polygon",
        cache_ttl_hours=24,
        fatality="supporting_nonfatal",
        notes="Notice-level locatable mineral cases; required but nonfatal.",
    ),
    "msha_mines": SourceDefinition(
        source_id="msha_mines",
        display_name="MSHA Mines Dataset",
        authority="Mine Safety and Health Administration",
        landing_page="https://arlweb.msha.gov/opengovernmentdata/ogimsha.asp",
        service_url=None,
        required=True,
        state_scope="common",
        geometry_type="table",
        cache_ttl_hours=72,
        discovery="msha_portal",
        env_override="MSHA_MINES_URL",
        fatality="core_fatal",
        notes="Mine identity, status, type, commodity, coordinates.",
    ),
    "msha_inspections": SourceDefinition(
        source_id="msha_inspections",
        display_name="MSHA Inspections Dataset",
        authority="Mine Safety and Health Administration",
        landing_page="https://arlweb.msha.gov/opengovernmentdata/ogimsha.asp",
        service_url=None,
        required=True,
        state_scope="common",
        geometry_type="table",
        cache_ttl_hours=72,
        discovery="msha_portal",
        env_override="MSHA_INSPECTIONS_URL",
        fatality="core_nonfatal",
        notes="Inspection events per mine.",
    ),
    "msha_quarterly": SourceDefinition(
        source_id="msha_quarterly",
        display_name="MSHA Quarterly Employment/Production Dataset",
        authority="Mine Safety and Health Administration",
        landing_page="https://arlweb.msha.gov/opengovernmentdata/ogimsha.asp",
        service_url=None,
        required=True,
        state_scope="common",
        geometry_type="table",
        cache_ttl_hours=72,
        discovery="msha_portal",
        env_override="MSHA_QUARTERLY_URL",
        fatality="core_nonfatal",
        notes=(
            "Quarterly employee counts and hours. For metal/nonmetal mines these are "
            "activity evidence, never production quantity."
        ),
    ),
    "nevada_production": SourceDefinition(
        source_id="nevada_production",
        display_name="Nevada Division of Minerals Mineral Production",
        authority="Nevada Division of Minerals",
        landing_page="https://data-ndom.opendata.arcgis.com/pages/nevadamineralproduction",
        service_url=None,
        required=True,
        state_scope="NV",
        geometry_type="point",
        cache_ttl_hours=72,
        discovery="arcgis_item",
        item_id="123769bbf9e64e509cc2f0a2030eabb4",
        env_override="NEVADA_PRODUCTION_FEATURE_URL",
        fatality="state_primary_nonfatal",
        notes="Strongest Nevada production evidence: reported production years per mine.",
    ),
    "nevada_active_mines": SourceDefinition(
        source_id="nevada_active_mines",
        display_name="Nevada Active Mines / Energy Producers",
        authority="Nevada Division of Minerals",
        landing_page="https://www.arcgis.com/home/item.html?id=822720ccb83b48a59316912de21733ea",
        service_url=None,
        required=False,
        state_scope="NV",
        geometry_type="point",
        cache_ttl_hours=72,
        discovery="arcgis_item",
        item_id="822720ccb83b48a59316912de21733ea",
        env_override="NEVADA_ACTIVE_MINES_FEATURE_URL",
        fatality="supporting_nonfatal",
        notes="Optional supplemental current active-mine layer.",
    ),
    "utah_dogm": SourceDefinition(
        source_id="utah_dogm",
        display_name="Utah DOGM Mineral Mines / Permits",
        authority="Utah Division of Oil, Gas and Mining",
        landing_page="https://ogm.utah.gov/minerals-program/",
        service_url=None,
        required=True,
        state_scope="UT",
        geometry_type="mixed",
        cache_ttl_hours=72,
        discovery="arcgis_item",
        item_id="bff965abd4724c2eb7728536bb7aace4",
        env_override="UTAH_DOGM_FEATURE_URL",
        fatality="state_primary_nonfatal",
        notes="Mineral permits and mine records. An active permit is not proof of production.",
    ),
}

# Recognized BLM claim-type product codes.
CLAIM_TYPE_CODES: dict[str, str] = {
    "384101": "Lode Claim",
    "384103": "Lode Claim",
    "384201": "Placer Claim",
    "384203": "Placer Claim",
    "384301": "Tunnel Site",
    "384303": "Tunnel Site",
    "384401": "Mill Site",
    "384403": "Mill Site",
}

DEFAULT_ANALYTICAL_CLAIM_TYPES = ("Lode Claim", "Placer Claim")
RETAINED_HIDDEN_CLAIM_TYPES = ("Tunnel Site", "Mill Site")

MSHA_PORTAL_URLS = (
    "https://arlweb.msha.gov/opengovernmentdata/ogimsha.asp",
    "https://www.msha.gov/data-and-reports/data-sources-and-calculators/data-resources/msha-data-set-resources-gateway",
)

# Keywords used by MSHA portal discovery, per dataset. "avoid" keywords steer
# selection away from similarly named datasets (e.g. MinesProdYearly.zip when
# the plain Mines.zip identity dataset is wanted).
MSHA_PORTAL_KEYWORDS: dict[str, dict[str, list[str]]] = {
    "msha_mines": {
        "prefer": ["mines"],
        "avoid": ["prod", "yearly", "quarterly", "inspection", "violation",
                  "accident", "address", "employ", "contractor"],
    },
    "msha_inspections": {
        "prefer": ["inspections"],
        "avoid": ["violation", "accident", "contractor"],
    },
    "msha_quarterly": {
        "prefer": ["quarterly", "employ"],
        "avoid": ["yearly", "contractor", "coal only"],
    },
}

# ArcGIS layer-selection profiles.
LAYER_PROFILES: dict[str, dict] = {
    "nevada_production": {
        # Nevada publishes production split across per-commodity layers
        # (Metallics/NonMetallics/Clay/Aggregates <year>), so the profile must
        # recognize those titles; multiple qualifying layers are merged.
        "title_keywords": [
            "production", "mineral production", "producer", "mine",
            "metallics", "nonmetallics", "aggregates", "clay",
        ],
        "field_keywords": [
            "production", "year", "commodity", "mine", "operator",
            "company", "msha", "mined", "oz", "tons",
        ],
        "preferred_geometry": ["esriGeometryPoint"],
        "penalty_keywords": [
            "oil", "gas", "geothermal", "coal", "abandoned", "education",
            "sand and gravel pit boundaries", "claims", "county", "employment",
            "hillshade", "canvas", "boundaries", "districts",
        ],
        "min_selection_score": 30,
        "max_layers": 8,
    },
    "nevada_active_mines": {
        "title_keywords": ["active", "mine", "producer"],
        "field_keywords": ["mine", "status", "commodity", "operator"],
        "preferred_geometry": ["esriGeometryPoint"],
        "penalty_keywords": ["oil", "gas", "geothermal", "abandoned", "education"],
    },
    "utah_mineral_permits": {
        "title_keywords": ["mineral", "mine", "permit"],
        "field_keywords": ["permit", "operator", "status", "mine", "commodity", "activity"],
        "preferred_geometry": ["esriGeometryPoint", "esriGeometryPolygon"],
        "penalty_keywords": ["oil", "gas", "coal", "abandoned", "education"],
    },
}


def get_source(source_id: str) -> SourceDefinition:
    return SOURCES[source_id]
