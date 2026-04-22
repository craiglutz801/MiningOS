const BASE = import.meta.env.DEV ? "/api" : "/api";

export class ApiError extends Error {
  status: number;
  body?: { error?: string; detail?: string };
  constructor(message: string, status: number, body?: { error?: string; detail?: string }) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

/** One row from batch fetch-claim-records / batch lr2000 endpoints. */
export interface BatchAreaActionRow {
  id: number;
  name: string | null;
  ok: boolean;
  error?: string | null;
  claims_count?: number;
}

export interface BatchAreaActionResponse {
  ok: boolean;
  error?: string;
  processed?: number;
  succeeded?: number;
  failed?: number;
  results?: BatchAreaActionRow[];
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const isForm = options?.body instanceof FormData;
  const res = await fetch(`${BASE}${path}`, {
    headers: isForm ? { ...options?.headers } : { "Content-Type": "application/json", ...options?.headers },
    ...options,
  });
  const text = await res.text();
  if (!res.ok) {
    let body: { error?: string; detail?: string | unknown } | undefined;
    try {
      body = text ? JSON.parse(text) : undefined;
    } catch {
      body = undefined;
    }
    const raw = body?.detail ?? body?.error ?? text ?? res.statusText;
    const message = typeof raw === "string" ? raw : JSON.stringify(raw);
    throw new ApiError(message, res.status, body as { error?: string; detail?: string } | undefined);
  }
  if (!text || !text.trim()) {
    throw new ApiError("Server returned empty response. Is the backend running on port 8000?", res.status, undefined);
  }
  try {
    return JSON.parse(text) as T;
  } catch {
    throw new ApiError("Server returned invalid response. Try http://localhost:8000", res.status, undefined);
  }
}

export const api = {
  health: () => request<{ status: string }>("/health"),

  minerals: {
    list: () => request<{ id: number; name: string; sort_order: number }[]>("/minerals"),
    add: (name: string, sort_order?: number) =>
      request<{ id: number; name: string }>("/minerals?" + new URLSearchParams({ name, ...(sort_order != null && { sort_order: String(sort_order) }) }), { method: "POST" }),
    delete: (id: number) => request<{ status: string }>(`/minerals/${id}`, { method: "DELETE" }),
    report: (id: number) => request<MineralReport>(`/minerals/${id}/report`),
  },

  areas: {
    mineralSuggestions: () => request<string[]>("/areas-of-focus/minerals"),
    list: (params?: {
      mineral?: string;
      status?: string;
      state_abbr?: string;
      claim_type?: string;
      township?: string;
      range_val?: string;
      sector?: string;
      name?: string;
      limit?: number;
    }) => {
      const q = new URLSearchParams();
      if (params?.mineral) q.set("mineral", params.mineral);
      if (params?.status) q.set("status", params.status);
      if (params?.state_abbr) q.set("state_abbr", params.state_abbr);
      if (params?.claim_type) q.set("claim_type", params.claim_type);
      if (params?.township) q.set("township", params.township);
      if (params?.range_val) q.set("range_val", params.range_val);
      if (params?.sector) q.set("sector", params.sector);
      if (params?.name) q.set("name", params.name);
      if (params?.limit) q.set("limit", String(params.limit));
      const query = q.toString();
      return request<Area[]>(`/areas-of-focus${query ? "?" + query : ""}`);
    },
    get: (id: number) => request<Area>(`/areas-of-focus/${id}`),
    create: (body: {
      name: string;
      location_plss?: string;
      latitude?: number;
      longitude?: number;
      report_url?: string;
      information?: string;
      status?: string;
      minerals?: string[];
      priority?: string;
    }) =>
      request<{ id: number; name: string; location_plss?: string | null; latitude?: number | null; longitude?: number | null }>(
        "/areas-of-focus",
        {
          method: "POST",
          body: JSON.stringify(body),
        }
      ),
    updatePriority: (id: number, priority: string) =>
      request<{ id: number; priority: string }>(`/areas-of-focus/${id}/priority`, {
        method: "POST",
        body: JSON.stringify({ priority }),
      }),
    updateClaimType: (id: number, claim_type: string | null) =>
      request<{ id: number; claim_type: string | null }>(`/areas-of-focus/${id}/claim-type`, {
        method: "POST",
        body: JSON.stringify({ claim_type }),
      }),
    updateNotes: (id: number, notes: string | null) =>
      request<{ id: number; notes: string | null }>(`/areas-of-focus/${id}/notes`, {
        method: "POST",
        body: JSON.stringify({ notes }),
      }),
    updateCoordinates: (id: number, latitude: number, longitude: number) =>
      request<{ id: number; latitude: number; longitude: number }>(`/areas-of-focus/${id}/coordinates`, {
        method: "POST",
        body: JSON.stringify({ latitude, longitude }),
      }),
    updateName: (id: number, name: string) =>
      request<{ id: number; name: string }>(`/areas-of-focus/${id}/name`, {
        method: "POST",
        body: JSON.stringify({ name }),
      }),
    updatePlss: (
      id: number,
      location_plss: string | null,
      opts: { regeocode_coordinates?: boolean } = {},
    ) =>
      request<{
        ok: boolean;
        error?: string;
        location_plss?: string | null;
        state_abbr?: string | null;
        township?: string | null;
        range?: string | null;
        section?: string | null;
        meridian?: string | null;
        latitude?: number | null;
        longitude?: number | null;
        regeocoded?: boolean;
        conflicting_id?: number;
        conflicting_name?: string;
      }>(`/areas-of-focus/${id}/plss`, {
        method: "POST",
        body: JSON.stringify({
          location_plss,
          regeocode_coordinates: opts.regeocode_coordinates ?? true,
        }),
      }),
    plssFromCoordinates: (id: number) =>
      request<{
        ok: boolean;
        error?: string;
        location_plss?: string;
        plssid?: string;
        plss_normalized?: string;
        conflicting_id?: number;
        conflicting_name?: string;
      }>(`/areas-of-focus/${id}/plss-from-coordinates`, { method: "POST", body: JSON.stringify({}) }),
    plssFromCoordinatesBatch: () =>
      request<{
        updated: number;
        total: number;
        results: { id: number; ok?: boolean; error?: string; location_plss?: string }[];
      }>("/areas-of-focus/plss-from-coordinates-batch", { method: "POST", body: JSON.stringify({}) }),
    ingest: () =>
      request<{ files: number; rows: number; skipped?: number; errors?: string[]; message?: string }>(
        "/areas-of-focus/ingest",
        { method: "POST" }
      ),
    importCsv: (form: FormData) =>
      request<{
        preview?: boolean;
        valid_rows?: number;
        skipped?: number;
        conflicts?: { plss: string; existing_id: number; existing_name: string; new_name: string }[];
        message?: string;
        applied?: number;
        merged?: number;
        errors?: string[];
        applied_names?: string[];
        merged_names?: string[];
      }>("/areas-of-focus/import-csv", { method: "POST", body: form }),
    checkBlm: (id: number) => request<{ status?: string; claims_found?: number }>(`/areas-of-focus/${id}/check-blm`, { method: "POST" }),
    fetchClaimRecords: async (id: number) => {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 10 * 60 * 1000); // 10 min
      try {
        return await request<{ ok: boolean; log: string; claims: unknown[]; error?: string; fetched_at?: string }>(
          `/areas-of-focus/${id}/fetch-claim-records`,
          { method: "POST", signal: controller.signal }
        );
      } finally {
        clearTimeout(timer);
      }
    },
    /** Sequential batch (max 25 ids per request; caller may chunk larger sets). */
    batchFetchClaimRecords: async (ids: number[]) => {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 45 * 60 * 1000);
      try {
        return await request<BatchAreaActionResponse>(
          `/areas-of-focus/batch/fetch-claim-records`,
          { method: "POST", body: JSON.stringify({ ids }), signal: controller.signal }
        );
      } finally {
        clearTimeout(timer);
      }
    },
    batchLr2000GeographicReport: async (ids: number[]) => {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 45 * 60 * 1000);
      try {
        return await request<BatchAreaActionResponse>(
          `/areas-of-focus/batch/lr2000-geographic-report`,
          { method: "POST", body: JSON.stringify({ ids }), signal: controller.signal }
        );
      } finally {
        clearTimeout(timer);
      }
    },
    /** MLRS geographic mining-claims query (in-app; same layer as BLM Geographic Index report). */
    lr2000GeographicReport: async (id: number) => {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 120 * 1000);
      try {
        return await request<{
          ok: boolean;
          error?: string | null;
          claims: unknown[];
          fetched_at?: string | null;
          query_method?: string | null;
          log?: string;
          input?: Record<string, unknown>;
          source?: string | null;
        }>(`/areas-of-focus/${id}/lr2000-geographic-report`, { method: "POST", signal: controller.signal });
      } finally {
        clearTimeout(timer);
      }
    },
    generateReport: async (id: number) => {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 120 * 1000); // 2 min
      try {
        return await request<{ ok: boolean; report?: string; error?: string }>(
          `/areas-of-focus/${id}/generate-report`,
          { method: "POST", signal: controller.signal }
        );
      } finally {
        clearTimeout(timer);
      }
    },
    cleanPreview: () =>
      request<{ no_plss: Area[]; duplicates: { plss: string; plss_normalized: string; targets: Area[] }[] }>(
        "/areas-of-focus/clean-preview"
      ),
    fillPlssAi: async (ids: number[]) => {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 8 * 60 * 1000);
      try {
        return await request<{
          ok: boolean;
          updated?: number;
          error?: string;
          message?: string;
          summary?: Record<string, number>;
          pending_count?: number;
          dry_run?: boolean;
          results?: {
            id: number;
            name?: string | null;
            ok: boolean;
            pending_apply?: boolean;
            kind?: string;
            plss?: string;
            township?: string | null;
            range?: string | null;
            section?: string | null;
            latitude?: number | null;
            longitude?: number | null;
            notes_append?: string | null;
            confidence?: string;
            error?: string;
            skip_reason?: string;
            duplicate_of?: number | null;
            duplicate_name?: string | null;
          }[];
        }>("/areas-of-focus/fill-plss-ai", {
          method: "POST",
          body: JSON.stringify({ ids }),
          signal: controller.signal,
        });
      } finally {
        clearTimeout(timer);
      }
    },
    fillPlssAiPreview: async (ids: number[]) => {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 8 * 60 * 1000);
      try {
        return await request<{
          ok: boolean;
          updated?: number;
          error?: string;
          message?: string;
          summary?: Record<string, number>;
          pending_count?: number;
          dry_run?: boolean;
          results?: {
            id: number;
            name?: string | null;
            ok: boolean;
            pending_apply?: boolean;
            kind?: string;
            plss?: string;
            township?: string | null;
            range?: string | null;
            section?: string | null;
            latitude?: number | null;
            longitude?: number | null;
            notes_append?: string | null;
            confidence?: string;
            error?: string;
            skip_reason?: string;
            duplicate_of?: number | null;
            duplicate_name?: string | null;
          }[];
        }>("/areas-of-focus/fill-plss-ai-preview", {
          method: "POST",
          body: JSON.stringify({ ids }),
          signal: controller.signal,
        });
      } finally {
        clearTimeout(timer);
      }
    },
    fillPlssAiApply: async (
      items: {
        id: number;
        plss: string;
        township?: string | null;
        range?: string | null;
        section?: string | null;
        latitude?: number | null;
        longitude?: number | null;
        notes_append?: string | null;
      }[]
    ) => {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 3 * 60 * 1000);
      try {
        return await request<{
          ok: boolean;
          updated?: number;
          error?: string;
          message?: string;
          results?: {
            id: number;
            name?: string | null;
            ok: boolean;
            kind?: string;
            plss?: string;
            error?: string;
          }[];
        }>("/areas-of-focus/fill-plss-ai-apply", {
          method: "POST",
          body: JSON.stringify({ items }),
          signal: controller.signal,
        });
      } finally {
        clearTimeout(timer);
      }
    },
    delete: (id: number) => request<{ status: string }>(`/areas-of-focus/${id}`, { method: "DELETE" }),
    consolidate: (keepId: number, mergeIds: number[]) =>
      request<{ kept: number; deleted: number[]; error?: string }>("/areas-of-focus/consolidate", {
        method: "POST",
        body: JSON.stringify({ keep_id: keepId, merge_ids: mergeIds }),
      }),
  },

  alerts: {
    sendPriorityUnpaid: () =>
      request<{ sent: boolean; email_sent?: boolean; count: number; recipient?: string; message?: string }>(
        "/alerts/send-priority-unpaid",
        { method: "POST" }
      ),
  },

  discovery: {
    getPrompts: () => request<DiscoveryPrompt[]>("/discovery/prompts"),
    getDefaultPrompt: () => request<DiscoveryPrompt>("/discovery/prompts/default"),
    savePrompt: (mineral_name: string, system_instruction: string, user_prompt_template: string) =>
      request<{ status: string }>("/discovery/prompts", {
        method: "PUT",
        body: JSON.stringify({ mineral_name, system_instruction, user_prompt_template }),
      }),
    run: (replace: boolean, limit_per_mineral?: number) => {
      const q = new URLSearchParams();
      q.set("replace", String(replace));
      if (limit_per_mineral != null) q.set("limit_per_mineral", String(limit_per_mineral));
      return request<DiscoveryRunResult>(`/discovery/run?${q.toString()}`, { method: "POST" });
    },
    listRuns: (limit?: number) =>
      request<DiscoveryRunSummary[]>(`/discovery/runs${limit != null ? `?limit=${limit}` : ""}`),
    getRun: (id: number) => request<DiscoveryRun>(`/discovery/runs/${id}`),
  },

  mineReport: {
    process: async (form: FormData) => {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 5 * 60 * 1000);
      try {
        return await request<ProcessMineReportResult>("/process-mine-report", {
          method: "POST",
          body: form,
          signal: controller.signal,
        });
      } finally {
        clearTimeout(timer);
      }
    },
    importTargets: (targets: ReportTarget[], pdfUrl?: string, pdfFilename?: string) =>
      request<{ imported: number; errors: string[] }>("/import-report-targets", {
        method: "POST",
        body: JSON.stringify({ targets, pdf_url: pdfUrl, pdf_filename: pdfFilename }),
      }),
  },

  batchReport: {
    parseCSV: async (form: FormData, reportSeries?: "OME" | "DMEA" | "DMA") => {
      if (reportSeries) form.set("report_series", reportSeries);
      return request<BatchParseResult>("/batch-process-reports/parse", {
        method: "POST",
        body: form,
      });
    },
    processRows: async (rows: BatchRow[], skipPdf?: boolean) => {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 30 * 60 * 1000);
      try {
        return await request<{ ok: boolean; rows: BatchRow[] }>("/batch-process-reports/process", {
          method: "POST",
          body: JSON.stringify({ rows, skip_pdf: skipPdf ?? false }),
          signal: controller.signal,
        });
      } finally {
        clearTimeout(timer);
      }
    },
    importTargets: async (targets: Record<string, unknown>[]) => {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 45 * 60 * 1000);
      try {
        return await request<{
          imported: number;
          errors: string[];
          skipped?: { name: string; reason: string }[];
          note?: string;
        }>("/batch-import-targets", {
          method: "POST",
          body: JSON.stringify({ targets }),
          signal: controller.signal,
        });
      } finally {
        clearTimeout(timer);
      }
    },
  },
};

export interface DiscoveryPrompt {
  mineral_name: string;
  system_instruction: string;
  user_prompt_template: string;
  updated_at?: string;
}

export interface DiscoveryRunResult {
  status: string;
  minerals_checked?: string[];
  areas_added?: number;
  replace?: boolean;
  errors?: string[];
  message?: string;
  log?: string[];
}

/** List item for discovery run history. */
export interface DiscoveryRunSummary {
  id: number;
  created_at: string;
  replace: boolean;
  limit_per_mineral: number;
  status: string;
  message?: string;
  minerals_checked?: string[];
  areas_added: number;
  log_line_count?: number;
  error_count?: number;
}

/** One location from the AI discovery response. */
export interface DiscoveryLocationFromAi {
  name: string;
  state: string;
  plss?: string;
  mineral: string;
  notes?: string;
}

/** Full discovery run (goal + full log and output). */
export interface DiscoveryRun extends DiscoveryRunSummary {
  log?: string[];
  errors?: string[];
  locations_from_ai?: DiscoveryLocationFromAi[];
  urls_from_web_search?: string[];
}

export interface Mineral {
  id: number;
  name: string;
  sort_order: number;
}

/** AI-generated mineral report (uses, buyers, major miners, formations, locations, mining/milling). */
export interface MineralReport {
  mineral_name: string;
  error?: string;
  overview: string;
  uses: string[];
  key_buyers: string[];
  major_mining_operations: string[];
  common_formations: string[];
  prevalent_locations: string[];
  mining_and_milling: string;
}

export interface BatchRow {
  docket: string;
  name: string;
  state_abbr: string;
  county: string;
  minerals: string[];
  file_size: string;
  file_size_mb: number | null;
  has_scan: boolean;
  downloadable: boolean;
  url: string;
  skipped_reason: string | null;
  report_series?: "OME" | "DMEA" | "DMA";
  pdf_targets?: ReportTarget[];
  pdf_processed?: boolean;
  pdf_error?: string | null;
  /** Set when PDF was read and AI ran but returned zero importable targets */
  pdf_note?: string | null;
  pdf_document_opened?: boolean;
  had_extractable_text?: boolean;
  extraction_reached_ai?: boolean;
}

export interface BatchParseResult {
  ok: boolean;
  total: number;
  downloadable: number;
  rows: BatchRow[];
}

export interface Area {
  id: number;
  name: string;
  /** Present in clean-preview rows when parsed from validity_notes (`County: …`). */
  county?: string;
  location_plss?: string;
  /** Section-level PLSS key when set; empty means “no PLSS” in Clean Targets. */
  plss_normalized?: string | null;
  location_coords?: string;
  latitude?: number;
  longitude?: number;
  minerals?: string[];
  status?: string;
  report_links?: string[];
  report_summary?: string;
  validity_notes?: string;
  source?: string;
  external_id?: string;
  blm_case_url?: string;
  blm_serial_number?: string;
  roi_score?: number;
  priority?: string;
  state_abbr?: string;
  meridian?: string;
  township?: string;
  range?: string;
  section?: string;
  is_uploaded?: boolean;
  claim_type?: string;
  report_count?: number;
  magnitude_score?: number;
    characteristics?: {
    claim_records?: {
      fetched_at?: string;
      log?: string;
      claims?: unknown[];
      error?: string;
    };
    /** Snapshot from Run LR2000 Report (MLRS FeatureServer geographic query). */
    lr2000_geographic_index?: {
      ok?: boolean;
      fetched_at?: string;
      claims?: unknown[];
      query_method?: string;
      log?: string;
      source?: string;
      input?: Record<string, unknown>;
    };
    blm_prod_types?: string[];
  };
}

export interface ReportTarget {
  name: string;
  state: string;
  plss: string;
  township: string;
  range: string;
  section: string;
  latitude: number | null;
  longitude: number | null;
  minerals: string[];
  county: string;
  notes: string;
}

export interface ProcessMineReportResult {
  ok: boolean;
  targets: ReportTarget[];
  text_length?: number;
  error?: string;
  pdf_url?: string;
  pdf_filename?: string;
}

// ---- Automation Engine types ----

export interface AutomationRule {
  id: number;
  name: string;
  enabled: boolean;
  filter_config: Record<string, string>;
  action_type: string;
  outcome_type: string;
  schedule_cron: string | null;
  max_targets: number;
  created_at: string;
  updated_at: string;
}

export interface AutomationRunLogRow {
  id: number;
  ok: boolean;
  name?: string | null;
  changed?: boolean;
  claims_count?: number;
  status?: string;
  old_status?: string;
  error?: string;
}

export interface AutomationRun {
  id: number;
  rule_id: number;
  rule_name?: string;
  action_type?: string;
  started_at: string;
  finished_at: string | null;
  trigger_type: string;
  status: string;
  targets_total: number;
  targets_ok: number;
  targets_err: number;
  changes_found: number;
  email_sent: boolean;
  error_message: string | null;
  results: AutomationRunLogRow[];
  summary: string | null;
}

export interface AutomationMeta {
  action_types: string[];
  outcome_types: string[];
  filter_keys: string[];
  scheduler_running: boolean;
}

export const automations = {
  meta: () => request<AutomationMeta>("/automations/meta"),
  listRules: () => request<AutomationRule[]>("/automations/rules"),
  getRule: (id: number) => request<AutomationRule>(`/automations/rules/${id}`),
  createRule: (body: {
    name: string;
    action_type: string;
    filter_config?: Record<string, string>;
    outcome_type?: string;
    schedule_cron?: string | null;
    max_targets?: number;
    enabled?: boolean;
  }) =>
    request<AutomationRule>("/automations/rules", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  updateRule: (id: number, body: Partial<AutomationRule>) =>
    request<AutomationRule>(`/automations/rules/${id}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  deleteRule: (id: number) =>
    request<{ status: string }>(`/automations/rules/${id}`, { method: "DELETE" }),
  triggerRule: (id: number) =>
    request<{
      ok: boolean;
      run_id?: number;
      error?: string;
      targets_total?: number;
      targets_ok?: number;
      targets_err?: number;
      changes_found?: number;
      email_sent?: boolean;
      summary?: string;
    }>(`/automations/rules/${id}/trigger`, { method: "POST" }),
  listRuns: (params?: { rule_id?: number; limit?: number; offset?: number }) => {
    const q = new URLSearchParams();
    if (params?.rule_id) q.set("rule_id", String(params.rule_id));
    if (params?.limit) q.set("limit", String(params.limit));
    if (params?.offset) q.set("offset", String(params.offset));
    const qs = q.toString();
    return request<AutomationRun[]>(`/automations/runs${qs ? "?" + qs : ""}`);
  },
  getRun: (id: number) => request<AutomationRun>(`/automations/runs/${id}`),
};
