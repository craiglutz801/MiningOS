export interface SitlaMeta {
  ok: boolean;
  enabled: boolean;
  admin_enabled?: boolean;
  jobs_enabled?: boolean;
  label?: string;
  subtitle?: string;
  error?: string | null;
}

export interface SitlaSummaryCard {
  key: string;
  label: string;
  value: number | string;
  filter?: Record<string, string | number | boolean> | null;
}

export interface SitlaSummary {
  ok: boolean;
  error?: string | null;
  enabled?: boolean;
  coverage_banner?: {
    message: string;
    detail: string;
    enabled_sources: number;
    healthy_sources: number;
    failed_or_stale: number;
  };
  cards: SitlaSummaryCard[];
  disclaimer?: string;
}

export interface SitlaOpportunityRow {
  id: string;
  best_title: string | null;
  reference_number: string | null;
  lease_number: string | null;
  opportunity_type: string;
  lifecycle_status: string;
  county_name: string | null;
  published_commodity: string | null;
  commodities: string[] | null;
  acreage: number | null;
  plss_key: string | null;
  township: string | null;
  range: string | null;
  section_summary: string | null;
  meridian: string | null;
  latitude: number | null;
  longitude: number | null;
  geometry_accuracy?: string;
  offering_cycle: string | null;
  announcement_date: string | null;
  nomination_deadline: string | null;
  application_deadline: string | null;
  bidding_start_at: string | null;
  bidding_end_at: string | null;
  award_date: string | null;
  minimum_bid: number | string | null;
  winning_bid: number | string | null;
  annual_rental: number | string | null;
  royalty_rate: string | null;
  application_fee: number | string | null;
  bond_amount: number | string | null;
  primary_term_years: number | null;
  rights_clarity: string;
  surface_rights_status: string;
  mineral_rights_status: string;
  mineral_potential_score: number;
  acquisition_readiness_score: number;
  overall_priority_score: number;
  priority_tier: string;
  data_completeness_score: number;
  source_freshness_score: number;
  review_status: string;
  official_detail_url: string | null;
  external_bid_url: string | null;
  last_observed_at: string | null;
  is_active?: boolean;
  is_demo?: boolean;
  watchlisted?: boolean;
  nearby_active_claims?: number;
  mines_on_parcel?: number;
}

export interface SitlaOpportunityList {
  ok: boolean;
  error?: string | null;
  page: number;
  page_size: number;
  total: number;
  items: SitlaOpportunityRow[];
}

export interface SitlaOpportunityDetail {
  ok: boolean;
  error?: string | null;
  opportunity?: SitlaOpportunityRow & Record<string, unknown>;
  timeline?: Array<Record<string, unknown>>;
  commercial_terms?: Array<Record<string, unknown>> | Record<string, unknown> | null;
  legal_parts?: Array<Record<string, unknown>>;
  mineral_evidence?: Array<Record<string, unknown>>;
  claim_context?: Array<Record<string, unknown>>;
  evidence_ledger?: Array<Record<string, unknown>>;
  observations?: Array<Record<string, unknown>>;
  review_tasks?: Array<Record<string, unknown>>;
  score?: Record<string, unknown> | null;
  target_links?: Array<Record<string, unknown>>;
  disclaimer?: string;
}

export interface SitlaCoverage {
  ok: boolean;
  error?: string | null;
  sources: Array<Record<string, unknown>>;
  metrics: Record<string, number>;
  coverage_language?: string;
}

export interface SitlaFilters {
  search: string;
  county: string;
  status: string;
  opportunity_type: string;
  priority_tier: string;
  active_only: boolean;
}
