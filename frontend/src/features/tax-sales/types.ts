export interface TaxSalesMeta {
  ok: boolean;
  enabled: boolean;
  admin_enabled?: boolean;
  jobs_enabled?: boolean;
  label?: string;
  subtitle?: string;
  error?: string | null;
}

export interface TaxSummaryCard {
  key: string;
  label: string;
  value: number | string;
  filter?: Record<string, string | number | boolean> | null;
}

export interface TaxSalesSummary {
  ok: boolean;
  error?: string | null;
  enabled?: boolean;
  coverage_banner?: {
    message: string;
    detail: string;
    enabled_counties: number;
    healthy_counties: number;
    failed_or_stale: number;
  };
  cards: TaxSummaryCard[];
  disclaimer?: string;
}

export interface TaxOpportunityRow {
  id: string;
  state: string;
  county_name: string;
  primary_apn: string | null;
  best_name: string | null;
  sale_lifecycle_status: string;
  auction_start_at: string | null;
  amount_due: number | string | null;
  minimum_bid: number | string | null;
  years_delinquent: number | null;
  acreage: number | null;
  patent_classification: string;
  patent_confidence: number;
  mineral_signal: string;
  commodities: string[] | null;
  access_status: string;
  data_completeness_score: number;
  source_freshness_score: number;
  mineral_potential_score: number;
  acquisition_readiness_score: number;
  overall_priority_score: number;
  priority_tier: string;
  review_status: string;
  last_observed_at: string | null;
  latitude: number | null;
  longitude: number | null;
  publication_scope: string;
  plss_key: string | null;
  watchlisted?: boolean;
  nearby_active_claims?: number;
  mines_on_parcel?: number;
  is_demo?: boolean;
  geometry_accuracy?: string;
}

export interface TaxOpportunityList {
  ok: boolean;
  error?: string | null;
  page: number;
  page_size: number;
  total: number;
  items: TaxOpportunityRow[];
}

export interface TaxOpportunityDetail {
  ok: boolean;
  error?: string | null;
  opportunity?: TaxOpportunityRow & Record<string, unknown>;
  timeline?: Array<Record<string, unknown>>;
  patent_matches?: Array<Record<string, unknown>>;
  mineral_evidence?: Array<Record<string, unknown>>;
  claim_context?: Array<Record<string, unknown>>;
  evidence_ledger?: Array<Record<string, unknown>>;
  observations?: Array<Record<string, unknown>>;
  review_tasks?: Array<Record<string, unknown>>;
  score?: Record<string, unknown> | null;
  target_links?: Array<Record<string, unknown>>;
  disclaimer?: string;
}

export interface TaxCoverage {
  ok: boolean;
  error?: string | null;
  jurisdictions: Array<Record<string, unknown>>;
  metrics: Record<string, number>;
  coverage_language?: string;
}

export interface TaxFilters {
  state: string;
  county: string;
  status: string;
  patent_classification: string;
  mineral_signal: string;
  priority_tier: string;
  review_status: string;
  search: string;
  min_score: string;
  auction_within_days: string;
  active_only: boolean;
}
