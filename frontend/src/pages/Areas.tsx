import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { MapContainer, TileLayer, Marker } from "react-leaflet";
import L from "leaflet";
import { api, ApiError, type Area, type BatchAreaActionRow, type FetchClaimRecordsProgress } from "../api";
import { parseCsvForPreview, type CsvInspectResult } from "../csvInspectLocal";
import { ClaimPaymentBadge, getClaimPaymentText } from "../areas/claimPaymentBadge";

/** Server-enforced max ids per batch POST. */
const AREA_BATCH_MAX_CHUNK = 25;
const AREA_LIST_LIMIT = 2000;

const LS_BATCH_CHUNK = "mining_os_batch_chunk";
const LS_BATCH_PAUSE = "mining_os_batch_pause_sec";

function readStoredBatchChunk(): number {
  try {
    const v = parseInt(localStorage.getItem(LS_BATCH_CHUNK) || String(AREA_BATCH_MAX_CHUNK), 10);
    return Math.min(AREA_BATCH_MAX_CHUNK, Math.max(1, Number.isFinite(v) ? v : AREA_BATCH_MAX_CHUNK));
  } catch {
    return AREA_BATCH_MAX_CHUNK;
  }
}

function readStoredBatchPauseSec(): number {
  try {
    const v = parseInt(localStorage.getItem(LS_BATCH_PAUSE) || "0", 10);
    return Math.min(120, Math.max(0, Number.isFinite(v) ? v : 0));
  } catch {
    return 0;
  }
}

type BatchResultsRow = {
  id: number;
  name: string | null;
  fetchOk?: boolean;
  fetchClaims?: number;
  fetchError?: string | null;
  lrOk?: boolean;
  lrClaims?: number;
  lrError?: string | null;
};

function mergeBatchRow(
  map: Map<number, BatchResultsRow>,
  r: BatchAreaActionRow,
  kind: "fetch" | "lr2000"
): void {
  const prev = map.get(r.id) ?? { id: r.id, name: r.name };
  if (kind === "fetch") {
    prev.fetchOk = r.ok;
    prev.fetchClaims = r.claims_count;
    prev.fetchError = r.error ?? null;
  } else {
    prev.lrOk = r.ok;
    prev.lrClaims = r.claims_count;
    prev.lrError = r.error ?? null;
  }
  map.set(r.id, prev);
}

function areaHasFiniteCoords(a: Pick<Area, "latitude" | "longitude"> | null | undefined): boolean {
  if (!a) return false;
  return Number.isFinite(a.latitude as number) && Number.isFinite(a.longitude as number);
}

function areaMissingNormalizedPlss(a: Pick<Area, "plss_normalized"> | null | undefined): boolean {
  if (!a) return true;
  return !String(a.plss_normalized ?? "").trim();
}

function dedupeMineralList(minerals: string[]): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  for (const mineral of minerals) {
    const trimmed = mineral.trim();
    if (!trimmed) continue;
    const key = trimmed.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(trimmed);
  }
  return out;
}

function formatApiDetail(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((item) => formatApiDetail(item)).filter(Boolean).join("\n");
  }
  if (detail && typeof detail === "object") {
    const obj = detail as Record<string, unknown>;
    if (typeof obj.msg === "string" && obj.msg.trim()) return obj.msg;
    try {
      return JSON.stringify(detail);
    } catch {
      return String(detail);
    }
  }
  if (detail == null) return "";
  return String(detail);
}

function formatFetchClaimProgress(progress: FetchClaimRecordsProgress): string {
  const msg = progress.message?.trim();
  if (msg) return msg;
  if (typeof progress.current === "number" && typeof progress.total === "number" && progress.total > 0) {
    return `Checked ${progress.current} of ${progress.total} claim pages…`;
  }
  if (progress.status === "queued") return "Queued Fetch Claim Records job…";
  if (progress.status === "running") return "Fetching claim records…";
  if (progress.status === "done") return "Fetch complete.";
  return "Fetch Claim Records failed.";
}

const SATELLITE_TILE = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}";
const TARGET_PIN = new L.Icon({
  iconUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png",
  iconRetinaUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png",
  shadowUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png",
  iconSize: [25, 41],
  iconAnchor: [12, 41],
});

/** Canonical CSV import fields → FormData column_mapping JSON (values = source column header names). */
const EMPTY_CSV_MAPPING: Record<string, string> = {
  name: "",
  state: "",
  plss: "",
  township: "",
  range: "",
  section: "",
  minerals: "",
  status: "",
  report_url: "",
  latitude: "",
  longitude: "",
};

function importMappingIsValid(m: Record<string, string>, headers: string[] | undefined): boolean {
  if (!headers || !Array.isArray(headers)) return false;
  const sel = (k: string) => {
    const v = m[k]?.trim();
    return v && headers.includes(v) ? v : "";
  };
  if (!sel("name") || !sel("state")) return false;
  const plssOk = !!sel("plss");
  const trs = !!sel("township") && !!sel("range") && !!sel("section");
  return plssOk || trs;
}

/** Our standard column fields for the mapping UI. */
const MAPPING_FIELDS: readonly { key: string; label: string; required: boolean; group: string }[] = [
  { key: "name", label: "Name", required: true, group: "required" },
  { key: "state", label: "State (2-letter)", required: true, group: "required" },
  { key: "plss", label: "PLSS / Location", required: false, group: "location" },
  { key: "township", label: "Township", required: false, group: "location" },
  { key: "range", label: "Range", required: false, group: "location" },
  { key: "section", label: "Section", required: false, group: "location" },
  { key: "minerals", label: "Minerals / Commodity", required: false, group: "optional" },
  { key: "status", label: "Status", required: false, group: "optional" },
  { key: "report_url", label: "Report URL", required: false, group: "optional" },
  { key: "latitude", label: "Latitude", required: false, group: "optional" },
  { key: "longitude", label: "Longitude", required: false, group: "optional" },
] as const;

export function Areas() {
  const [searchParams] = useSearchParams();
  const areaIdParam = searchParams.get("areaId");
  const mineralParam = searchParams.get("mineral") ?? "";
  const statusParam = searchParams.get("status") ?? "";
  const targetStatusParam = searchParams.get("target_status") ?? "";
  const [areas, setAreas] = useState<Area[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [nameFilter, setNameFilter] = useState("");
  const [nameDropdownOpen, setNameDropdownOpen] = useState(false);
  const [nameInputFocused, setNameInputFocused] = useState(false);
  const [mineralFilter, setMineralFilter] = useState(mineralParam);
  const [statusFilter, setStatusFilter] = useState(statusParam);
  const [targetStatusFilter, setTargetStatusFilter] = useState(targetStatusParam);
  const [stateFilter, setStateFilter] = useState("");
  const [claimTypeFilter, setClaimTypeFilter] = useState("");
  const [retrievalTypeFilter, setRetrievalTypeFilter] = useState("");
  const [townshipFilter, setTownshipFilter] = useState("");
  const [rangeFilter, setRangeFilter] = useState("");
  const [sectorFilter, setSectorFilter] = useState("");
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [mineralSuggestions, setMineralSuggestions] = useState<string[]>([]);
  const [mineralDropdownOpen, setMineralDropdownOpen] = useState(false);
  const [mineralInputFocused, setMineralInputFocused] = useState(false);
  const [selected, setSelected] = useState<Area | null>(null);
  const [alertSending, setAlertSending] = useState(false);
  const [prioritySaving, setPrioritySaving] = useState(false);
  const [importFile, setImportFile] = useState<File | null>(null);
  const [importBulkPriority, setImportBulkPriority] = useState("monitoring_low");
  const [importBulkReportUrl, setImportBulkReportUrl] = useState("");
  const [importBulkMineral, setImportBulkMineral] = useState("");
  const [importLoading, setImportLoading] = useState(false);
  const [importResult, setImportResult] = useState<{
    applied: number;
    merged: number;
    skipped: number;
    errors: string[];
    applied_names?: string[];
    merged_names?: string[];
  } | null>(null);
  const [importPreview, setImportPreview] = useState<{
    preview?: boolean;
    valid_rows?: number;
    skipped?: number;
    skip_reasons?: string[];
    source_row_count?: number;
    conflicts?: { plss: string; existing_id: number; existing_name: string; new_name: string }[];
    message?: string;
    applied?: number;
    merged?: number;
    errors?: string[];
  } | null>(null);
  const [importConflictStrategy, setImportConflictStrategy] = useState<"merge" | "use_old" | "use_new" | "">("");
  const [csvInspect, setCsvInspect] = useState<CsvInspectResult | null>(null);
  const [importColumnMapping, setImportColumnMapping] = useState<Record<string, string>>(() => ({ ...EMPTY_CSV_MAPPING }));
  const [importCsvSession, setImportCsvSession] = useState(0);
  const [importSuccessBanner, setImportSuccessBanner] = useState<{ applied: number; merged: number; skipped: number } | null>(null);
  const [addTargetsModalOpen, setAddTargetsModalOpen] = useState(false);
  const [addTargetMode, setAddTargetMode] = useState<"csv" | "single">("csv");
  const [singleName, setSingleName] = useState("");
  const [singleState, setSingleState] = useState("");
  const [singleTownship, setSingleTownship] = useState("");
  const [singleRange, setSingleRange] = useState("");
  const [singleSection, setSingleSection] = useState("");
  const [singleReportUrl, setSingleReportUrl] = useState("");
  const [singleInformation, setSingleInformation] = useState("");
  const [singleStatus, setSingleStatus] = useState("");
  const [singleMinerals, setSingleMinerals] = useState("");
  const [singlePriority, setSinglePriority] = useState("monitoring_low");
  const [singleSaving, setSingleSaving] = useState(false);
  const [singleLatitude, setSingleLatitude] = useState("");
  const [singleLongitude, setSingleLongitude] = useState("");
  const [fillPlssFromCoordsLoading, setFillPlssFromCoordsLoading] = useState(false);
  const [cleanCoordsLoading, setCleanCoordsLoading] = useState(false);
  const [cleanCoordsBanner, setCleanCoordsBanner] = useState<string | null>(null);
  const [coordsEditing, setCoordsEditing] = useState(false);
  const [coordsLatDraft, setCoordsLatDraft] = useState("");
  const [coordsLonDraft, setCoordsLonDraft] = useState("");
  const [coordsSaving, setCoordsSaving] = useState(false);
  const [tableSelectedIds, setTableSelectedIds] = useState<Set<number>>(new Set());
  const [batchControlOpen, setBatchControlOpen] = useState(false);
  const [batchOptFetch, setBatchOptFetch] = useState(true);
  const [batchOptLr2000, setBatchOptLr2000] = useState(true);
  const [batchChunkDraft, setBatchChunkDraft] = useState(readStoredBatchChunk);
  const [batchPauseDraft, setBatchPauseDraft] = useState(readStoredBatchPauseSec);
  const [batchRunStatus, setBatchRunStatus] = useState<string | null>(null);
  const [batchResultsModal, setBatchResultsModal] = useState<{
    title: string;
    summary: string;
    rows: BatchResultsRow[];
    modes: { fetch: boolean; lr2000: boolean };
  } | null>(null);

  // USPS 2-letter state codes (50 states + DC), alphabetical by code
  const US_STATES = ["AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC"];
  const [cleanModalOpen, setCleanModalOpen] = useState(false);
  const [cleanData, setCleanData] = useState<{
    no_plss: Area[];
    duplicates: { plss: string; plss_normalized: string; targets: Area[] }[];
  } | null>(null);
  const [cleanLoading, setCleanLoading] = useState(false);
  const [cleanKeepIdPerGroup, setCleanKeepIdPerGroup] = useState<Record<string, number>>({});
  const [cleanDeleting, setCleanDeleting] = useState<number | null>(null);
  const [cleanConsolidating, setCleanConsolidating] = useState<string | null>(null);
  const [cleanNoPlssSelected, setCleanNoPlssSelected] = useState<Set<number>>(new Set());
  const [cleanAiLoading, setCleanAiLoading] = useState(false);
  const [cleanAiBanner, setCleanAiBanner] = useState<string | null>(null);
  const [cleanAiFailures, setCleanAiFailures] = useState<{ id: number; error?: string; kind?: string }[] | null>(null);
  const [cleanAiOutcome, setCleanAiOutcome] = useState<{ ok: number; fail: number } | null>(null);
  const [cleanModalTab, setCleanModalTab] = useState<"no_plss" | "duplicates">("no_plss");
  const [notesEditing, setNotesEditing] = useState(false);
  const [notesDraft, setNotesDraft] = useState("");
  const [notesSaving, setNotesSaving] = useState(false);
  const [mineralsEditing, setMineralsEditing] = useState(false);
  const [mineralsDraft, setMineralsDraft] = useState<string[]>([]);
  const [mineralDraftInput, setMineralDraftInput] = useState("");
  const [mineralDraftDropdownOpen, setMineralDraftDropdownOpen] = useState(false);
  const [mineralDraftInputFocused, setMineralDraftInputFocused] = useState(false);
  const [mineralsSaving, setMineralsSaving] = useState(false);
  const [nameEditing, setNameEditing] = useState(false);
  const [nameDraft, setNameDraft] = useState("");
  const [nameSaving, setNameSaving] = useState(false);
  const [plssEditing, setPlssEditing] = useState(false);
  const [plssStateDraft, setPlssStateDraft] = useState("");
  const [plssTownshipDraft, setPlssTownshipDraft] = useState("");
  const [plssRangeDraft, setPlssRangeDraft] = useState("");
  const [plssSectionDraft, setPlssSectionDraft] = useState("");
  const [plssRegeocode, setPlssRegeocode] = useState(true);
  const [plssSaving, setPlssSaving] = useState(false);
  const [fetchClaimRecordsLoading, setFetchClaimRecordsLoading] = useState(false);
  const [fetchClaimRecordsProgress, setFetchClaimRecordsProgress] = useState<string | null>(null);
  const [lr2000Loading, setLr2000Loading] = useState(false);
  const [clearClaimSnapshotLoading, setClearClaimSnapshotLoading] = useState(false);
  const [clearLr2000SnapshotLoading, setClearLr2000SnapshotLoading] = useState(false);
  const [rawJsonModal, setRawJsonModal] = useState<{ title: string; data: unknown } | null>(null);
  const [generateReportLoading, setGenerateReportLoading] = useState(false);
  /** After fill-plss-ai-preview: review proposed PLSS before apply. */
  const [plssAiReviewModal, setPlssAiReviewModal] = useState<null | {
    message: string;
    results: {
      id: number;
      name?: string | null;
      ok: boolean;
      pending_apply?: boolean;
      plss?: string;
      township?: string | null;
      range?: string | null;
      section?: string | null;
      latitude?: number | null;
      longitude?: number | null;
      notes_append?: string | null;
      confidence?: string;
      kind?: string;
      error?: string;
      duplicate_of?: number | null;
      duplicate_name?: string | null;
    }[];
    applyIds: Set<number>;
    plssEdits: Record<number, string>;
  }>(null);
  const [plssAiApplying, setPlssAiApplying] = useState(false);

  // Sync URL params into filter state when they change (e.g. landing from Minerals "Locations" link)
  useEffect(() => {
    setMineralFilter(searchParams.get("mineral") ?? "");
    setStatusFilter(searchParams.get("status") ?? "");
    setTargetStatusFilter(searchParams.get("target_status") ?? "");
  }, [searchParams]);

  useEffect(() => {
    setCoordsEditing(false);
    setCoordsLatDraft("");
    setCoordsLonDraft("");
    setMineralsEditing(false);
    setMineralsDraft([]);
    setMineralDraftInput("");
    setMineralDraftDropdownOpen(false);
    setMineralDraftInputFocused(false);
  }, [selected?.id]);

  // Load distinct minerals for autocomplete (merge minerals of interest + minerals already on targets)
  useEffect(() => {
    const load = async () => {
      try {
        const [fromAreas, fromMinerals] = await Promise.all([
          api.areas.mineralSuggestions().catch(() => [] as string[]),
          api.minerals.list().catch(() => [] as { id: number; name: string }[]),
        ]);
        const mineralNames = fromMinerals.map((m) => m.name);
        const merged = Array.from(new Set([...mineralNames, ...fromAreas]));
        merged.sort((a, b) => a.localeCompare(b));
        setMineralSuggestions(merged);
      } catch {
        setMineralSuggestions([]);
      }
    };
    load();
  }, []);

  /** Merge minerals from the current target list so the filter always includes what you see on the page. */
  useEffect(() => {
    if (!areas.length) return;
    const fromRows = new Set<string>();
    for (const a of areas) {
      for (const m of a.minerals || []) {
        const s = String(m).trim();
        if (s) fromRows.add(s);
      }
    }
    setMineralSuggestions((prev) => {
      const merged = Array.from(new Set([...prev, ...fromRows]));
      merged.sort((a, b) => a.localeCompare(b, undefined, { sensitivity: "base" }));
      return merged;
    });
  }, [areas]);

  const addMineralDraft = (rawValue: string) => {
    const trimmed = rawValue.trim().replace(/[;,]+$/, "");
    if (!trimmed) return;
    const canonical =
      mineralSuggestions.find((m) => m.toLowerCase() === trimmed.toLowerCase()) ?? trimmed;
    setMineralsDraft((prev) => dedupeMineralList([...prev, canonical]));
    setMineralDraftInput("");
    setMineralDraftDropdownOpen(false);
  };

  const removeMineralDraft = (mineral: string) => {
    setMineralsDraft((prev) => prev.filter((m) => m.toLowerCase() !== mineral.toLowerCase()));
  };

  const load = () => {
    setLoading(true);
    setError(null);
    api.areas
      .list({
        mineral: mineralFilter || undefined,
        status: statusFilter || undefined,
        target_status: targetStatusFilter || undefined,
        state_abbr: stateFilter || undefined,
        claim_type: claimTypeFilter || undefined,
        retrieval_type: retrievalTypeFilter || undefined,
        township: townshipFilter.trim() || undefined,
        range_val: rangeFilter.trim() || undefined,
        sector: sectorFilter.trim() || undefined,
        name: nameFilter.trim() || undefined,
        limit: AREA_LIST_LIMIT,
      })
      .then((list) => {
        const rows = Array.isArray(list) ? list : [];
        setAreas(rows);
        if (areaIdParam) {
          const id = parseInt(areaIdParam, 10);
          if (!Number.isNaN(id)) {
            const area = rows.find((a) => a.id === id);
            if (area) setSelected(area);
          }
        }
      })
      .catch((e) => {
        if (e instanceof ApiError && e.status === 503 && e.body?.error === "database_unavailable") {
          setError("DB_SETUP");
        } else {
          const detail = e instanceof ApiError ? formatApiDetail(e.body?.detail) : "";
          setError(detail || (e as Error).message);
        }
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    if (!batchControlOpen) return;
    setBatchChunkDraft(readStoredBatchChunk());
    setBatchPauseDraft(readStoredBatchPauseSec());
  }, [batchControlOpen]);

  const runConfiguredBatch = async () => {
    const ids = Array.from(tableSelectedIds);
    if (ids.length === 0) return;
    if (!batchOptFetch && !batchOptLr2000) {
      setError("Choose at least one action: Fetch claim records and/or LR2000 report.");
      return;
    }
    let chunk = Math.min(AREA_BATCH_MAX_CHUNK, Math.max(1, Math.floor(batchChunkDraft)));
    if (!Number.isFinite(chunk)) chunk = AREA_BATCH_MAX_CHUNK;
    let pauseSec = Math.min(120, Math.max(0, Math.floor(batchPauseDraft)));
    if (!Number.isFinite(pauseSec)) pauseSec = 0;
    localStorage.setItem(LS_BATCH_CHUNK, String(chunk));
    localStorage.setItem(LS_BATCH_PAUSE, String(pauseSec));
    const pauseMs = pauseSec * 1000;
    const numChunks = Math.ceil(ids.length / chunk);

    setBatchControlOpen(false);
    setError(null);
    setBatchRunStatus("Starting…");

    const byId = new Map<number, BatchResultsRow>();
    let fetchOk = 0;
    let fetchFail = 0;
    let lrOk = 0;
    let lrFail = 0;
    let aborted = false;

    try {
      for (let i = 0; i < ids.length; i += chunk) {
        const slice = ids.slice(i, i + chunk);
        const chunkIdx = Math.floor(i / chunk) + 1;

        if (batchOptFetch) {
          setBatchRunStatus(`Fetch claim records — request ${chunkIdx} of ${numChunks}…`);
          const res = await api.areas.batchFetchClaimRecords(slice);
          if (!res.ok && res.error) {
            setError(res.error);
            aborted = true;
            break;
          }
          for (const r of res.results ?? []) {
            mergeBatchRow(byId, r, "fetch");
            if (r.ok) fetchOk += 1;
            else fetchFail += 1;
          }
        }

        if (batchOptLr2000) {
          setBatchRunStatus(`LR2000 report — request ${chunkIdx} of ${numChunks}…`);
          const res = await api.areas.batchLr2000GeographicReport(slice);
          if (!res.ok && res.error) {
            setError(res.error);
            aborted = true;
            break;
          }
          for (const r of res.results ?? []) {
            mergeBatchRow(byId, r, "lr2000");
            if (r.ok) lrOk += 1;
            else lrFail += 1;
          }
        }

        if (pauseMs > 0 && i + chunk < ids.length) {
          setBatchRunStatus(`Pausing ${pauseSec}s before next group…`);
          await new Promise((r) => setTimeout(r, pauseMs));
        }
      }

      const rows: BatchResultsRow[] = ids.map((id) => {
        const row = byId.get(id);
        if (row) return row;
        const a = areas.find((x) => x.id === id);
        return { id, name: a?.name ?? null };
      });

      if (!aborted) {
        const parts: string[] = [];
        if (batchOptFetch) parts.push(`Fetch: ${fetchOk} ok, ${fetchFail} failed`);
        if (batchOptLr2000) parts.push(`LR2000: ${lrOk} ok, ${lrFail} failed`);
        setBatchResultsModal({
          title: "Batch results",
          summary: `${parts.join(" · ")} (${ids.length} targets).`,
          rows,
          modes: { fetch: batchOptFetch, lr2000: batchOptLr2000 },
        });
        setTableSelectedIds(new Set());
      }

      load();
      if (selected) {
        try {
          const full = await api.areas.get(selected.id);
          setSelected(full);
        } catch {
          /* ignore */
        }
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Batch run failed");
    } finally {
      setBatchRunStatus(null);
    }
  };

  useEffect(
    () => load(),
    [mineralFilter, statusFilter, targetStatusFilter, stateFilter, claimTypeFilter, retrievalTypeFilter, townshipFilter, rangeFilter, sectorFilter, nameFilter]
  );

  const refreshMineralSuggestions = async () => {
    try {
      const [fromAreas, fromMinerals] = await Promise.all([
        api.areas.mineralSuggestions().catch(() => [] as string[]),
        api.minerals.list().catch(() => [] as { id: number; name: string }[]),
      ]);
      const mineralNames = fromMinerals.map((m) => m.name);
      const merged = Array.from(new Set([...mineralNames, ...fromAreas]));
      merged.sort((a, b) => a.localeCompare(b));
      setMineralSuggestions(merged);
    } catch {
      // keep existing list
    }
  };


  const resetImportCsvUi = () => {
    setCsvInspect(null);
    setImportColumnMapping({ ...EMPTY_CSV_MAPPING });
    setImportPreview(null);
    setImportConflictStrategy("");
    setImportFile(null);
    setImportCsvSession((k) => k + 1);
  };

  const doImportCsv = async (strategy: "merge" | "use_old" | "use_new" = "merge") => {
    if (!importFile) return;
    if (!csvInspect) {
      setError("Load the CSV file and wait for column detection to finish.");
      return;
    }
    if (!importMappingIsValid(importColumnMapping, csvInspect.headers ?? [])) {
      setError("Map Name and State, and either PLSS / Location or Township, Range, and Section to your CSV columns.");
      return;
    }
    setImportLoading(true);
    setError(null);
    setImportPreview(null);
    try {
      const form = new FormData();
      form.set("file", importFile);
      form.set("column_mapping", JSON.stringify(importColumnMapping));
      form.set("conflict_strategy", strategy);
      if (importBulkPriority) form.set("bulk_priority", importBulkPriority);
      if (importBulkReportUrl.trim()) form.set("bulk_report_url", importBulkReportUrl.trim());
      if (importBulkMineral.trim()) form.set("bulk_mineral", importBulkMineral.trim());

      console.log("[CSV Import] sending request", { strategy, mapping: importColumnMapping, file: importFile.name });
      const r = (await api.areas.importCsv(form)) as Record<string, unknown>;
      console.log("[CSV Import] server response", r);

      const applied = Number(r.applied ?? 0);
      const merged = Number(r.merged ?? 0);
      const skipped = Number(r.skipped ?? 0);
      const errors = (r.errors as string[]) ?? [];
      const skipReasons = (r.skip_reasons as string[]) ?? [];
      const appliedNames = (r.applied_names as string[]) ?? [];
      const mergedNames = (r.merged_names as string[]) ?? [];

      if (applied > 0 || merged > 0) {
        setImportResult({ applied, merged, skipped, errors, applied_names: appliedNames, merged_names: mergedNames });
        resetImportCsvUi();
        setAddTargetsModalOpen(false);
        setImportSuccessBanner({ applied, merged, skipped });
        setTimeout(() => setImportSuccessBanner(null), 6000);
        void load();
        void refreshMineralSuggestions();
      } else {
        const sourceRows = Number(r.source_row_count ?? r.valid_rows ?? 0);
        let msg = `Import failed: 0 of ${sourceRows || "?"} row(s) could be imported.`;
        if (r.message && typeof r.message === "string") msg = r.message as string;
        if (skipReasons.length > 0) {
          msg += "\n\nWhat went wrong:\n• " + skipReasons.slice(0, 12).join("\n• ");
          if (skipReasons.length > 12) msg += `\n• … and ${skipReasons.length - 12} more`;
        }
        if (errors.length > 0) {
          msg += "\n\nErrors:\n• " + errors.slice(0, 5).join("\n• ");
        }
        const debug = r.debug_first_row as Record<string, unknown> | undefined;
        if (debug) {
          msg += "\n\nDebug (first row from server):";
          msg += `\n  CSV columns: ${JSON.stringify(debug.original_keys)}`;
          msg += `\n  Values: ${JSON.stringify(debug.original_vals)}`;
          if (debug.mapped) msg += `\n  After mapping: ${JSON.stringify(debug.mapped)}`;
          msg += `\n  Parse ok: ${debug.parsed_ok}`;
          if (debug.skip_reason) msg += `\n  Skip reason: ${debug.skip_reason}`;
        }
        setError(msg);
        setImportPreview({
          preview: true,
          valid_rows: 0,
          skipped,
          skip_reasons: skipReasons,
          source_row_count: sourceRows,
          message: r.message as string | undefined,
        });
      }
    } catch (e) {
      console.error("[CSV Import] error", e);
      setError((e as Error).message);
    } finally {
      setImportLoading(false);
    }
  };

  const sendAlerts = async () => {
    setAlertSending(true);
    setError(null);
    try {
      const r = await api.alerts.sendPriorityUnpaid();
      const emailed = r.email_sent ?? r.sent;
      if (emailed) {
        alert(r.message || `Email sent for ${r.count} area(s).`);
      } else {
        setError(r.message || "Email was not sent.");
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setAlertSending(false);
    }
  };

  const statusBadge = (status?: string) => {
    const s = (status || "unknown").toLowerCase();
    const label = s.toUpperCase();
    const styles =
      s === "paid"
        ? "bg-emerald-100 text-emerald-800 border border-emerald-200"
        : s === "unpaid"
          ? "bg-red-100 text-red-800 border border-red-300"
          : "bg-slate-100 text-slate-600 border border-slate-200";
    return (
      <span className={`inline-flex px-2 py-0.5 rounded text-xs font-bold tracking-wide ${styles}`}>
        {label}
      </span>
    );
  };

  const TARGET_STATUS_LABELS: Record<string, string> = {
    monitoring_low: "Monitoring - Low Priority",
    monitoring_med: "Monitoring - Med Priority",
    monitoring_high: "Monitoring - High Priority",
    negotiation: "Negotiation",
    due_diligence: "Due Diligence",
    ownership: "Ownership",
    low: "Monitoring - Low Priority",
    medium: "Monitoring - Med Priority",
    high: "Monitoring - High Priority",
  };

  const TARGET_STATUS_COLORS: Record<string, string> = {
    monitoring_low: "bg-slate-100 text-slate-600",
    monitoring_med: "bg-amber-100 text-amber-800",
    monitoring_high: "bg-red-100 text-red-800",
    negotiation: "bg-blue-100 text-blue-800",
    due_diligence: "bg-violet-100 text-violet-800",
    ownership: "bg-emerald-100 text-emerald-800",
    low: "bg-slate-100 text-slate-600",
    medium: "bg-amber-100 text-amber-800",
    high: "bg-red-100 text-red-800",
  };

  const targetStatusBadge = (priority?: string) => {
    const key = (priority || "monitoring_low").toLowerCase();
    const label = TARGET_STATUS_LABELS[key] || key;
    const colors = TARGET_STATUS_COLORS[key] || "bg-slate-100 text-slate-600";
    return <span className={`inline-flex px-2 py-0.5 rounded text-xs font-medium whitespace-nowrap ${colors}`}>{label}</span>;
  };

  const BLM_PROD_COLORS: Record<string, string> = {
    "Lode Claim": "bg-sky-100 text-sky-800",
    "Placer Claim": "bg-amber-100 text-amber-800",
    "Mill Site": "bg-violet-100 text-violet-800",
    "Tunnel Site": "bg-teal-100 text-teal-800",
    "Patent Claim": "bg-pink-100 text-pink-800",
    "Patent": "bg-pink-100 text-pink-800",
  };
  const prodTypeBadge = (prodType: string) => {
    const colors = BLM_PROD_COLORS[prodType] || "bg-gray-100 text-gray-700";
    return (
      <span key={prodType} className={`inline-flex px-2 py-0.5 rounded text-xs font-medium ${colors}`}>
        {prodType}
      </span>
    );
  };

  const tableVisibleIds = areas.map((a) => a.id);
  const tableSelectedOnPage = tableVisibleIds.filter((id) => tableSelectedIds.has(id)).length;
  const tableAllOnPageSelected = tableVisibleIds.length > 0 && tableSelectedOnPage === tableVisibleIds.length;

  return (
    <div>
      {/* Success toast after CSV import */}
      {importSuccessBanner && (
        <div className="fixed top-4 right-4 z-[60] animate-[slideIn_0.3s_ease-out] max-w-sm">
          <div className="bg-emerald-50 border border-emerald-200 rounded-xl shadow-lg px-5 py-4 flex items-start gap-3">
            <div className="shrink-0 mt-0.5">
              <svg className="w-5 h-5 text-emerald-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
              </svg>
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-semibold text-emerald-900">Import complete</p>
              <p className="text-xs text-emerald-700 mt-0.5">
                {importSuccessBanner.applied > 0 && <>{importSuccessBanner.applied} added</>}
                {importSuccessBanner.applied > 0 && importSuccessBanner.merged > 0 && ", "}
                {importSuccessBanner.merged > 0 && <>{importSuccessBanner.merged} updated</>}
                {importSuccessBanner.skipped > 0 && <>, {importSuccessBanner.skipped} skipped</>}
              </p>
            </div>
            <button
              type="button"
              onClick={() => setImportSuccessBanner(null)}
              className="shrink-0 p-1 text-emerald-400 hover:text-emerald-600 rounded"
              aria-label="Dismiss"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        </div>
      )}

      <div className="flex flex-wrap items-center justify-between gap-4 mb-6">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 mb-2">Targets</h1>
          <p className="text-slate-600">Claims and mines with location, minerals, status, and report links.</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => {
              resetImportCsvUi();
              setAddTargetsModalOpen(true);
            }}
            className="px-4 py-2 bg-slate-700 text-white rounded-lg text-sm font-medium hover:bg-slate-800"
          >
            Add Targets
          </button>
          <button
            type="button"
            onClick={async () => {
              setCleanModalOpen(true);
              setCleanLoading(true);
              setCleanData(null);
              setCleanKeepIdPerGroup({});
              setCleanNoPlssSelected(new Set());
              setCleanAiBanner(null);
              setCleanAiFailures(null);
              setCleanAiOutcome(null);
              setCleanCoordsBanner(null);
              setCleanModalTab("no_plss");
              setError(null);
              try {
                const data = await api.areas.cleanPreview();
                setCleanData(data);
                const initial: Record<string, number> = {};
                data.duplicates.forEach((g) => {
                  if (g.targets.length > 0) initial[g.plss_normalized] = g.targets[0].id;
                });
                setCleanKeepIdPerGroup(initial);
              } catch (e) {
                const msg = e instanceof Error ? e.message : "Failed to load clean preview";
                setError(typeof msg === "string" ? msg : JSON.stringify(msg));
              } finally {
                setCleanLoading(false);
              }
            }}
            className="px-4 py-2 bg-slate-600 text-white rounded-lg text-sm font-medium hover:bg-slate-700"
          >
            Clean Targets
          </button>
          <button
            onClick={sendAlerts}
            disabled={alertSending}
            className="px-4 py-2 bg-primary-600 text-white rounded-lg text-sm font-medium hover:bg-primary-700 disabled:opacity-50"
          >
            {alertSending ? "Sending…" : "Email priority unpaid"}
          </button>
        </div>
      </div>

      {/* Add Targets modal — Upload CSV only */}
      {addTargetsModalOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50"
          onClick={() => {
            if (importLoading) return;
            resetImportCsvUi();
            setAddTargetsModalOpen(false);
          }}
          role="dialog"
          aria-modal="true"
          aria-labelledby="add-targets-modal-title"
        >
          <div
            className={`bg-white rounded-2xl shadow-xl w-full max-h-[90vh] overflow-hidden flex flex-col relative ${csvInspect ? "max-w-5xl" : "max-w-2xl"}`}
            onClick={(e) => e.stopPropagation()}
          >
            {importLoading && (
              <div className="absolute inset-0 z-20 flex flex-col items-center justify-center gap-3 bg-white/85 backdrop-blur-[1px] rounded-2xl">
                <div className="animate-spin w-10 h-10 border-4 border-primary-200 border-t-primary-600 rounded-full" />
                <p className="text-sm font-medium text-slate-700">Importing targets…</p>
                <p className="text-xs text-slate-500">This may take a moment for large files.</p>
              </div>
            )}
            <div className="flex items-center justify-between px-6 py-4 border-b border-slate-200 shrink-0">
              <h2 id="add-targets-modal-title" className="text-xl font-bold text-slate-900">
                Add Targets
              </h2>
              <button
                type="button"
                disabled={importLoading}
                onClick={() => {
                  resetImportCsvUi();
                  setAddTargetsModalOpen(false);
                }}
                className="p-2 text-slate-400 hover:text-slate-600 rounded-lg hover:bg-slate-100 disabled:opacity-30 disabled:cursor-not-allowed"
                aria-label="Close"
              >
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
            <div className="px-6 py-4 overflow-y-auto flex-1">
              <div className="flex gap-2 mb-4">
                <button
                  type="button"
                  onClick={() => setAddTargetMode("csv")}
                  className={`px-3 py-2 rounded-lg text-sm font-medium ${addTargetMode === "csv" ? "bg-slate-700 text-white" : "bg-slate-100 text-slate-600 hover:bg-slate-200"}`}
                >
                  Upload CSV
                </button>
                <button
                  type="button"
                  onClick={() => setAddTargetMode("single")}
                  className={`px-3 py-2 rounded-lg text-sm font-medium ${addTargetMode === "single" ? "bg-slate-700 text-white" : "bg-slate-100 text-slate-600 hover:bg-slate-200"}`}
                >
                  Add single target
                </button>
              </div>

              {addTargetMode === "csv" && (
                <>
                  {/* Step 1: Pick file */}
                  {!csvInspect && (
                    <div className="flex flex-col items-center justify-center py-10 px-6 border-2 border-dashed border-slate-300 rounded-xl bg-slate-50/50">
                      <svg className="w-10 h-10 text-slate-400 mb-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 16V4m0 0l-4 4m4-4l4 4M4 20h16" />
                      </svg>
                      <p className="text-sm text-slate-600 mb-1 font-medium">Upload a CSV file</p>
                      <p className="text-xs text-slate-500 mb-4 text-center max-w-sm">
                        We'll preview the columns and let you map them to our fields before importing.
                      </p>
                      <label className="px-4 py-2 bg-slate-700 text-white rounded-lg text-sm font-medium hover:bg-slate-800 cursor-pointer">
                        Choose file
                        <input
                          key={importCsvSession}
                          type="file"
                          accept=".csv"
                          className="hidden"
                          onChange={async (e) => {
                            const f = e.target.files?.[0] ?? null;
                            if (!f) return;
                            setImportFile(f);
                            setError(null);
                            try {
                              const text = await f.text();
                              const result = parseCsvForPreview(text);
                              if (result.headers.length === 0) throw new Error("No header row found in CSV");
                              setCsvInspect(result);
                              setImportColumnMapping({ ...EMPTY_CSV_MAPPING, ...result.suggested_mapping });
                            } catch (err) {
                              setError(err instanceof Error ? err.message : "Could not read CSV");
                              setCsvInspect(null);
                            }
                          }}
                        />
                      </label>
                    </div>
                  )}

                  {/* Step 2: Preview + column mapping */}
                  {csvInspect && csvInspect.headers.length > 0 && (
                    <div className="space-y-5">
                      {/* File stats bar */}
                      <div className="flex flex-wrap items-center gap-4 px-4 py-3 bg-slate-50 rounded-lg border border-slate-200">
                        <div className="flex items-center gap-2 text-sm text-slate-700">
                          <svg className="w-4 h-4 text-emerald-500 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                          </svg>
                          <span className="font-medium">{importFile?.name}</span>
                        </div>
                        <span className="text-sm text-slate-600">
                          <strong>{csvInspect.total_rows}</strong> row{csvInspect.total_rows !== 1 ? "s" : ""}
                        </span>
                        <span className="text-sm text-slate-600">
                          <strong>{csvInspect.headers.length}</strong> column{csvInspect.headers.length !== 1 ? "s" : ""}
                        </span>
                        <button
                          type="button"
                          onClick={() => resetImportCsvUi()}
                          className="ml-auto text-xs text-slate-500 hover:text-slate-700 underline"
                        >
                          Choose different file
                        </button>
                      </div>

                      {/* Preview table */}
                      <div>
                        <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2">
                          Preview (first {Math.min(csvInspect.sample_rows.length, 5)} of {csvInspect.total_rows} rows)
                        </h3>
                        <div className="border border-slate-200 rounded-lg overflow-x-auto max-h-56 overflow-y-auto">
                          <table className="w-full text-xs text-left">
                            <thead className="bg-slate-50 sticky top-0 z-10">
                              <tr>
                                {csvInspect.headers.map((h, hi) => {
                                  const mappedTo = Object.entries(importColumnMapping).find(([, v]) => v === h)?.[0];
                                  const fieldDef = mappedTo ? MAPPING_FIELDS.find((f) => f.key === mappedTo) : null;
                                  return (
                                    <th
                                      key={`h-${hi}`}
                                      className={`py-2 px-2 font-semibold whitespace-nowrap border-b border-slate-200 ${
                                        fieldDef ? "text-primary-700 bg-primary-50/60" : "text-slate-600"
                                      }`}
                                    >
                                      <div>{h}</div>
                                      {fieldDef && (
                                        <div className="font-normal text-[10px] text-primary-500 mt-0.5">
                                          {fieldDef.label}
                                        </div>
                                      )}
                                    </th>
                                  );
                                })}
                              </tr>
                            </thead>
                            <tbody>
                              {csvInspect.sample_rows.map((row, ri) => (
                                <tr key={ri} className="border-b border-slate-100 last:border-0">
                                  {csvInspect.headers.map((h, hi) => (
                                    <td
                                      key={`c-${ri}-${hi}`}
                                      className="py-1.5 px-2 text-slate-700 max-w-[14rem] truncate"
                                      title={row[h] ?? ""}
                                    >
                                      {row[h] ?? ""}
                                    </td>
                                  ))}
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      </div>

                      {/* Column mapping */}
                      <div>
                        <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-1">
                          Map your columns to our fields
                        </h3>
                        <p className="text-xs text-slate-500 mb-3">
                          We auto-detected matches (highlighted above). Adjust any that are wrong. Need either <strong>PLSS / Location</strong> or all three of <strong>Township + Range + Section</strong>.
                        </p>
                        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-x-4 gap-y-2">
                          {MAPPING_FIELDS.map(({ key, label, required }) => {
                            const current = importColumnMapping[key] ?? "";
                            const isMapped = !!current && csvInspect.headers.includes(current);
                            return (
                              <label key={key} className="flex items-center gap-2 text-sm">
                                <span className={`w-[8.5rem] shrink-0 text-xs ${isMapped ? "text-primary-700 font-semibold" : "text-slate-600"}`}>
                                  {label}
                                  {required ? <span className="text-red-500"> *</span> : null}
                                </span>
                                <select
                                  value={current}
                                  onChange={(e) =>
                                    setImportColumnMapping((prev) => ({ ...prev, [key]: e.target.value }))
                                  }
                                  className={`flex-1 min-w-0 px-2 py-1.5 border rounded-lg text-sm bg-white ${
                                    isMapped ? "border-primary-300 ring-1 ring-primary-200" : "border-slate-200"
                                  }`}
                                >
                                  <option value="">— Not mapped —</option>
                                  {csvInspect.headers.map((h, hi) => (
                                    <option key={`opt-${hi}`} value={h}>
                                      {h}
                                    </option>
                                  ))}
                                </select>
                              </label>
                            );
                          })}
                        </div>
                      </div>

                      {/* Bulk options */}
                      <div>
                        <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2">Import options</h3>
                        <div className="flex flex-wrap items-end gap-3">
                          <label className="flex flex-col gap-1">
                            <span className="text-xs text-slate-500">Target Status</span>
                            <select
                              value={importBulkPriority}
                              onChange={(e) => setImportBulkPriority(e.target.value)}
                              className="px-3 py-2 border border-slate-200 rounded-lg text-sm w-48"
                            >
                              <option value="monitoring_low">Monitoring - Low Priority</option>
                              <option value="monitoring_med">Monitoring - Med Priority</option>
                              <option value="monitoring_high">Monitoring - High Priority</option>
                              <option value="negotiation">Negotiation</option>
                              <option value="due_diligence">Due Diligence</option>
                              <option value="ownership">Ownership</option>
                            </select>
                          </label>
                          <label className="flex flex-col gap-1 min-w-[140px]">
                            <span className="text-xs text-slate-500">Mineral (optional)</span>
                            <select
                              value={importBulkMineral}
                              onChange={(e) => setImportBulkMineral(e.target.value)}
                              className="px-3 py-2 border border-slate-200 rounded-lg text-sm"
                            >
                              <option value="">None</option>
                              {mineralSuggestions.map((m) => (
                                <option key={m} value={m}>{m}</option>
                              ))}
                            </select>
                          </label>
                          <label className="flex flex-col gap-1 min-w-[180px]">
                            <span className="text-xs text-slate-500">Report URL (optional)</span>
                            <input
                              type="url"
                              value={importBulkReportUrl}
                              onChange={(e) => setImportBulkReportUrl(e.target.value)}
                              placeholder="https://..."
                              className="px-3 py-2 border border-slate-200 rounded-lg text-sm"
                            />
                          </label>
                        </div>
                      </div>

                      {/* Import button */}
                      <div className="flex items-center gap-3 pt-1">
                        <button
                          type="button"
                          onClick={() => doImportCsv()}
                          disabled={
                            importLoading ||
                            !importFile ||
                            !importMappingIsValid(importColumnMapping, csvInspect.headers)
                          }
                          className="px-5 py-2.5 bg-primary-600 text-white rounded-lg text-sm font-semibold hover:bg-primary-700 disabled:opacity-50 disabled:cursor-not-allowed"
                        >
                          {importLoading ? (
                            <span className="flex items-center gap-2">
                              <span className="animate-spin w-4 h-4 border-2 border-white/40 border-t-white rounded-full" />
                              Importing…
                            </span>
                          ) : (
                            `Import ${csvInspect.total_rows} row${csvInspect.total_rows !== 1 ? "s" : ""}`
                          )}
                        </button>
                        {!importMappingIsValid(importColumnMapping, csvInspect.headers) && (
                          <p className="text-xs text-amber-600">
                            Map <strong>Name</strong>, <strong>State</strong>, and a location column to enable import.
                          </p>
                        )}
                      </div>

                      {/* Server import check — always visible in the modal after you click Import */}
                      {importPreview && importPreview.applied === undefined && importPreview.merged === undefined && (
                        <div
                          className={`mt-4 rounded-xl border-2 p-4 space-y-3 ${
                            Number(importPreview.valid_rows ?? 0) === 0
                              ? "border-red-300 bg-red-50/90"
                              : (importPreview.conflicts?.length ?? 0) > 0
                                ? "border-amber-300 bg-amber-50/90"
                                : "border-emerald-200 bg-emerald-50/80"
                          }`}
                          role="status"
                          aria-live="polite"
                        >
                          <div className="flex flex-wrap items-baseline justify-between gap-2">
                            <h3 className="text-sm font-bold text-slate-900">Import check</h3>
                            <span className="text-xs font-mono text-slate-600">
                              {Number(importPreview.valid_rows ?? 0)} ok · {Number(importPreview.skipped ?? 0)} skipped
                              {importPreview.source_row_count != null
                                ? ` · ${importPreview.source_row_count} row(s) in file`
                                : ""}
                            </span>
                          </div>
                          {importPreview.message && (
                            <p className="text-sm text-slate-800 leading-relaxed">{importPreview.message}</p>
                          )}
                          {Number(importPreview.valid_rows ?? 0) === 0 && (
                            <div className="text-xs text-slate-700 space-y-2 border-t border-red-200/80 pt-3">
                              <p className="font-semibold text-red-900">Why rows fail</p>
                              <p>
                                Each row needs a <strong>Name</strong>, <strong>State</strong> (2-letter), and a location: either a
                                single <strong>PLSS / Location</strong> column our parser can read (township, range, and section),
                                or separate Township, Range, and Section columns. Column names in your file are matched
                                case-insensitively.
                              </p>
                            </div>
                          )}
                          {(importPreview.skip_reasons?.length ?? 0) > 0 && (
                            <div>
                              <p className="text-xs font-semibold text-slate-800 mb-1">Details (per row)</p>
                              <ul className="list-disc list-inside space-y-1 text-xs text-slate-800 max-h-40 overflow-y-auto">
                                {importPreview.skip_reasons!.slice(0, 15).map((reason, i) => (
                                  <li key={i}>{reason}</li>
                                ))}
                                {importPreview.skip_reasons!.length > 15 && (
                                  <li className="text-slate-600">… and {importPreview.skip_reasons!.length - 15} more</li>
                                )}
                              </ul>
                            </div>
                          )}
                        </div>
                      )}

                      {/* Conflict handling (returned from server preview) */}
                      {(importPreview?.conflicts?.length ?? 0) > 0 && (
                        <div className="p-3 bg-amber-50 border border-amber-200 rounded-lg text-sm">
                          <p className="font-medium text-amber-900">
                            {importPreview!.conflicts!.length} target(s) already exist with the same PLSS. How to handle?
                          </p>
                          <div className="flex flex-wrap items-center gap-3 mt-2">
                            <select
                              value={importConflictStrategy}
                              onChange={(e) => setImportConflictStrategy(e.target.value as "merge" | "use_old" | "use_new")}
                              className="px-3 py-2 border border-slate-200 rounded-lg text-sm"
                            >
                              <option value="">Choose…</option>
                              <option value="merge">Merge (add minerals/reports to existing)</option>
                              <option value="use_old">Use old (skip these rows)</option>
                              <option value="use_new">Use new (overwrite with CSV data)</option>
                            </select>
                            <button
                              type="button"
                              onClick={() => importConflictStrategy && doImportCsv(importConflictStrategy)}
                              disabled={!importConflictStrategy || importLoading}
                              className="px-3 py-2 bg-amber-600 text-white rounded-lg text-sm font-medium hover:bg-amber-700 disabled:opacity-50"
                            >
                              Confirm import
                            </button>
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </>
              )}

              {addTargetMode === "single" && (
                <form
                  className="space-y-4"
                  onSubmit={async (e) => {
                    e.preventDefault();
                    const name = singleName.trim();
                    const state = singleState.trim();
                    const township = singleTownship.trim();
                    const rangeVal = singleRange.trim();
                    const section = singleSection.trim();
                    const latN = parseFloat(singleLatitude.trim());
                    const lonN = parseFloat(singleLongitude.trim());
                    const hasPlss = !!(state && township && rangeVal && section);
                    const hasCoords = Number.isFinite(latN) && Number.isFinite(lonN);
                    if (!name || (!hasPlss && !hasCoords)) return;
                    setSingleSaving(true);
                    setError(null);
                    try {
                      await api.areas.create({
                        name,
                        ...(hasPlss ? { location_plss: `${state} ${township} ${rangeVal} ${section}`.trim() } : {}),
                        ...(hasCoords ? { latitude: latN, longitude: lonN } : {}),
                        report_url: singleReportUrl.trim() || undefined,
                        information: singleInformation.trim() || undefined,
                        status: singleStatus || undefined,
                        minerals: singleMinerals ? singleMinerals.split(/[,;]+/).map((m) => m.trim()).filter(Boolean) : undefined,
                        priority: singlePriority || "monitoring_low",
                      });
                      setAddTargetsModalOpen(false);
                      setSingleName("");
                      setSingleState("");
                      setSingleTownship("");
                      setSingleRange("");
                      setSingleSection("");
                      setSingleLatitude("");
                      setSingleLongitude("");
                      setSingleReportUrl("");
                      setSingleInformation("");
                      setSingleStatus("");
                      setSingleMinerals("");
                      setSinglePriority("monitoring_low");
                      load();
                      refreshMineralSuggestions();
                    } catch (err) {
                      setError(err instanceof Error ? err.message : "Failed to add target");
                    } finally {
                      setSingleSaving(false);
                    }
                  }}
                >
                  <p className="text-xs text-slate-600">
                    Add a <strong>Name</strong> plus either full <strong>PLSS</strong> (State, Township, Range, Section) or decimal{" "}
                    <strong>latitude / longitude</strong> (WGS84). You can provide both. Coordinates-only targets can get PLSS later
                    from the detail panel.
                  </p>
                  <label className="block">
                    <span className="text-xs text-slate-500">Name <span className="text-red-500">*</span></span>
                    <input
                      type="text"
                      value={singleName}
                      onChange={(e) => setSingleName(e.target.value)}
                      placeholder="Target or claim name"
                      className="mt-1 w-full px-3 py-2 border border-slate-200 rounded-lg text-sm"
                    />
                  </label>
                  <label className="block">
                    <span className="text-xs text-slate-500">State (for PLSS)</span>
                    <select
                      value={singleState}
                      onChange={(e) => setSingleState(e.target.value)}
                      className="mt-1 w-full px-3 py-2 border border-slate-200 rounded-lg text-sm"
                    >
                      <option value="">—</option>
                      {US_STATES.map((st) => (
                        <option key={st} value={st}>{st}</option>
                      ))}
                    </select>
                  </label>
                  <div className="grid grid-cols-3 gap-3">
                    <label className="block">
                      <span className="text-xs text-slate-500">T (Township)</span>
                      <input
                        type="text"
                        value={singleTownship}
                        onChange={(e) => setSingleTownship(e.target.value)}
                        placeholder="e.g. 12N, 5S"
                        className="mt-1 w-full px-3 py-2 border border-slate-200 rounded-lg text-sm"
                      />
                    </label>
                    <label className="block">
                      <span className="text-xs text-slate-500">R (Range)</span>
                      <input
                        type="text"
                        value={singleRange}
                        onChange={(e) => setSingleRange(e.target.value)}
                        placeholder="e.g. 14E, 2W"
                        className="mt-1 w-full px-3 py-2 border border-slate-200 rounded-lg text-sm"
                      />
                    </label>
                    <label className="block">
                      <span className="text-xs text-slate-500">Sector (Section)</span>
                      <input
                        type="text"
                        value={singleSection}
                        onChange={(e) => setSingleSection(e.target.value)}
                        placeholder="1–36"
                        className="mt-1 w-full px-3 py-2 border border-slate-200 rounded-lg text-sm"
                      />
                    </label>
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    <label className="block">
                      <span className="text-xs text-slate-500">Latitude (WGS84)</span>
                      <input
                        type="text"
                        inputMode="decimal"
                        value={singleLatitude}
                        onChange={(e) => setSingleLatitude(e.target.value)}
                        placeholder="e.g. 40.1234"
                        className="mt-1 w-full px-3 py-2 border border-slate-200 rounded-lg text-sm"
                      />
                    </label>
                    <label className="block">
                      <span className="text-xs text-slate-500">Longitude (WGS84)</span>
                      <input
                        type="text"
                        inputMode="decimal"
                        value={singleLongitude}
                        onChange={(e) => setSingleLongitude(e.target.value)}
                        placeholder="e.g. -112.4567"
                        className="mt-1 w-full px-3 py-2 border border-slate-200 rounded-lg text-sm"
                      />
                    </label>
                  </div>
                  <label className="block">
                    <span className="text-xs text-slate-500">Report URL (optional)</span>
                    <input
                      type="url"
                      value={singleReportUrl}
                      onChange={(e) => setSingleReportUrl(e.target.value)}
                      placeholder="https://..."
                      className="mt-1 w-full px-3 py-2 border border-slate-200 rounded-lg text-sm"
                    />
                  </label>
                  <label className="block">
                    <span className="text-xs text-slate-500">Information (optional)</span>
                    <textarea
                      value={singleInformation}
                      onChange={(e) => setSingleInformation(e.target.value)}
                      placeholder="Notes or description"
                      rows={2}
                      className="mt-1 w-full px-3 py-2 border border-slate-200 rounded-lg text-sm resize-none"
                    />
                  </label>
                  <div className="flex flex-wrap gap-4">
                    <label className="block min-w-[120px]">
                      <span className="text-xs text-slate-500">Claim Status (optional)</span>
                      <select
                        value={singleStatus}
                        onChange={(e) => setSingleStatus(e.target.value)}
                        className="mt-1 w-full px-3 py-2 border border-slate-200 rounded-lg text-sm"
                      >
                        <option value="">—</option>
                        <option value="paid">Paid</option>
                        <option value="unpaid">Unpaid</option>
                        <option value="unknown">Unknown</option>
                      </select>
                    </label>
                    <label className="block flex-1 min-w-[140px]">
                      <span className="text-xs text-slate-500">Minerals (optional)</span>
                      <input
                        type="text"
                        value={singleMinerals}
                        onChange={(e) => setSingleMinerals(e.target.value)}
                        placeholder="e.g. Lithium, Gold"
                        className="mt-1 w-full px-3 py-2 border border-slate-200 rounded-lg text-sm"
                      />
                    </label>
                    <label className="block min-w-[180px]">
                      <span className="text-xs text-slate-500">Target Status (optional)</span>
                      <select
                        value={singlePriority}
                        onChange={(e) => setSinglePriority(e.target.value)}
                        className="mt-1 w-full px-3 py-2 border border-slate-200 rounded-lg text-sm"
                      >
                        <option value="monitoring_low">Monitoring - Low Priority</option>
                        <option value="monitoring_med">Monitoring - Med Priority</option>
                        <option value="monitoring_high">Monitoring - High Priority</option>
                        <option value="negotiation">Negotiation</option>
                        <option value="due_diligence">Due Diligence</option>
                        <option value="ownership">Ownership</option>
                      </select>
                    </label>
                  </div>
                  <button
                    type="submit"
                    disabled={
                      singleSaving ||
                      !singleName.trim() ||
                      !(
                        (singleState.trim() &&
                          singleTownship.trim() &&
                          singleRange.trim() &&
                          singleSection.trim()) ||
                        (Number.isFinite(parseFloat(singleLatitude.trim())) &&
                          Number.isFinite(parseFloat(singleLongitude.trim())))
                      )
                    }
                    className="px-4 py-2 bg-slate-700 text-white rounded-lg text-sm font-medium hover:bg-slate-800 disabled:opacity-50"
                  >
                    {singleSaving ? "Adding…" : "Add target"}
                  </button>
                </form>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Import result modal — preview of what was uploaded */}
      {importResult && (
        <div
          className="fixed inset-0 z-[60] flex items-center justify-center p-4 bg-black/50"
          role="dialog"
          aria-modal="true"
          aria-labelledby="import-result-modal-title"
        >
          <div
            className="bg-white rounded-2xl shadow-xl max-w-lg w-full max-h-[85vh] overflow-hidden flex flex-col"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between px-6 py-4 border-b border-slate-200">
              <h2 id="import-result-modal-title" className="text-xl font-bold text-slate-900">
                Import result
              </h2>
              <button
                type="button"
                onClick={() => {
                  setImportResult(null);
                  setImportFile(null);
                  setImportConflictStrategy("");
                  setImportPreview(null);
                  load();
                  refreshMineralSuggestions();
                }}
                className="p-2 text-slate-400 hover:text-slate-600 rounded-lg hover:bg-slate-100"
                aria-label="Close"
              >
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
            <div className="px-6 py-4 overflow-y-auto flex-1 space-y-4">
              <div className="flex flex-wrap gap-4 text-sm">
                <span className="font-medium text-emerald-700">{importResult.applied} added</span>
                <span className="font-medium text-slate-600">{importResult.merged} updated</span>
                {importResult.skipped > 0 && (
                  <span className="text-slate-500">{importResult.skipped} skipped</span>
                )}
              </div>
              {importResult.applied_names && importResult.applied_names.length > 0 && (
                <div>
                  <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-1">Added</h3>
                  <ul className="text-sm text-slate-700 list-disc list-inside space-y-0.5 max-h-32 overflow-y-auto">
                    {importResult.applied_names.slice(0, 25).map((name, i) => (
                      <li key={i}>{name}</li>
                    ))}
                    {importResult.applied_names.length > 25 && (
                      <li className="text-slate-500">… and {importResult.applied_names.length - 25} more</li>
                    )}
                  </ul>
                </div>
              )}
              {importResult.merged_names && importResult.merged_names.length > 0 && (
                <div>
                  <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-1">Updated (merged)</h3>
                  <ul className="text-sm text-slate-700 list-disc list-inside space-y-0.5 max-h-32 overflow-y-auto">
                    {importResult.merged_names.slice(0, 25).map((name, i) => (
                      <li key={i}>{name}</li>
                    ))}
                    {importResult.merged_names.length > 25 && (
                      <li className="text-slate-500">… and {importResult.merged_names.length - 25} more</li>
                    )}
                  </ul>
                </div>
              )}
              {importResult.errors && importResult.errors.length > 0 && (
                <div className="p-3 bg-red-50 border border-red-200 rounded-lg">
                  <h3 className="text-xs font-semibold text-red-800 uppercase tracking-wide mb-1">Errors</h3>
                  <ul className="text-sm text-red-700 list-disc list-inside space-y-0.5">
                    {importResult.errors.slice(0, 10).map((err, i) => (
                      <li key={i}>{err}</li>
                    ))}
                    {importResult.errors.length > 10 && (
                      <li className="text-red-600">… and {importResult.errors.length - 10} more</li>
                    )}
                  </ul>
                </div>
              )}
            </div>
            <div className="px-6 py-4 border-t border-slate-200">
              <button
                type="button"
                onClick={() => {
                  setImportResult(null);
                  setImportFile(null);
                  setImportConflictStrategy("");
                  setImportPreview(null);
                  load();
                  refreshMineralSuggestions();
                }}
                className="w-full px-4 py-2 bg-slate-700 text-white rounded-lg text-sm font-medium hover:bg-slate-800"
              >
                Done
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Clean Targets modal */}
      {cleanModalOpen && (
        <div
          className="fixed inset-0 z-[60] flex items-center justify-center p-4 bg-black/50"
          role="dialog"
          aria-modal="true"
          aria-labelledby="clean-targets-modal-title"
        >
          <div
            className="bg-white rounded-2xl shadow-xl max-w-4xl w-full max-h-[90vh] overflow-hidden flex flex-col relative"
            onClick={(e) => e.stopPropagation()}
          >
            {(cleanAiLoading || cleanCoordsLoading) && (
              <div
                className="absolute inset-0 z-20 flex flex-col items-center justify-center gap-3 bg-white/85 backdrop-blur-[1px] rounded-2xl"
                role="status"
                aria-live="polite"
                aria-busy="true"
              >
                <div className="animate-spin w-10 h-10 border-4 border-primary-200 border-t-primary-600 rounded-full" />
                <p className="text-sm font-medium text-slate-700">
                  {cleanCoordsLoading ? "Resolving PLSS from coordinates (BLM)…" : "Finding PLSS with AI…"}
                </p>
                <p className="text-xs text-slate-500 max-w-xs text-center">
                  {cleanCoordsLoading
                    ? "Querying the national PLSS layer for each target. Large batches may take several minutes."
                    : "This can take a minute or two for many targets."}
                </p>
              </div>
            )}
            <div className="shrink-0 border-b border-slate-200">
              <div className="flex items-center justify-between px-6 py-4">
                <h2 id="clean-targets-modal-title" className="text-xl font-bold text-slate-900">
                  Clean Targets
                </h2>
                <button
                  type="button"
                  disabled={cleanAiLoading || cleanCoordsLoading}
                  onClick={() => {
                    setCleanModalOpen(false);
                    setCleanData(null);
                    setCleanModalTab("no_plss");
                    setCleanCoordsBanner(null);
                    load();
                    refreshMineralSuggestions();
                  }}
                  className="p-2 text-slate-400 hover:text-slate-600 rounded-lg hover:bg-slate-100 disabled:opacity-40 disabled:pointer-events-none"
                  aria-label="Close"
                >
                  <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>
              {!cleanLoading && cleanData && (
                <div className="flex gap-2 px-6 pb-3 overflow-x-auto" role="tablist" aria-label="Clean targets views">
                  <button
                    type="button"
                    role="tab"
                    aria-selected={cleanModalTab === "no_plss"}
                    id="clean-tab-no-plss"
                    onClick={() => setCleanModalTab("no_plss")}
                    className={`px-3 py-1.5 text-sm font-medium rounded-lg whitespace-nowrap border transition-colors ${
                      cleanModalTab === "no_plss"
                        ? "bg-primary-600 text-white border-primary-600"
                        : "bg-white text-slate-700 border-slate-200 hover:bg-slate-50"
                    }`}
                  >
                    Targets with no PLSS ({cleanData.no_plss.length})
                  </button>
                  <button
                    type="button"
                    role="tab"
                    aria-selected={cleanModalTab === "duplicates"}
                    id="clean-tab-duplicates"
                    onClick={() => setCleanModalTab("duplicates")}
                    className={`px-3 py-1.5 text-sm font-medium rounded-lg whitespace-nowrap border transition-colors ${
                      cleanModalTab === "duplicates"
                        ? "bg-primary-600 text-white border-primary-600"
                        : "bg-white text-slate-700 border-slate-200 hover:bg-slate-50"
                    }`}
                  >
                    Targets with duplicate PLSS ({cleanData.duplicates.length})
                  </button>
                </div>
              )}
            </div>
            {/* no_plss toolbar + banners — pinned outside the scroll area */}
            {!cleanLoading && cleanData && cleanModalTab === "no_plss" && cleanData.no_plss.length > 0 && (
              <div className="shrink-0 px-6 py-3 border-b border-slate-200 bg-white space-y-2">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h3 className="text-sm font-semibold text-slate-700">Targets with no PLSS</h3>
                  <div className="flex flex-wrap items-center gap-2">
                    <button
                      type="button"
                      onClick={() => setCleanNoPlssSelected(new Set(cleanData.no_plss.map((x) => x.id)))}
                      className="px-2 py-1 text-xs font-medium text-slate-700 bg-slate-100 rounded-lg hover:bg-slate-200"
                    >
                      Select all
                    </button>
                    <button
                      type="button"
                      onClick={() => setCleanNoPlssSelected(new Set())}
                      className="px-2 py-1 text-xs font-medium text-slate-600 bg-white border border-slate-200 rounded-lg hover:bg-slate-50"
                    >
                      Clear selection
                    </button>
                    <button
                      type="button"
                      disabled={cleanAiLoading || cleanCoordsLoading || cleanNoPlssSelected.size === 0}
                      onClick={async () => {
                        const ids = Array.from(cleanNoPlssSelected);
                        if (ids.length === 0) return;
                        setCleanAiLoading(true);
                        setCleanAiBanner(null);
                        setCleanAiFailures(null);
                        setCleanAiOutcome(null);
                        setError(null);
                        try {
                          const res = await api.areas.fillPlssAiPreview(ids);
                          if (!res.ok && res.error) {
                            setError(res.error);
                          } else {
                            const rows = res.results ?? [];
                            const pending = rows.filter((r) => r.pending_apply);
                            const edits: Record<number, string> = {};
                            for (const r of pending) {
                              if (r.plss) edits[r.id] = r.plss;
                            }
                            setPlssAiReviewModal({
                              message: res.message ?? "",
                              results: rows,
                              applyIds: new Set(pending.map((r) => r.id)),
                              plssEdits: edits,
                            });
                          }
                        } catch (e) {
                          setError(e instanceof Error ? e.message : "AI PLSS preview failed");
                        } finally {
                          setCleanAiLoading(false);
                        }
                      }}
                      className="px-3 py-1.5 text-xs font-medium text-white bg-primary-600 rounded-lg hover:bg-primary-700 disabled:opacity-50"
                    >
                      {cleanAiLoading ? "Searching…" : `Find PLSS with AI (${cleanNoPlssSelected.size} selected)`}
                    </button>
                    <p className="text-xs text-slate-500 w-full basis-full">
                      Find PLSS with AI runs web search + OpenAI, then opens a review screen — nothing is saved until you click Apply (max 40 targets per run).
                    </p>
                    <button
                      type="button"
                      disabled={cleanAiLoading || cleanCoordsLoading}
                      onClick={async () => {
                        const inList = cleanData.no_plss.filter(areaHasFiniteCoords).length;
                        if (
                          !confirm(
                            "Resolve PLSS from latitude/longitude (BLM Cadastral) for every target that has coordinates but no normalized PLSS? " +
                              `This runs on the full database, not only the ${cleanData.no_plss.length} row(s) in this list. ` +
                              `${inList} row(s) shown here have coordinates.`
                          )
                        )
                          return;
                        setCleanCoordsLoading(true);
                        setCleanCoordsBanner(null);
                        setError(null);
                        try {
                          const res = await api.areas.plssFromCoordinatesBatch();
                          const fail = res.total - res.updated;
                          setCleanCoordsBanner(
                            `Updated ${res.updated} of ${res.total} target(s) from coordinates.` +
                              (fail ? ` ${fail} failed, skipped, or had no PLSS match.` : "")
                          );
                          const data = await api.areas.cleanPreview();
                          setCleanData(data);
                          load();
                        } catch (e) {
                          setError(e instanceof Error ? e.message : "Batch PLSS from coordinates failed");
                        } finally {
                          setCleanCoordsLoading(false);
                        }
                      }}
                      className="px-3 py-1.5 text-xs font-medium text-white bg-slate-700 rounded-lg hover:bg-slate-800 disabled:opacity-50"
                    >
                      {cleanCoordsLoading ? "BLM lookup…" : "Fill PLSS from coordinates (batch)"}
                    </button>
                  </div>
                </div>
                {cleanCoordsBanner && (
                  <div className="text-sm rounded-lg px-3 py-2 border border-slate-200 bg-slate-50 text-slate-800">
                    <p className="whitespace-pre-wrap">{cleanCoordsBanner}</p>
                  </div>
                )}
                {cleanAiBanner && (
                  <div
                    className={`text-sm rounded-lg px-3 py-2 border ${
                      cleanAiOutcome && cleanAiOutcome.ok === 0 && cleanAiOutcome.fail > 0
                        ? "text-amber-950 bg-amber-50 border-amber-200"
                        : "text-emerald-800 bg-emerald-50 border-emerald-200"
                    }`}
                  >
                    <p className="whitespace-pre-wrap">{cleanAiBanner}</p>
                    {cleanAiFailures && cleanAiFailures.length > 0 && (
                      <details className="mt-2 text-xs text-slate-700">
                        <summary className="cursor-pointer font-medium text-slate-800 select-none">
                          Sample failures ({cleanAiFailures.length}
                          {cleanAiOutcome && cleanAiOutcome.fail > cleanAiFailures.length
                            ? ` of ${cleanAiOutcome.fail}`
                            : ""}
                          )
                        </summary>
                        <ul className="mt-2 space-y-1 max-h-48 overflow-y-auto font-mono">
                          {cleanAiFailures.map((f) => (
                            <li key={f.id}>
                              <span className="text-slate-500">#{f.id}</span>
                              {f.kind ? <span className="text-slate-400"> [{f.kind}]</span> : null}{" "}
                              {f.error || "—"}
                            </li>
                          ))}
                        </ul>
                      </details>
                    )}
                  </div>
                )}
              </div>
            )}
            <div className="px-6 py-4 overflow-y-auto flex-1 min-h-0 space-y-6">
              {cleanLoading && (
                <div className="py-12 flex flex-col items-center justify-center gap-3 text-slate-600" role="status" aria-busy="true" aria-live="polite">
                  <div className="animate-spin w-10 h-10 border-4 border-slate-200 border-t-primary-600 rounded-full" />
                  <p className="text-sm font-medium">Loading Clean Targets…</p>
                  <p className="text-xs text-slate-500">Fetching targets with no PLSS and duplicate groups.</p>
                </div>
              )}
              {!cleanLoading && cleanData && cleanModalTab === "no_plss" && (
                  <section>
                    {cleanData.no_plss.length === 0 ? (
                      <p className="text-slate-500 text-sm">No targets missing PLSS.</p>
                    ) : (
                      <div className="border border-slate-200 rounded-lg overflow-hidden">
                        <table className="w-full text-sm">
                          <thead className="sticky top-0 z-10">
                            <tr className="bg-slate-50 border-b border-slate-200">
                              <th className="w-10 py-2 px-2 text-left bg-slate-50"></th>
                              <th className="text-left py-2 px-3 font-semibold text-slate-700 bg-slate-50">Name</th>
                              <th className="text-left py-2 px-3 font-semibold text-slate-700 w-14 bg-slate-50">State</th>
                              <th className="text-left py-2 px-3 font-semibold text-slate-700 min-w-[7rem] bg-slate-50">County</th>
                              <th className="text-left py-2 px-3 font-semibold text-slate-700 bg-slate-50">Location</th>
                              <th className="text-left py-2 px-3 font-semibold text-slate-700 bg-slate-50">Minerals</th>
                              <th className="text-left py-2 px-3 font-semibold text-slate-700 bg-slate-50">Actions</th>
                            </tr>
                          </thead>
                          <tbody>
                            {cleanData.no_plss.map((a) => (
                              <tr key={a.id} className="border-b border-slate-100 last:border-0">
                                <td className="py-2 px-2">
                                  <input
                                    type="checkbox"
                                    className="rounded border-slate-300"
                                    checked={cleanNoPlssSelected.has(a.id)}
                                    onChange={(e) => {
                                      const next = new Set(cleanNoPlssSelected);
                                      if (e.target.checked) next.add(a.id);
                                      else next.delete(a.id);
                                      setCleanNoPlssSelected(next);
                                    }}
                                    aria-label={`Select ${a.name}`}
                                  />
                                </td>
                                <td className="py-2 px-3 font-medium text-slate-900">{a.name}</td>
                                <td className="py-2 px-3 text-slate-600 font-mono text-xs">{a.state_abbr || "—"}</td>
                                <td className="py-2 px-3 text-slate-600 text-xs">{a.county || "—"}</td>
                                <td className="py-2 px-3 text-slate-600">{a.location_plss || "—"}</td>
                                <td className="py-2 px-3">
                                  {(a.minerals || []).length > 0 ? (
                                    <div className="flex flex-wrap gap-1">
                                      {a.minerals!.map((m, mi) => (
                                        <span key={mi} className="inline-block px-1.5 py-0.5 bg-primary-100 text-primary-700 rounded text-xs font-medium">{m}</span>
                                      ))}
                                    </div>
                                  ) : "—"}
                                </td>
                                <td className="py-2 px-3">
                                  <div className="flex gap-2">
                                    <button
                                      type="button"
                                      onClick={async () => {
                                        if (!confirm(`Delete "${a.name}"?`)) return;
                                        setCleanDeleting(a.id);
                                        try {
                                          await api.areas.delete(a.id);
                                          setCleanData((d) => d ? { ...d, no_plss: d.no_plss.filter((x) => x.id !== a.id) } : null);
                                          load();
                                        } catch (e) {
                                          setError(e instanceof Error ? e.message : "Delete failed");
                                        } finally {
                                          setCleanDeleting(null);
                                        }
                                      }}
                                      disabled={cleanDeleting === a.id}
                                      className="text-red-600 hover:underline text-xs font-medium disabled:opacity-50"
                                    >
                                      Delete
                                    </button>
                                    <button
                                      type="button"
                                      onClick={async () => {
                                        setCleanModalOpen(false);
                                        setCleanData(null);
                                        try {
                                          const full = await api.areas.get(a.id);
                                          setSelected(full);
                                        } catch {
                                          setSelected(a);
                                        }
                                        load();
                                      }}
                                      className="text-primary-600 hover:underline text-xs font-medium"
                                    >
                                      Edit
                                    </button>
                                  </div>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </section>
              )}
              {!cleanLoading && cleanData && cleanModalTab === "duplicates" && (
                  <section aria-labelledby="clean-tab-duplicates">
                    <p className="text-xs text-slate-500 mb-3">
                      Choose which target to keep, then merge the others into it (minerals and links combined), or delete rows you do not need.
                    </p>
                    {cleanData.duplicates.length === 0 ? (
                      <p className="text-slate-500 text-sm">None.</p>
                    ) : (
                      <div className="space-y-4">
                        {cleanData.duplicates.map((group) => {
                          const rawKeep = cleanKeepIdPerGroup[group.plss_normalized] ?? group.targets[0]?.id;
                          const keepId =
                            rawKeep !== undefined && group.targets.some((t) => t.id === rawKeep)
                              ? rawKeep
                              : group.targets[0]!.id;
                          const mergeIds = group.targets.filter((t) => t.id !== keepId).map((t) => t.id);
                          return (
                            <div key={group.plss_normalized} className="border border-slate-200 rounded-lg overflow-hidden">
                              <div className="bg-slate-50 px-3 py-2 text-xs font-medium text-slate-600">
                                PLSS: {group.plss}
                              </div>
                              <table className="w-full text-sm">
                                <thead>
                                  <tr className="bg-slate-50/50 border-b border-slate-200">
                                    <th className="w-24 py-2 px-2 text-left font-semibold text-slate-700">Keep</th>
                                    <th className="text-left py-2 px-3 font-semibold text-slate-700">Name</th>
                                    <th className="text-left py-2 px-3 font-semibold text-slate-700 w-14">State</th>
                                    <th className="text-left py-2 px-3 font-semibold text-slate-700 min-w-[7rem]">County</th>
                                    <th className="text-left py-2 px-3 font-semibold text-slate-700">Location</th>
                                    <th className="text-left py-2 px-3 font-semibold text-slate-700">Minerals</th>
                                    <th className="text-left py-2 px-3 font-semibold text-slate-700">Actions</th>
                                  </tr>
                                </thead>
                                <tbody>
                                  {group.targets.map((t) => (
                                    <tr key={t.id} className="border-b border-slate-100 last:border-0">
                                      <td className="py-2 px-2">
                                        <input
                                          type="radio"
                                          name={`keep-${group.plss_normalized}`}
                                          checked={keepId === t.id}
                                          onChange={() => setCleanKeepIdPerGroup((prev) => ({ ...prev, [group.plss_normalized]: t.id }))}
                                          aria-label={`Keep ${t.name} and merge others into it`}
                                        />
                                      </td>
                                      <td className="py-2 px-3 font-medium text-slate-900">{t.name}</td>
                                      <td className="py-2 px-3 text-slate-600 font-mono text-xs">{t.state_abbr || "—"}</td>
                                      <td className="py-2 px-3 text-slate-600 text-xs">{t.county || "—"}</td>
                                      <td className="py-2 px-3 text-slate-600">{t.location_plss || "—"}</td>
                                      <td className="py-2 px-3">
                                        {(t.minerals || []).length > 0 ? (
                                          <div className="flex flex-wrap gap-1">
                                            {t.minerals!.map((m, mi) => (
                                              <span key={mi} className="inline-block px-1.5 py-0.5 bg-primary-100 text-primary-700 rounded text-xs font-medium">{m}</span>
                                            ))}
                                          </div>
                                        ) : "—"}
                                      </td>
                                      <td className="py-2 px-3">
                                        <div className="flex gap-2">
                                          <button
                                            type="button"
                                            onClick={async () => {
                                              if (
                                                !confirm(
                                                  `Delete "${t.name}"? This removes the target. Use Merge into selected if you want to combine duplicates instead.`
                                                )
                                              )
                                                return;
                                              setCleanDeleting(t.id);
                                              try {
                                                await api.areas.delete(t.id);
                                                setCleanData((d) => {
                                                  if (!d) return null;
                                                  const duplicates = d.duplicates
                                                    .map((g) => {
                                                      if (g.plss_normalized !== group.plss_normalized) return g;
                                                      return { ...g, targets: g.targets.filter((x) => x.id !== t.id) };
                                                    })
                                                    .filter((g) => g.targets.length > 1);
                                                  return { ...d, duplicates };
                                                });
                                                load();
                                              } catch (e) {
                                                setError(e instanceof Error ? e.message : "Delete failed");
                                              } finally {
                                                setCleanDeleting(null);
                                              }
                                            }}
                                            disabled={cleanDeleting === t.id}
                                            className="text-red-600 hover:underline text-xs font-medium disabled:opacity-50"
                                          >
                                            Delete
                                          </button>
                                          <button
                                            type="button"
                                            onClick={async () => {
                                              setCleanModalOpen(false);
                                              setCleanData(null);
                                              setCleanModalTab("no_plss");
                                              try {
                                                const full = await api.areas.get(t.id);
                                                setSelected(full);
                                              } catch {
                                                setSelected(t);
                                              }
                                              load();
                                            }}
                                            className="text-primary-600 hover:underline text-xs font-medium"
                                          >
                                            Edit
                                          </button>
                                        </div>
                                      </td>
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                              <div className="px-3 py-2 border-t border-slate-100 bg-slate-50/30">
                                <button
                                  type="button"
                                  onClick={async () => {
                                    if (mergeIds.length === 0) return;
                                    setCleanConsolidating(group.plss_normalized);
                                    try {
                                      await api.areas.consolidate(keepId, mergeIds);
                                      setCleanData((d) =>
                                        d ? { ...d, duplicates: d.duplicates.filter((g) => g.plss_normalized !== group.plss_normalized) } : null
                                      );
                                      load();
                                    } catch (e) {
                                      setError(e instanceof Error ? e.message : "Merge failed");
                                    } finally {
                                      setCleanConsolidating(null);
                                    }
                                  }}
                                  disabled={cleanConsolidating === group.plss_normalized || mergeIds.length === 0}
                                  className="px-3 py-1.5 bg-primary-600 text-white rounded-lg text-xs font-medium hover:bg-primary-700 disabled:opacity-50"
                                >
                                  {cleanConsolidating === group.plss_normalized ? "…" : "Merge into selected"}
                                </button>
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </section>
              )}
            </div>
          </div>
        </div>
      )}

      {error && error === "DB_SETUP" && (
        <div className="fixed top-4 left-1/2 z-[100] w-[min(92vw,36rem)] -translate-x-1/2 p-6 bg-amber-50 border border-amber-200 rounded-xl text-amber-900 text-sm shadow-lg">
          <strong>Database not running.</strong> On the <Link to="/" className="text-primary-600 underline">Dashboard</Link> see &quot;Set up the database&quot; for steps (Docker → <code>docker compose up -d</code> → <code>--init-db</code>).
        </div>
      )}
      {error && error !== "DB_SETUP" && (
        <div
          className="fixed top-4 left-1/2 z-[100] w-[min(94vw,48rem)] max-h-[min(70vh,28rem)] -translate-x-1/2 flex flex-col gap-2 p-4 bg-red-50 border border-red-200 rounded-lg text-red-800 text-sm shadow-lg overflow-hidden"
          role="alert"
        >
          <div className="flex items-start justify-between gap-3 shrink-0">
            <span className="font-medium text-red-900">Error</span>
            <div className="flex items-center gap-2 shrink-0">
              <button
                type="button"
                className="px-2 py-1 text-xs font-medium rounded-md bg-red-100 text-red-900 hover:bg-red-200"
                onClick={() => {
                  void navigator.clipboard.writeText(error);
                }}
              >
                Copy
              </button>
              <button
                type="button"
                className="p-1 text-red-600 hover:bg-red-100 rounded"
                aria-label="Dismiss error"
                onClick={() => setError(null)}
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
          </div>
          <pre className="whitespace-pre-wrap break-words overflow-y-auto text-xs leading-relaxed font-sans">{error}</pre>
        </div>
      )}

      <section className="mb-6 p-4 bg-white rounded-xl border border-slate-200 shadow-card">
        <h2 className="text-sm font-semibold text-slate-700 mb-3">Search & filter</h2>
        <div className="flex flex-wrap items-center gap-4">
          <label className="flex flex-col gap-1 relative">
            <span className="text-xs text-slate-500">Name</span>
            <input
              type="text"
              value={nameFilter}
              onChange={(e) => {
                setNameFilter(e.target.value);
                setNameDropdownOpen(true);
              }}
              onFocus={() => {
                setNameInputFocused(true);
                setNameDropdownOpen(true);
              }}
              onBlur={() => {
                setNameInputFocused(false);
                setTimeout(() => setNameDropdownOpen(false), 150);
              }}
              placeholder="Search by name"
              className="px-3 py-2 border border-slate-200 rounded-lg text-sm w-48 focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
              autoComplete="off"
            />
            {nameDropdownOpen && nameInputFocused && nameFilter.trim().length > 0 && (
              <ul
                onMouseDown={(e) => e.preventDefault()}
                className="absolute z-[100] top-full left-0 mt-0.5 w-72 max-h-72 overflow-y-auto bg-white border border-slate-200 rounded-lg shadow-lg py-1 text-sm"
                role="listbox"
              >
                {(() => {
                  const q = nameFilter.trim().toLowerCase();
                  const matched = areas
                    .filter((a) => a.name?.toLowerCase().includes(q))
                    .slice(0, 30);
                  if (matched.length === 0) {
                    return (
                      <li className="px-3 py-2 text-slate-500" role="option">
                        No matching names.
                      </li>
                    );
                  }
                  return matched.map((a) => (
                    <li
                      key={a.id}
                      role="option"
                      className="px-3 py-2 cursor-pointer hover:bg-primary-50 text-slate-800 truncate"
                      onMouseDown={(e) => {
                        e.preventDefault();
                        setNameFilter(a.name ?? "");
                        setNameDropdownOpen(false);
                      }}
                    >
                      {a.name}
                    </li>
                  ));
                })()}
              </ul>
            )}
          </label>
          <label className="flex flex-col gap-1 relative">
            <span className="text-xs text-slate-500">Mineral</span>
            <input
              type="text"
              value={mineralFilter}
              onChange={(e) => {
                setMineralFilter(e.target.value);
                setMineralDropdownOpen(true);
              }}
              onFocus={() => {
                setMineralInputFocused(true);
                setMineralDropdownOpen(true);
              }}
              onBlur={() => {
                setMineralInputFocused(false);
                setTimeout(() => setMineralDropdownOpen(false), 150);
              }}
              placeholder="e.g. Tungsten (autocomplete)"
              className="px-3 py-2 border border-slate-200 rounded-lg text-sm w-44 focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
              autoComplete="off"
            />
            {mineralDropdownOpen && mineralInputFocused && (
              <ul
                onMouseDown={(e) => e.preventDefault()}
                className="absolute z-[100] top-full left-0 mt-0.5 w-64 max-h-72 overflow-y-auto bg-white border border-slate-200 rounded-lg shadow-lg py-1 text-sm"
                role="listbox"
              >
                {(() => {
                  const q = mineralFilter.trim().toLowerCase();
                  const filtered =
                    q === ""
                      ? mineralSuggestions
                      : mineralSuggestions.filter((m) => m.toLowerCase().includes(q));
                  if (filtered.length === 0) {
                    return (
                      <li className="px-3 py-2 text-slate-500" role="option">
                        No matching minerals. Type to search.
                      </li>
                    );
                  }
                  return filtered.map((m) => (
                    <li
                      key={m}
                      role="option"
                      className="px-3 py-2 cursor-pointer hover:bg-primary-50 text-slate-800"
                      onMouseDown={(e) => {
                        e.preventDefault();
                        setMineralFilter(m);
                        setMineralDropdownOpen(false);
                      }}
                    >
                      {m}
                    </li>
                  ));
                })()}
              </ul>
            )}
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-xs text-slate-500">Target Status</span>
            <select
              value={targetStatusFilter}
              onChange={(e) => setTargetStatusFilter(e.target.value)}
              className="px-3 py-2 border border-slate-200 rounded-lg text-sm focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
            >
              <option value="">All target statuses</option>
              <option value="monitoring_high">Monitoring - High Priority</option>
              <option value="monitoring_med">Monitoring - Med Priority</option>
              <option value="monitoring_low">Monitoring - Low Priority</option>
              <option value="negotiation">Negotiation</option>
              <option value="due_diligence">Due Diligence</option>
              <option value="ownership">Ownership</option>
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-xs text-slate-500">Claim Status</span>
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className="px-3 py-2 border border-slate-200 rounded-lg text-sm focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
            >
              <option value="">All statuses</option>
              <option value="paid">Paid</option>
              <option value="unpaid">Unpaid</option>
              <option value="unknown">Unknown</option>
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-xs text-slate-500">State</span>
            <input
              type="text"
              value={stateFilter}
              onChange={(e) => setStateFilter(e.target.value)}
              placeholder="e.g. UT, NV"
              className="px-3 py-2 border border-slate-200 rounded-lg text-sm w-28 focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-xs text-slate-500">Claim type</span>
            <select
              value={claimTypeFilter}
              onChange={(e) => setClaimTypeFilter(e.target.value)}
              className="px-3 py-2 border border-slate-200 rounded-lg text-sm focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
            >
              <option value="">All</option>
              <option value="Patented">Patented</option>
              <option value="Unpatented">Unpatented</option>
              <option value="Mining claims">Mining claims</option>
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-xs text-slate-500">Retrieval type</span>
            <select
              value={retrievalTypeFilter}
              onChange={(e) => setRetrievalTypeFilter(e.target.value)}
              className="px-3 py-2 border border-slate-200 rounded-lg text-sm focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
            >
              <option value="">All</option>
              <option value="Known Mine">Known Mine</option>
              <option value="User Added">User Added</option>
            </select>
          </label>
          {(nameFilter || mineralFilter || statusFilter || targetStatusFilter || stateFilter || claimTypeFilter || retrievalTypeFilter || townshipFilter || rangeFilter || sectorFilter) && (
            <button
              type="button"
              onClick={() => {
                setNameFilter("");
                setMineralFilter("");
                setStatusFilter("");
                setTargetStatusFilter("");
                setStateFilter("");
                setClaimTypeFilter("");
                setRetrievalTypeFilter("");
                setTownshipFilter("");
                setRangeFilter("");
                setSectorFilter("");
              }}
              className="self-end px-3 py-2 text-slate-600 hover:text-slate-900 text-sm font-medium"
            >
              Clear filters
            </button>
          )}
        </div>

        <div className="mt-4 pt-4 border-t border-slate-200">
          <button
            type="button"
            onClick={() => setAdvancedOpen((o) => !o)}
            className="flex items-center gap-2 text-sm font-medium text-slate-700 hover:text-slate-900"
          >
            <span className="inline-block w-4 text-slate-500">{advancedOpen ? "▼" : "▶"}</span>
            Advanced Search (Township, Range, Sector)
          </button>
          {advancedOpen && (
            <div className="flex flex-wrap items-end gap-4 mt-3">
              <label className="flex flex-col gap-1">
                <span className="text-xs text-slate-500">Township</span>
                <input
                  type="text"
                  value={townshipFilter}
                  onChange={(e) => setTownshipFilter(e.target.value)}
                  placeholder="e.g. 12S, T12S"
                  className="px-3 py-2 border border-slate-200 rounded-lg text-sm w-28 focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
                />
              </label>
              <label className="flex flex-col gap-1">
                <span className="text-xs text-slate-500">Range</span>
                <input
                  type="text"
                  value={rangeFilter}
                  onChange={(e) => setRangeFilter(e.target.value)}
                  placeholder="e.g. 14E, R14E"
                  className="px-3 py-2 border border-slate-200 rounded-lg text-sm w-28 focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
                />
              </label>
              <label className="flex flex-col gap-1">
                <span className="text-xs text-slate-500">Sector</span>
                <input
                  type="text"
                  value={sectorFilter}
                  onChange={(e) => setSectorFilter(e.target.value)}
                  placeholder="e.g. 1–36"
                  className="px-3 py-2 border border-slate-200 rounded-lg text-sm w-24 focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
                />
              </label>
              <p className="text-xs text-slate-500 max-w-xs">
                Leave blank to ignore. Example: Range 14E shows all targets with Range 14E (any Township/Sector).
              </p>
            </div>
          )}
        </div>
      </section>

      {areas.length > 0 && tableSelectedIds.size > 0 && (
        <div className="mb-3 flex flex-wrap items-center gap-2 px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg">
          <span className="text-sm font-medium text-slate-800">{tableSelectedIds.size} selected</span>
          <button
            type="button"
            disabled={batchRunStatus !== null}
            onClick={() => setBatchControlOpen(true)}
            className="px-3 py-1.5 text-xs font-medium text-white bg-slate-800 rounded-lg hover:bg-slate-900 disabled:opacity-50"
          >
            {batchRunStatus ? "Running…" : "Batch actions…"}
          </button>
          <button
            type="button"
            disabled={batchRunStatus !== null}
            onClick={() => setTableSelectedIds(new Set())}
            className="px-3 py-1.5 text-xs font-medium text-slate-700 bg-white border border-slate-200 rounded-lg hover:bg-slate-100 disabled:opacity-50"
          >
            Clear selection
          </button>
          {batchRunStatus && (
            <span className="text-xs text-slate-600 max-w-md truncate" title={batchRunStatus}>
              {batchRunStatus}
            </span>
          )}
          <span className="text-xs text-slate-500">
            Choose actions, chunk size (1–{AREA_BATCH_MAX_CHUNK}), and optional pause in the dialog.
          </span>
        </div>
      )}

      <div className={`grid gap-6 ${selected ? "lg:grid-cols-2" : "grid-cols-1"}`}>
        <div className="bg-white rounded-xl border border-slate-200 shadow-card">
          {loading ? (
            <div className="p-8 text-center text-slate-500">Loading…</div>
          ) : areas.length === 0 ? (
            <div className="p-8 text-center text-slate-500">
              No targets. Click <strong>Ingest data files</strong> to load your CSVs.
            </div>
          ) : (
            <div className="max-h-[min(70vh,calc(100vh-14rem))] overflow-y-auto overflow-x-auto">
              <table className="w-full text-sm border-collapse">
                <thead>
                  <tr className="border-b border-slate-200">
                    <th className="sticky top-0 z-20 w-10 bg-slate-50 py-3 px-2 shadow-[0_1px_0_0_rgb(226_232_240)]">
                      <input
                        type="checkbox"
                        className="rounded border-slate-300"
                        checked={tableAllOnPageSelected}
                        ref={(el) => {
                          if (el) {
                            el.indeterminate =
                              tableSelectedOnPage > 0 && tableSelectedOnPage < tableVisibleIds.length;
                          }
                        }}
                        onChange={(e) => {
                          setTableSelectedIds((prev) => {
                            const n = new Set(prev);
                            if (e.target.checked) {
                              tableVisibleIds.forEach((id) => n.add(id));
                            } else {
                              tableVisibleIds.forEach((id) => n.delete(id));
                            }
                            return n;
                          });
                        }}
                        aria-label="Select all targets on this page"
                      />
                    </th>
                    <th className="sticky top-0 z-20 bg-slate-50 text-left py-3 px-4 font-semibold text-slate-700 shadow-[0_1px_0_0_rgb(226_232_240)]">
                      Name
                    </th>
                    <th className="sticky top-0 z-20 bg-slate-50 text-left py-3 px-4 font-semibold text-slate-700 shadow-[0_1px_0_0_rgb(226_232_240)]">
                      Location (PLSS)
                    </th>
                    <th className="sticky top-0 z-20 bg-slate-50 text-left py-3 px-4 font-semibold text-slate-700 shadow-[0_1px_0_0_rgb(226_232_240)]">
                      State
                    </th>
                    <th className="sticky top-0 z-20 bg-slate-50 text-left py-3 px-4 font-semibold text-slate-700 shadow-[0_1px_0_0_rgb(226_232_240)]">
                      Minerals
                    </th>
                    <th className="sticky top-0 z-20 bg-slate-50 text-left py-3 px-4 font-semibold text-slate-700 shadow-[0_1px_0_0_rgb(226_232_240)]">
                      Claim Status
                    </th>
                    <th className="sticky top-0 z-20 bg-slate-50 text-left py-3 px-4 font-semibold text-slate-700 shadow-[0_1px_0_0_rgb(226_232_240)]">
                      Target Status
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {areas.map((a) => (
                    <tr
                      key={a.id}
                      onClick={async () => {
                        try {
                          const full = await api.areas.get(a.id);
                          setSelected(full);
                        } catch {
                          setSelected(a);
                        }
                      }}
                      className={`border-b border-slate-100 hover:bg-primary-50/50 cursor-pointer ${selected?.id === a.id ? "bg-primary-50" : ""}`}
                    >
                      <td
                        className="py-3 px-2 align-middle"
                        onClick={(e) => e.stopPropagation()}
                        onKeyDown={(e) => e.stopPropagation()}
                      >
                        <input
                          type="checkbox"
                          className="rounded border-slate-300"
                          checked={tableSelectedIds.has(a.id)}
                          onChange={(e) => {
                            e.stopPropagation();
                            setTableSelectedIds((prev) => {
                              const n = new Set(prev);
                              if (e.target.checked) n.add(a.id);
                              else n.delete(a.id);
                              return n;
                            });
                          }}
                          onClick={(e) => e.stopPropagation()}
                          aria-label={`Select ${a.name}`}
                        />
                      </td>
                      <td className="py-3 px-4 font-medium text-slate-900">{a.name}</td>
                      <td className="py-3 px-4 text-slate-600">{a.location_plss || a.location_coords || "—"}</td>
                      <td className="py-3 px-4 text-slate-600">{a.state_abbr || "—"}</td>
                      <td className="py-3 px-4">
                        {(a.minerals || []).length > 0 ? (
                          <div className="flex flex-wrap gap-1">
                            {a.minerals!.map((m, i) => (
                              <span key={i} className="inline-block px-2 py-0.5 bg-primary-100 text-primary-700 rounded text-xs font-medium">{m}</span>
                            ))}
                          </div>
                        ) : "—"}
                      </td>
                      <td className="py-3 px-4">{statusBadge(a.status)}</td>
                      <td className="py-3 px-4">
                        {targetStatusBadge(a.priority)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {selected && (
        <div className="sticky top-20 self-start max-h-[calc(100vh-5.5rem)] flex flex-col bg-white rounded-xl border border-slate-200 shadow-card overflow-hidden">
          <div className="p-4 border-b border-slate-100 font-semibold text-slate-900 shrink-0 bg-white">Detail</div>
          <div className="p-4 overflow-y-auto flex-1 min-h-0">
              <div className="space-y-3 text-sm">
                <div>
                  <div className="flex items-center justify-between">
                    <span className="text-slate-500">Name</span>
                    {!nameEditing && (
                      <button
                        type="button"
                        onClick={() => {
                          setNameDraft(selected.name || "");
                          setNameEditing(true);
                        }}
                        className="text-xs text-primary-600 hover:underline"
                      >
                        Edit
                      </button>
                    )}
                  </div>
                  {nameEditing ? (
                    <div className="mt-1 space-y-2">
                      <input
                        type="text"
                        value={nameDraft}
                        onChange={(e) => setNameDraft(e.target.value)}
                        className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
                        placeholder="Target name"
                        maxLength={500}
                      />
                      <div className="flex gap-2">
                        <button
                          type="button"
                          disabled={nameSaving || !nameDraft.trim()}
                          onClick={async () => {
                            if (!nameDraft.trim()) return;
                            setNameSaving(true);
                            setError(null);
                            try {
                              await api.areas.updateName(selected.id, nameDraft.trim());
                              const full = await api.areas.get(selected.id);
                              setSelected(full);
                              setNameEditing(false);
                              load();
                            } catch (err) {
                              setError(err instanceof Error ? err.message : "Failed to rename target");
                            } finally {
                              setNameSaving(false);
                            }
                          }}
                          className="px-3 py-1 bg-primary-600 text-white rounded text-xs font-medium hover:bg-primary-700 disabled:opacity-50"
                        >
                          {nameSaving ? "Saving…" : "Save"}
                        </button>
                        <button
                          type="button"
                          onClick={() => setNameEditing(false)}
                          className="px-3 py-1 bg-slate-100 text-slate-600 rounded text-xs font-medium hover:bg-slate-200"
                        >
                          Cancel
                        </button>
                      </div>
                    </div>
                  ) : (
                    <span className="font-medium text-slate-900">{selected.name}</span>
                  )}
                </div>
                <div>
                  <span className="text-slate-500 block">Target Status</span>
                  <select
                    value={(selected.priority || "monitoring_low").toLowerCase()}
                    onChange={async (e) => {
                      const v = e.target.value;
                      const id = selected.id;
                      setPrioritySaving(true);
                      setError(null);
                      try {
                        await api.areas.updatePriority(id, v);
                        const list = await api.areas.list({
                          mineral: mineralFilter || undefined,
                          status: statusFilter || undefined,
                          target_status: targetStatusFilter || undefined,
                          state_abbr: stateFilter || undefined,
                          claim_type: claimTypeFilter || undefined,
                          retrieval_type: retrievalTypeFilter || undefined,
                          township: townshipFilter.trim() || undefined,
                          range_val: rangeFilter.trim() || undefined,
                          sector: sectorFilter.trim() || undefined,
                          name: nameFilter.trim() || undefined,
                          limit: AREA_LIST_LIMIT,
                        });
                        setAreas(list);
                        const updated = list.find((a) => a.id === id);
                        if (updated) setSelected(updated);
                      } catch (err) {
                        setError(err instanceof Error ? err.message : "Failed to save target status");
                      } finally {
                        setPrioritySaving(false);
                      }
                    }}
                    disabled={prioritySaving}
                    className="mt-0.5 px-3 py-2 border border-slate-200 rounded-lg w-full max-w-[14rem] focus:ring-2 focus:ring-primary-500 focus:border-primary-500 disabled:opacity-60"
                  >
                    <option value="monitoring_low">Monitoring - Low Priority</option>
                    <option value="monitoring_med">Monitoring - Med Priority</option>
                    <option value="monitoring_high">Monitoring - High Priority</option>
                    <option value="negotiation">Negotiation</option>
                    <option value="due_diligence">Due Diligence</option>
                    <option value="ownership">Ownership</option>
                  </select>
                </div>
                {selected.latitude != null &&
                  selected.longitude != null &&
                  Number.isFinite(selected.latitude) &&
                  Number.isFinite(selected.longitude) && (
                  <div className="rounded-lg overflow-hidden border border-slate-200">
                    <MapContainer
                      key={`mini-${selected.id}`}
                      center={[selected.latitude, selected.longitude]}
                      zoom={15}
                      scrollWheelZoom={false}
                      dragging={false}
                      zoomControl={false}
                      attributionControl={false}
                      doubleClickZoom={false}
                      touchZoom={false}
                      keyboard={false}
                      style={{ height: 180, width: "100%", cursor: "default" }}
                    >
                      <TileLayer url={SATELLITE_TILE} maxZoom={19} />
                      <Marker position={[selected.latitude, selected.longitude]} icon={TARGET_PIN} />
                    </MapContainer>
                    <Link
                      to={`/map?areaId=${selected.id}`}
                      className="block text-center py-1.5 bg-slate-800/70 text-white text-xs font-medium hover:bg-slate-800/90 transition-colors"
                    >
                      View in Map
                    </Link>
                  </div>
                )}
                <div>
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-slate-500 block">Coordinates (WGS84)</span>
                    {!coordsEditing && (
                      <button
                        type="button"
                        onClick={() => {
                          setCoordsLatDraft(
                            selected.latitude != null && Number.isFinite(selected.latitude) ? String(selected.latitude) : ""
                          );
                          setCoordsLonDraft(
                            selected.longitude != null && Number.isFinite(selected.longitude) ? String(selected.longitude) : ""
                          );
                          setCoordsEditing(true);
                        }}
                        className="text-xs text-primary-600 hover:underline shrink-0"
                      >
                        Edit
                      </button>
                    )}
                  </div>
                  {coordsEditing ? (
                    <div className="mt-1 space-y-2">
                      <div className="grid grid-cols-2 gap-2">
                        <label className="block">
                          <span className="text-[11px] text-slate-500">Latitude</span>
                          <input
                            type="text"
                            inputMode="decimal"
                            value={coordsLatDraft}
                            onChange={(e) => setCoordsLatDraft(e.target.value)}
                            className="mt-0.5 w-full px-2 py-1.5 border border-slate-200 rounded-lg text-sm"
                          />
                        </label>
                        <label className="block">
                          <span className="text-[11px] text-slate-500">Longitude</span>
                          <input
                            type="text"
                            inputMode="decimal"
                            value={coordsLonDraft}
                            onChange={(e) => setCoordsLonDraft(e.target.value)}
                            className="mt-0.5 w-full px-2 py-1.5 border border-slate-200 rounded-lg text-sm"
                          />
                        </label>
                      </div>
                      <div className="flex gap-2">
                        <button
                          type="button"
                          disabled={coordsSaving}
                          onClick={async () => {
                            const lat = parseFloat(coordsLatDraft.trim());
                            const lon = parseFloat(coordsLonDraft.trim());
                            if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
                              setError("Enter valid numeric latitude and longitude.");
                              return;
                            }
                            setCoordsSaving(true);
                            setError(null);
                            try {
                              await api.areas.updateCoordinates(selected.id, lat, lon);
                              const full = await api.areas.get(selected.id);
                              setSelected(full);
                              setCoordsEditing(false);
                              load();
                            } catch (err) {
                              setError(err instanceof Error ? err.message : "Failed to save coordinates");
                            } finally {
                              setCoordsSaving(false);
                            }
                          }}
                          className="px-3 py-1 bg-primary-600 text-white rounded text-xs font-medium hover:bg-primary-700 disabled:opacity-50"
                        >
                          {coordsSaving ? "Saving…" : "Save"}
                        </button>
                        <button
                          type="button"
                          onClick={() => setCoordsEditing(false)}
                          className="px-3 py-1 bg-slate-100 text-slate-600 rounded text-xs font-medium hover:bg-slate-200"
                        >
                          Cancel
                        </button>
                      </div>
                    </div>
                  ) : (
                    <span className="text-slate-700">
                      {areaHasFiniteCoords(selected)
                        ? `${selected.latitude}, ${selected.longitude}`
                        : "—"}
                    </span>
                  )}
                </div>
                {areaHasFiniteCoords(selected) && areaMissingNormalizedPlss(selected) && (
                  <button
                    type="button"
                    disabled={fillPlssFromCoordsLoading}
                    onClick={async () => {
                      if (!selected?.id) return;
                      setFillPlssFromCoordsLoading(true);
                      setError(null);
                      try {
                        const r = await api.areas.plssFromCoordinates(selected.id);
                        if (!r.ok) {
                          const msg =
                            r.error === "no_plss_feature"
                              ? "BLM did not return a PLSS section for this point (try adjusting coordinates)."
                              : r.error === "duplicate_plss"
                                ? `That section is already used by another target${r.conflicting_name ? ` (${r.conflicting_name})` : ""}.`
                                : r.error || "Could not resolve PLSS";
                          setError(msg);
                        } else {
                          const full = await api.areas.get(selected.id);
                          setSelected(full);
                          load();
                        }
                      } catch (e) {
                        setError(e instanceof Error ? e.message : "PLSS lookup failed");
                      } finally {
                        setFillPlssFromCoordsLoading(false);
                      }
                    }}
                    className="w-full px-3 py-2 bg-teal-700 text-white rounded-lg text-sm font-medium hover:bg-teal-800 disabled:opacity-50"
                  >
                    {fillPlssFromCoordsLoading ? "Querying BLM…" : "Fill PLSS from coordinates"}
                  </button>
                )}
                <div>
                  <div className="flex items-center justify-between">
                    <span className="text-slate-500">Location (PLSS)</span>
                    {!plssEditing && (
                      <button
                        type="button"
                        onClick={() => {
                          // Pre-fill from the stored components. The DB keeps BLM-encoded
                          // values like "0120S" / "0120W" / "035"; display them in the
                          // friendlier "12S" / "12W" / "35" form the user is used to.
                          const compact = (v?: string | null) =>
                            v ? v.replace(/^0+(\d)/, "$1") : "";
                          const compactSec = (v?: string | null) =>
                            v ? v.replace(/^0+(?=\d)/, "") : "";
                          setPlssStateDraft((selected.state_abbr || "").toUpperCase());
                          setPlssTownshipDraft(compact(selected.township));
                          setPlssRangeDraft(compact(selected.range));
                          setPlssSectionDraft(compactSec(selected.section));
                          setPlssRegeocode(true);
                          setPlssEditing(true);
                        }}
                        className="text-xs text-primary-600 hover:underline"
                      >
                        {selected.location_plss ? "Edit" : "Add"}
                      </button>
                    )}
                  </div>
                  {plssEditing ? (
                    <div className="mt-1 space-y-2">
                      <div className="grid grid-cols-4 gap-2">
                        <label className="flex flex-col gap-1">
                          <span className="text-[11px] text-slate-500">State</span>
                          <input
                            type="text"
                            value={plssStateDraft}
                            onChange={(e) => setPlssStateDraft(e.target.value.toUpperCase().slice(0, 2))}
                            maxLength={2}
                            className="px-2 py-1.5 border border-slate-200 rounded text-sm uppercase focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
                            placeholder="UT"
                          />
                        </label>
                        <label className="flex flex-col gap-1">
                          <span className="text-[11px] text-slate-500">Township</span>
                          <input
                            type="text"
                            value={plssTownshipDraft}
                            onChange={(e) => setPlssTownshipDraft(e.target.value)}
                            className="px-2 py-1.5 border border-slate-200 rounded text-sm focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
                            placeholder="12S"
                          />
                        </label>
                        <label className="flex flex-col gap-1">
                          <span className="text-[11px] text-slate-500">Range</span>
                          <input
                            type="text"
                            value={plssRangeDraft}
                            onChange={(e) => setPlssRangeDraft(e.target.value)}
                            className="px-2 py-1.5 border border-slate-200 rounded text-sm focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
                            placeholder="12W"
                          />
                        </label>
                        <label className="flex flex-col gap-1">
                          <span className="text-[11px] text-slate-500">Section</span>
                          <input
                            type="text"
                            value={plssSectionDraft}
                            onChange={(e) => setPlssSectionDraft(e.target.value)}
                            className="px-2 py-1.5 border border-slate-200 rounded text-sm focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
                            placeholder="35"
                          />
                        </label>
                      </div>
                      <label className="flex items-center gap-2 text-xs text-slate-600 select-none">
                        <input
                          type="checkbox"
                          checked={plssRegeocode}
                          onChange={(e) => setPlssRegeocode(e.target.checked)}
                          className="h-3.5 w-3.5"
                        />
                        Re-derive latitude/longitude from this PLSS via BLM
                      </label>
                      <div className="text-[11px] text-slate-500 leading-snug">
                        Accepts <code>12S</code> or <code>T12S</code>, <code>12W</code> or <code>R12W</code>,
                        section 1-36. Leave all blank → Save clears the PLSS.
                      </div>
                      <div className="flex gap-2">
                        <button
                          type="button"
                          disabled={plssSaving}
                          onClick={async () => {
                            setPlssSaving(true);
                            setError(null);
                            try {
                              const stateVal = plssStateDraft.trim();
                              const twpVal = plssTownshipDraft.trim();
                              const rngVal = plssRangeDraft.trim();
                              const secVal = plssSectionDraft.trim();
                              if (!twpVal && !rngVal && !secVal && !stateVal) {
                                const res = await api.areas.updatePlss(selected.id, null, {
                                  regeocode_coordinates: false,
                                });
                                if (!res.ok) {
                                  setError(res.error || "Failed to clear PLSS");
                                } else {
                                  const full = await api.areas.get(selected.id);
                                  setSelected(full);
                                  setPlssEditing(false);
                                  load();
                                }
                                return;
                              }
                              const res = await api.areas.updatePlssComponents(
                                selected.id,
                                {
                                  state_abbr: stateVal || null,
                                  township: twpVal || null,
                                  range_val: rngVal || null,
                                  section: secVal || null,
                                },
                                { regeocode_coordinates: plssRegeocode },
                              );
                              if (!res.ok) {
                                const msg =
                                  res.error === "invalid_components"
                                    ? res.detail || "One or more PLSS fields could not be parsed."
                                    : res.error === "duplicate_plss"
                                      ? `That section is already used by another target${res.conflicting_name ? ` (${res.conflicting_name})` : ""}.`
                                      : res.error === "not_found"
                                        ? "Target not found."
                                        : res.error || "Failed to update PLSS";
                                setError(msg);
                              } else {
                                const full = await api.areas.get(selected.id);
                                setSelected(full);
                                setPlssEditing(false);
                                load();
                              }
                            } catch (err) {
                              setError(err instanceof Error ? err.message : "Failed to update PLSS");
                            } finally {
                              setPlssSaving(false);
                            }
                          }}
                          className="px-3 py-1 bg-primary-600 text-white rounded text-xs font-medium hover:bg-primary-700 disabled:opacity-50"
                        >
                          {plssSaving ? "Saving…" : "Save"}
                        </button>
                        <button
                          type="button"
                          onClick={() => setPlssEditing(false)}
                          className="px-3 py-1 bg-slate-100 text-slate-600 rounded text-xs font-medium hover:bg-slate-200"
                        >
                          Cancel
                        </button>
                      </div>
                    </div>
                  ) : (
                    <span className="text-slate-700">{selected.location_plss || "—"}</span>
                  )}
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <span className="text-slate-500 block">State</span>
                    <span className="text-slate-700">{selected.state_abbr || "—"}</span>
                  </div>
                  <div>
                    <span className="text-slate-500 block">Meridian</span>
                    <span className="text-slate-700">{selected.meridian || "—"}</span>
                  </div>
                  <div>
                    <span className="text-slate-500 block">Township</span>
                    <span className="text-slate-700">{selected.township || "—"}</span>
                  </div>
                  <div>
                    <span className="text-slate-500 block">Range</span>
                    <span className="text-slate-700">{selected.range || "—"}</span>
                  </div>
                  <div>
                    <span className="text-slate-500 block">Sector</span>
                    <span className="text-slate-700">{selected.section || "—"}</span>
                  </div>
                  <div>
                    <span className="text-slate-500 block">Uploaded</span>
                    <span className="text-slate-700">{selected.is_uploaded ? "Yes" : "—"}</span>
                  </div>
                </div>
                <div>
                  <div className="flex items-center justify-between">
                    <span className="text-slate-500">Minerals</span>
                    {!mineralsEditing && (
                      <button
                        type="button"
                        onClick={() => {
                          setMineralsDraft(dedupeMineralList(selected.minerals || []));
                          setMineralDraftInput("");
                          setMineralDraftDropdownOpen(false);
                          setMineralsEditing(true);
                        }}
                        className="text-xs text-primary-600 hover:underline"
                      >
                        {(selected.minerals || []).length > 0 ? "Edit" : "Add"}
                      </button>
                    )}
                  </div>
                  {mineralsEditing ? (
                    <div className="mt-1 space-y-2">
                      {mineralsDraft.length > 0 ? (
                        <div className="flex flex-wrap gap-1">
                          {mineralsDraft.map((m) => (
                            <span
                              key={m}
                              className="inline-flex items-center gap-1 px-2 py-0.5 bg-primary-100 text-primary-700 rounded text-xs font-medium"
                            >
                              {m}
                              <button
                                type="button"
                                onClick={() => removeMineralDraft(m)}
                                className="text-primary-700/80 hover:text-primary-900"
                                aria-label={`Remove ${m}`}
                              >
                                ×
                              </button>
                            </span>
                          ))}
                        </div>
                      ) : (
                        <div className="text-xs text-slate-500">No minerals selected yet.</div>
                      )}
                      <div className="relative">
                        <input
                          type="text"
                          value={mineralDraftInput}
                          onChange={(e) => {
                            const value = e.target.value;
                            setMineralDraftInput(value);
                            setMineralDraftDropdownOpen(true);
                            if (/[;,]$/.test(value)) addMineralDraft(value);
                          }}
                          onFocus={() => {
                            setMineralDraftInputFocused(true);
                            setMineralDraftDropdownOpen(true);
                          }}
                          onBlur={() => {
                            setMineralDraftInputFocused(false);
                            setTimeout(() => setMineralDraftDropdownOpen(false), 150);
                          }}
                          onKeyDown={(e) => {
                            if (e.key === "Enter" || e.key === ",") {
                              e.preventDefault();
                              addMineralDraft(mineralDraftInput);
                            } else if (
                              e.key === "Backspace" &&
                              !mineralDraftInput.trim() &&
                              mineralsDraft.length > 0
                            ) {
                              e.preventDefault();
                              setMineralsDraft((prev) => prev.slice(0, -1));
                            }
                          }}
                          placeholder="Add mineral…"
                          className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
                          autoComplete="off"
                        />
                        {mineralDraftDropdownOpen && mineralDraftInputFocused && (
                          <ul
                            onMouseDown={(e) => e.preventDefault()}
                            className="absolute z-[100] top-full left-0 mt-0.5 w-full max-h-56 overflow-y-auto bg-white border border-slate-200 rounded-lg shadow-lg py-1 text-sm"
                            role="listbox"
                          >
                            {(() => {
                              const q = mineralDraftInput.trim().toLowerCase();
                              const selectedMinerals = new Set(mineralsDraft.map((m) => m.toLowerCase()));
                              const filtered = mineralSuggestions.filter((m) => {
                                const lower = m.toLowerCase();
                                if (selectedMinerals.has(lower)) return false;
                                return q === "" ? true : lower.includes(q);
                              });
                              if (filtered.length === 0) {
                                return (
                                  <li className="px-3 py-2 text-slate-500" role="option">
                                    No matching minerals. Keep typing to add a custom one.
                                  </li>
                                );
                              }
                              return filtered.map((m) => (
                                <li
                                  key={m}
                                  role="option"
                                  className="px-3 py-2 cursor-pointer hover:bg-primary-50 text-slate-800"
                                  onMouseDown={(e) => {
                                    e.preventDefault();
                                    addMineralDraft(m);
                                  }}
                                >
                                  {m}
                                </li>
                              ));
                            })()}
                          </ul>
                        )}
                      </div>
                      <div className="text-[11px] text-slate-500 leading-snug">
                        Type to search from your mineral list. Press Enter or comma to add. Click × to remove quickly.
                      </div>
                      <div className="flex gap-2">
                        <button
                          type="button"
                          disabled={mineralsSaving}
                          onClick={async () => {
                            setMineralsSaving(true);
                            setError(null);
                            try {
                              await api.areas.updateMinerals(selected.id, mineralsDraft);
                              const full = await api.areas.get(selected.id);
                              setSelected(full);
                              setMineralsEditing(false);
                              load();
                            } catch (err) {
                              setError(err instanceof Error ? err.message : "Failed to save minerals");
                            } finally {
                              setMineralsSaving(false);
                            }
                          }}
                          className="px-3 py-1 bg-primary-600 text-white rounded text-xs font-medium hover:bg-primary-700 disabled:opacity-50"
                        >
                          {mineralsSaving ? "Saving…" : "Save"}
                        </button>
                        <button
                          type="button"
                          onClick={() => setMineralsEditing(false)}
                          className="px-3 py-1 bg-slate-100 text-slate-600 rounded text-xs font-medium hover:bg-slate-200"
                        >
                          Cancel
                        </button>
                      </div>
                    </div>
                  ) : (selected.minerals || []).length > 0 ? (
                    <div className="flex flex-wrap gap-1 mt-0.5">
                      {selected.minerals!.map((m, i) => (
                        <span key={i} className="inline-block px-2 py-0.5 bg-primary-100 text-primary-700 rounded text-xs font-medium">{m}</span>
                      ))}
                    </div>
                  ) : <span className="text-slate-700">—</span>}
                </div>
                <div>
                  <span className="text-slate-500 block">Claim Status</span>
                  {statusBadge(selected.status)}
                </div>
                <div>
                  <span className="text-slate-500 block">Claim Type</span>
                  <span className="text-slate-700">{selected.claim_type || "—"}</span>
                </div>
                <div>
                  <span className="text-slate-500 block">Retrieval Type</span>
                  <span className="text-slate-700">{selected.retrieval_type || "User Added"}</span>
                </div>
                {selected.characteristics?.blm_prod_types && selected.characteristics.blm_prod_types.length > 0 && (
                  <div>
                    <span className="text-slate-500 block">BLM Type</span>
                    <div className="flex flex-wrap gap-1 mt-0.5">
                      {selected.characteristics.blm_prod_types.map((t) => prodTypeBadge(t))}
                    </div>
                  </div>
                )}
                {selected.report_links && selected.report_links.length > 0 && (
                  <div>
                    <span className="text-slate-500 block">Reports</span>
                    <ul className="mt-1 space-y-1">
                      {selected.report_links.map((url, i) => (
                        <li key={i}>
                          <a href={url} target="_blank" rel="noreferrer" className="text-primary-600 hover:underline truncate block max-w-full">
                            {url}
                          </a>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                <div>
                  <div className="flex items-center justify-between">
                    <span className="text-slate-500">Notes</span>
                    {!notesEditing && (
                      <button
                        type="button"
                        onClick={() => {
                          setNotesDraft(selected.validity_notes || "");
                          setNotesEditing(true);
                        }}
                        className="text-xs text-primary-600 hover:underline"
                      >
                        {selected.validity_notes ? "Edit" : "Add"}
                      </button>
                    )}
                  </div>
                  {notesEditing ? (
                    <div className="mt-1 space-y-2">
                      <textarea
                        value={notesDraft}
                        onChange={(e) => setNotesDraft(e.target.value)}
                        rows={3}
                        className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm resize-none focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
                        placeholder="Add notes about this target..."
                      />
                      <div className="flex gap-2">
                        <button
                          type="button"
                          disabled={notesSaving}
                          onClick={async () => {
                            setNotesSaving(true);
                            try {
                              await api.areas.updateNotes(selected.id, notesDraft.trim() || null);
                              const full = await api.areas.get(selected.id);
                              setSelected(full);
                              setNotesEditing(false);
                            } catch (err) {
                              setError(err instanceof Error ? err.message : "Failed to save notes");
                            } finally {
                              setNotesSaving(false);
                            }
                          }}
                          className="px-3 py-1 bg-primary-600 text-white rounded text-xs font-medium hover:bg-primary-700 disabled:opacity-50"
                        >
                          {notesSaving ? "Saving…" : "Save"}
                        </button>
                        <button
                          type="button"
                          onClick={() => setNotesEditing(false)}
                          className="px-3 py-1 bg-slate-100 text-slate-600 rounded text-xs font-medium hover:bg-slate-200"
                        >
                          Cancel
                        </button>
                      </div>
                    </div>
                  ) : (
                    <p className="text-slate-700 text-sm mt-0.5 whitespace-pre-wrap">
                      {selected.validity_notes || <span className="text-slate-400 italic">No notes</span>}
                    </p>
                  )}
                </div>

                {/* ── Inline: Claim Records from MLRS Scrape ── */}
                {selected.characteristics?.claim_records && (() => {
                  const cr = selected.characteristics.claim_records;
                  const claims = (cr.claims ?? []) as Record<string, unknown>[];
                  return (
                    <div className="mt-3 border border-emerald-200 rounded-lg overflow-hidden">
                      <div className="px-3 py-2 bg-emerald-50 border-b border-emerald-100">
                        <div className="flex flex-wrap items-start justify-between gap-2">
                          <div className="min-w-0">
                            <span className="text-xs font-semibold text-emerald-900 block">
                              Claim Records from MLRS Scrape
                            </span>
                            <button
                              type="button"
                              disabled={clearClaimSnapshotLoading}
                              onClick={async () => {
                                if (!selected?.id) return;
                                if (
                                  !window.confirm(
                                    "Are you sure? This removes all stored MLRS claim records for this target."
                                  )
                                ) {
                                  return;
                                }
                                setClearClaimSnapshotLoading(true);
                                setError(null);
                                try {
                                  const out = await api.areas.clearClaimRecordsSnapshot(selected.id);
                                  if (!out.ok && out.error) setError(out.error);
                                  const full = await api.areas.get(selected.id);
                                  setSelected(full);
                                  const list = await api.areas.list({
                                    mineral: mineralFilter || undefined,
                                    status: statusFilter || undefined,
                                    target_status: targetStatusFilter || undefined,
                                    state_abbr: stateFilter || undefined,
                                    claim_type: claimTypeFilter || undefined,
                                    retrieval_type: retrievalTypeFilter || undefined,
                                    township: townshipFilter.trim() || undefined,
                                    range_val: rangeFilter.trim() || undefined,
                                    sector: sectorFilter.trim() || undefined,
                                    name: nameFilter.trim() || undefined,
                                    limit: AREA_LIST_LIMIT,
                                  });
                                  setAreas(list);
                                } catch (e) {
                                  setError(e instanceof Error ? e.message : "Clear failed");
                                } finally {
                                  setClearClaimSnapshotLoading(false);
                                }
                              }}
                              className="mt-1 block text-left text-[11px] text-slate-600 hover:text-red-700 underline underline-offset-2 disabled:opacity-50"
                            >
                              {clearClaimSnapshotLoading ? "Clearing\u2026" : "Clear all stored claims for this target"}
                            </button>
                          </div>
                          <button
                            type="button"
                            onClick={() => setRawJsonModal({ title: "MLRS Scrape \u2014 Raw JSON", data: cr })}
                            className="text-[11px] text-emerald-700 hover:underline shrink-0 pt-0.5"
                          >
                            View Raw JSON
                          </button>
                        </div>
                      </div>
                      <div className="px-3 py-2 space-y-1">
                        {cr.fetched_at && <p className="text-[11px] text-slate-500">Fetched: {new Date(cr.fetched_at).toLocaleString()}</p>}
                        {cr.error && <p className="text-xs text-red-600">{cr.error}</p>}
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
                                const nm = String(c.claim_name ?? c.CSE_NAME ?? "\u2014");
                                const sn = String(c.serial_number ?? c.CSE_NR ?? "\u2014");
                                const plss = String(c.plss ?? c.CSE_META ?? "\u2014");
                                const casePage = typeof c.case_page === "string" ? c.case_page : null;
                                const pay = typeof c.payment_report === "string" ? c.payment_report : null;
                                const payInfo = getClaimPaymentText(c);
                                // Highlight unpaid claims (the "Maintenance fee payment was not received…" rows)
                                // with a blue background so they pop in the table.
                                const rowCls = payInfo.status === "unpaid"
                                  ? "border-t border-blue-200 bg-blue-50"
                                  : "border-t border-slate-100";
                                return (
                                  <tr key={`mlrs-${sn}-${i}`} className={rowCls}>
                                    <td className="px-3 py-1.5 text-slate-800">{nm}</td>
                                    <td className="px-3 py-1.5 font-mono text-slate-700">{sn}</td>
                                    <td className="px-3 py-1.5 whitespace-nowrap">
                                      <ClaimPaymentBadge status={payInfo.status} message={payInfo.message} />
                                    </td>
                                    <td className="px-3 py-1.5 text-slate-600 min-w-[16rem] whitespace-normal break-words" title={plss}>{plss}</td>
                                    <td className="px-3 py-1.5 space-x-2 whitespace-nowrap">
                                      {casePage && <a href={casePage} target="_blank" rel="noopener noreferrer" className="text-primary-600 hover:underline">Case</a>}
                                      {pay && <a href={pay} target="_blank" rel="noopener noreferrer" className="text-primary-600 hover:underline">RAS</a>}
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
                })()}

                {/* ── Inline: Claim Records from LR2000 Pull ── */}
                {selected.characteristics?.lr2000_geographic_index && (() => {
                  const lr = selected.characteristics.lr2000_geographic_index;
                  const claims = (lr.claims ?? []) as Record<string, unknown>[];
                  return (
                    <div className="mt-3 border border-amber-200 rounded-lg overflow-hidden">
                      <div className="px-3 py-2 bg-amber-50 border-b border-amber-100">
                        <div className="flex flex-wrap items-start justify-between gap-2">
                          <div className="min-w-0">
                            <span className="text-xs font-semibold text-amber-900 block">
                              Claim Records from LR2000 Pull
                            </span>
                            <button
                              type="button"
                              disabled={clearLr2000SnapshotLoading}
                              onClick={async () => {
                                if (!selected?.id) return;
                                if (
                                  !window.confirm(
                                    "Are you sure? This removes all stored LR2000 / Geographic Index claim records for this target."
                                  )
                                ) {
                                  return;
                                }
                                setClearLr2000SnapshotLoading(true);
                                setError(null);
                                try {
                                  const out = await api.areas.clearLr2000Snapshot(selected.id);
                                  if (!out.ok && out.error) setError(out.error);
                                  const full = await api.areas.get(selected.id);
                                  setSelected(full);
                                  const list = await api.areas.list({
                                    mineral: mineralFilter || undefined,
                                    status: statusFilter || undefined,
                                    target_status: targetStatusFilter || undefined,
                                    state_abbr: stateFilter || undefined,
                                    claim_type: claimTypeFilter || undefined,
                                    retrieval_type: retrievalTypeFilter || undefined,
                                    township: townshipFilter.trim() || undefined,
                                    range_val: rangeFilter.trim() || undefined,
                                    sector: sectorFilter.trim() || undefined,
                                    name: nameFilter.trim() || undefined,
                                    limit: AREA_LIST_LIMIT,
                                  });
                                  setAreas(list);
                                } catch (e) {
                                  setError(e instanceof Error ? e.message : "Clear failed");
                                } finally {
                                  setClearLr2000SnapshotLoading(false);
                                }
                              }}
                              className="mt-1 block text-left text-[11px] text-slate-600 hover:text-red-700 underline underline-offset-2 disabled:opacity-50"
                            >
                              {clearLr2000SnapshotLoading ? "Clearing\u2026" : "Clear all stored LR2000 records for this target"}
                            </button>
                          </div>
                          <button
                            type="button"
                            onClick={() => setRawJsonModal({ title: "LR2000 Pull \u2014 Raw JSON", data: lr })}
                            className="text-[11px] text-amber-700 hover:underline shrink-0 pt-0.5"
                          >
                            View Raw JSON
                          </button>
                        </div>
                      </div>
                      <div className="px-3 py-2 space-y-1">
                        {lr.fetched_at && <p className="text-[11px] text-slate-500">Fetched: {new Date(lr.fetched_at).toLocaleString()}</p>}
                      </div>
                      {claims.length > 0 ? (
                        <div className="overflow-x-auto">
                          <table className="min-w-full text-[11px]">
                            <thead className="bg-slate-50 text-slate-600 text-left">
                              <tr>
                                <th className="px-3 py-1.5 font-medium">Claim</th>
                                <th className="px-3 py-1.5 font-medium">Serial</th>
                                <th className="px-3 py-1.5 font-medium">Payment</th>
                                <th className="px-3 py-1.5 font-medium">PLSS</th>
                                <th className="px-3 py-1.5 font-medium">Type</th>
                                <th className="px-3 py-1.5 font-medium">Links</th>
                              </tr>
                            </thead>
                            <tbody>
                              {claims.map((c, i) => {
                                const nm = String(c.claim_name ?? c.CSE_NAME ?? "\u2014");
                                const sn = String(c.serial_number ?? c.CSE_NR ?? "\u2014");
                                const plss = String(c.plss ?? c.CSE_META ?? "\u2014");
                                const prod = c.BLM_PROD != null ? String(c.BLM_PROD) : "\u2014";
                                const casePage = typeof c.case_page === "string" ? c.case_page : null;
                                const pay = typeof c.payment_report === "string" ? c.payment_report : null;
                                const payInfo = getClaimPaymentText(c);
                                const rowCls = payInfo.status === "unpaid"
                                  ? "border-t border-blue-200 bg-blue-50"
                                  : "border-t border-slate-100";
                                return (
                                  <tr key={`lr-${sn}-${i}`} className={rowCls}>
                                    <td className="px-3 py-1.5 text-slate-800">{nm}</td>
                                    <td className="px-3 py-1.5 font-mono text-slate-700">{sn}</td>
                                    <td className="px-3 py-1.5">
                                      <ClaimPaymentBadge status={payInfo.status} message={payInfo.message} />
                                      {payInfo.status === "unpaid" && payInfo.message && (
                                        <p className="mt-0.5 text-[10px] text-blue-900 leading-tight max-w-[18rem]">{payInfo.message}</p>
                                      )}
                                    </td>
                                    <td className="px-3 py-1.5 text-slate-600 max-w-[10rem] truncate" title={plss}>{plss}</td>
                                    <td className="px-3 py-1.5 text-slate-600">{prod}</td>
                                    <td className="px-3 py-1.5 space-x-2 whitespace-nowrap">
                                      {casePage && <a href={casePage} target="_blank" rel="noopener noreferrer" className="text-primary-600 hover:underline">Case</a>}
                                      {pay && <a href={pay} target="_blank" rel="noopener noreferrer" className="text-primary-600 hover:underline">RAS</a>}
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
                })()}

                {/* ── Action buttons ── */}
                <a
                  href={(() => {
                    const twp = selected.township || "";
                    const rng = selected.range || "";
                    const sec = selected.section || "";
                    const st = selected.state_abbr || "";
                    const mer = selected.meridian || "";
                    const twpMatch = twp.match(/^(\d+)([NS])$/i);
                    const rngMatch = rng.match(/^(\d+)([EW])$/i);
                    if (twpMatch && rngMatch && st && mer) {
                      const twpNum = twpMatch[1].padStart(3, "0");
                      const twpDir = twpMatch[2].toUpperCase();
                      const rngNum = rngMatch[1].padStart(3, "0");
                      const rngDir = rngMatch[2].toUpperCase();
                      const secNum = sec ? String(parseInt(sec, 10)).padStart(3, "0") : "";
                      return `https://reports.blm.gov/report.cfm?application=RAS&report=2&state=${st}&meridian=${mer}&township=${twpNum}&tns=${twpDir}&range=${rngNum}&rew=${rngDir}${secNum ? `&section=${secNum}` : ""}`;
                    }
                    if (selected.latitude != null && selected.longitude != null)
                      return `https://mlrs.blm.gov/s/research-map#12,${selected.latitude},${selected.longitude}`;
                    return "https://mlrs.blm.gov/s/research-map";
                  })()}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="mt-2 w-full inline-flex justify-center items-center px-3 py-2 bg-slate-600 text-white rounded-lg text-sm font-medium hover:bg-slate-700"
                >
                  Show on MLRS
                </a>
                <button
                  type="button"
                  onClick={async () => {
                    if (!selected?.id) return;
                    setLr2000Loading(true);
                    setError(null);
                    try {
                      const result = await api.areas.lr2000GeographicReport(selected.id);
                      try {
                        const full = await api.areas.get(selected.id);
                        setSelected(full);
                      } catch {
                        /* ignore */
                      }
                      if (!result.ok && result.error) setError(result.error);
                    } catch (e) {
                      setError(e instanceof Error ? e.message : "LR2000 report failed");
                    } finally {
                      setLr2000Loading(false);
                    }
                  }}
                  disabled={lr2000Loading}
                  className="mt-2 w-full px-3 py-2 bg-amber-700 text-white rounded-lg text-sm font-medium hover:bg-amber-800 disabled:opacity-50"
                >
                  {lr2000Loading ? "Running…" : "Run LR2000 Report"}
                </button>


                {selected.blm_case_url && (
                  <a
                    href={selected.blm_case_url}
                    target="_blank"
                    rel="noreferrer"
                    className="block mt-2 text-primary-600 hover:underline text-sm"
                  >
                    Open BLM case →
                  </a>
                )}
                {selected.location_plss && (
                  <>
                    <button
                      onClick={async () => {
                        if (!selected?.id) return;
                        setFetchClaimRecordsLoading(true);
                        setFetchClaimRecordsProgress("Queued Fetch Claim Records job…");
                        setError(null);
                        try {
                          const result = await api.areas.fetchClaimRecords(selected.id, {
                            onProgress: (progress) => setFetchClaimRecordsProgress(formatFetchClaimProgress(progress)),
                          });
                          const claimRecords = {
                            fetched_at: result.fetched_at ?? new Date().toISOString(),
                            log: result.log ?? "",
                            claims: result.claims ?? [],
                            error: result.error,
                          };
                          setSelected((prev) =>
                            prev
                              ? {
                                  ...prev,
                                  characteristics: {
                                    ...prev.characteristics,
                                    claim_records: claimRecords,
                                  },
                                }
                              : prev
                          );
                          try {
                            const list = await api.areas.list({
                              mineral: mineralFilter || undefined,
                              status: statusFilter || undefined,
                              target_status: targetStatusFilter || undefined,
                              state_abbr: stateFilter || undefined,
                              claim_type: claimTypeFilter || undefined,
                              retrieval_type: retrievalTypeFilter || undefined,
                              township: townshipFilter.trim() || undefined,
                              range_val: rangeFilter.trim() || undefined,
                              sector: sectorFilter.trim() || undefined,
                              name: nameFilter.trim() || undefined,
                              limit: AREA_LIST_LIMIT,
                            });
                            setAreas(list);
                            const full = await api.areas.get(selected.id);
                            setSelected(full);
                          } catch {
                            /* refresh best-effort; we already updated selected with claim_records */
                          }
                          if (result.error && !result.ok) setError(result.error);
                        } catch (e) {
                          let msg =
                            e instanceof ApiError && e.body?.detail
                              ? String(e.body.detail)
                              : (e as Error).message;
                          if (e instanceof ApiError && e.body?.error === "client_timeout") {
                            msg = e.message;
                          }
                          // The script may have finished and saved data even if the browser timed out.
                          // Reload the area from the DB so the user can still view results.
                          try {
                            const full = await api.areas.get(selected.id);
                            setSelected(full);
                            if (full.characteristics?.claim_records) {
                              setError("Request timed out, but results were saved. See MLRS Scrape section above.");
                            } else {
                              setError(msg);
                            }
                          } catch {
                            setError(msg);
                          }
                        } finally {
                          setFetchClaimRecordsProgress(null);
                          setFetchClaimRecordsLoading(false);
                        }
                      }}
                      disabled={fetchClaimRecordsLoading}
                      className="mt-2 w-full px-3 py-2 bg-emerald-600 text-white rounded-lg text-sm font-medium hover:bg-emerald-700 disabled:opacity-50"
                    >
                      {fetchClaimRecordsLoading ? "Fetching…" : "Fetch Claim Records (PLSS)"}
                    </button>
                    {fetchClaimRecordsLoading && fetchClaimRecordsProgress && (
                      <p className="mt-1 text-[11px] text-slate-500">{fetchClaimRecordsProgress}</p>
                    )}
                  </>
                )}
                <button
                  type="button"
                  onClick={async () => {
                    if (!selected?.id) return;
                    setGenerateReportLoading(true);
                    setError(null);
                    try {
                      const result = await api.areas.generateReport(selected.id);
                      if (result.ok && result.report) {
                        const blob = new Blob([result.report], { type: "text/plain;charset=utf-8" });
                        const name = (selected.name || "Target").replace(/[^a-zA-Z0-9-_]/g, "_").slice(0, 40);
                        const date = new Date().toISOString().slice(0, 10);
                        const url = URL.createObjectURL(blob);
                        const a = document.createElement("a");
                        a.href = url;
                        a.download = `Mineral_Report_${name}_${date}.txt`;
                        a.click();
                        URL.revokeObjectURL(url);
                      } else {
                        setError(result.error || "Report generation failed.");
                      }
                    } catch (err) {
                      setError(err instanceof Error ? err.message : "Failed to generate report.");
                    } finally {
                      setGenerateReportLoading(false);
                    }
                  }}
                  disabled={generateReportLoading}
                  className="mt-2 w-full px-3 py-2 bg-violet-600 text-white rounded-lg text-sm font-medium hover:bg-violet-700 disabled:opacity-50"
                >
                  {generateReportLoading ? "Generating…" : "Generate Report"}
                </button>
              </div>
          </div>
        </div>
        )}
      </div>

      {plssAiReviewModal && (
        <div
          className="fixed inset-0 z-[70] flex items-center justify-center p-4 bg-black/50"
          onClick={() => !plssAiApplying && setPlssAiReviewModal(null)}
          role="dialog"
          aria-modal="true"
          aria-labelledby="plss-ai-review-title"
        >
          <div
            className="bg-white rounded-xl shadow-xl max-w-4xl w-full max-h-[90vh] flex flex-col"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="p-4 border-b border-slate-200 flex justify-between items-center shrink-0 gap-2 flex-wrap">
              <h3 id="plss-ai-review-title" className="font-semibold text-slate-900">
                Review proposed PLSS
              </h3>
              <button
                type="button"
                disabled={plssAiApplying}
                onClick={() => setPlssAiReviewModal(null)}
                className="text-slate-500 hover:text-slate-700 text-xl leading-none disabled:opacity-40"
                aria-label="Close"
              >
                &times;
              </button>
            </div>
            <p className="px-4 pt-3 text-sm text-slate-600">{plssAiReviewModal.message}</p>
            <div className="px-4 pt-2 flex flex-wrap gap-2 shrink-0">
              <button
                type="button"
                disabled={plssAiApplying}
                onClick={() => {
                  const pendingIds = plssAiReviewModal.results.filter((r) => r.pending_apply).map((r) => r.id);
                  setPlssAiReviewModal((m) =>
                    m ? { ...m, applyIds: new Set(pendingIds) } : m
                  );
                }}
                className="px-2 py-1 text-xs font-medium rounded-lg bg-slate-100 text-slate-800 hover:bg-slate-200 disabled:opacity-50"
              >
                Select all proposals
              </button>
              <button
                type="button"
                disabled={plssAiApplying}
                onClick={() => setPlssAiReviewModal((m) => (m ? { ...m, applyIds: new Set() } : m))}
                className="px-2 py-1 text-xs font-medium rounded-lg border border-slate-200 text-slate-700 hover:bg-slate-50 disabled:opacity-50"
              >
                Clear proposal selection
              </button>
            </div>
            <div className="p-4 overflow-y-auto flex-1 min-h-0">
              <table className="w-full text-sm border border-slate-200 rounded-lg overflow-hidden">
                <thead>
                  <tr className="bg-slate-50 border-b border-slate-200">
                    <th className="text-left py-2 px-2 font-semibold text-slate-700 w-10">Apply</th>
                    <th className="text-left py-2 px-2 font-semibold text-slate-700">ID</th>
                    <th className="text-left py-2 px-2 font-semibold text-slate-700">Name</th>
                    <th className="text-left py-2 px-2 font-semibold text-slate-700">Proposed PLSS</th>
                    <th className="text-left py-2 px-2 font-semibold text-slate-700">Conf.</th>
                    <th className="text-left py-2 px-2 font-semibold text-slate-700">Source</th>
                    <th className="text-left py-2 px-2 font-semibold text-slate-700">Note</th>
                  </tr>
                </thead>
                <tbody>
                  {plssAiReviewModal.results.map((r) => {
                    const canApply = Boolean(r.pending_apply);
                    const checked = canApply && plssAiReviewModal.applyIds.has(r.id);
                    return (
                      <tr key={r.id} className="border-b border-slate-100 last:border-0">
                        <td className="py-2 px-2 align-top">
                          {canApply ? (
                            <input
                              type="checkbox"
                              className="rounded border-slate-300"
                              checked={checked}
                              disabled={plssAiApplying}
                              onChange={(e) => {
                                setPlssAiReviewModal((m) => {
                                  if (!m) return m;
                                  const next = new Set(m.applyIds);
                                  if (e.target.checked) next.add(r.id);
                                  else next.delete(r.id);
                                  return { ...m, applyIds: next };
                                });
                              }}
                              aria-label={`Apply PLSS for target ${r.id}`}
                            />
                          ) : (
                            <span className="text-slate-300">—</span>
                          )}
                        </td>
                        <td className="py-2 px-2 font-mono text-xs align-top">{r.id}</td>
                        <td className="py-2 px-2 align-top">{r.name || "—"}</td>
                        <td className="py-2 px-2 align-top min-w-[10rem]">
                          {canApply ? (
                            <input
                              type="text"
                              value={plssAiReviewModal.plssEdits[r.id] ?? r.plss ?? ""}
                              disabled={plssAiApplying}
                              onChange={(e) => {
                                const v = e.target.value;
                                setPlssAiReviewModal((m) =>
                                  m ? { ...m, plssEdits: { ...m.plssEdits, [r.id]: v } } : m
                                );
                              }}
                              className="w-full px-2 py-1 border border-slate-200 rounded text-xs font-mono"
                            />
                          ) : (
                            <span className="text-slate-500">{r.plss || "—"}</span>
                          )}
                        </td>
                        <td className="py-2 px-2 align-top text-xs">{r.confidence || "—"}</td>
                        <td className="py-2 px-2 align-top text-xs text-slate-600">{r.kind || "—"}</td>
                        <td className="py-2 px-2 align-top text-xs text-red-700 break-words max-w-[14rem]">
                          {r.error || "—"}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <div className="p-4 border-t border-slate-200 flex flex-wrap gap-2 shrink-0">
              <button
                type="button"
                disabled={plssAiApplying || plssAiReviewModal.applyIds.size === 0}
                onClick={async () => {
                  const m = plssAiReviewModal;
                  if (!m) return;
                  const items = m.results
                    .filter((r) => r.pending_apply && m.applyIds.has(r.id))
                    .map((r) => {
                      const plss = (m.plssEdits[r.id] ?? r.plss ?? "").trim();
                      return {
                        id: r.id,
                        plss,
                        township: r.township ?? null,
                        range: r.range ?? null,
                        section: r.section ?? null,
                        latitude: r.latitude ?? null,
                        longitude: r.longitude ?? null,
                        notes_append: r.notes_append ?? null,
                      };
                    })
                    .filter((x) => x.plss.length > 0);
                  if (items.length === 0) {
                    setError("Select at least one proposal with a non-empty PLSS string.");
                    return;
                  }
                  setPlssAiApplying(true);
                  setError(null);
                  try {
                    const res = await api.areas.fillPlssAiApply(items);
                    if (!res.ok && res.error) {
                      setError(res.error);
                      return;
                    }
                    const okN = res.updated ?? res.results?.filter((x) => x.ok).length ?? 0;
                    const failN = (res.results?.length ?? 0) - okN;
                    setPlssAiReviewModal(null);
                    setCleanAiOutcome({ ok: okN, fail: failN });
                    const fails = (res.results ?? []).filter((x) => !x.ok).slice(0, 40);
                    setCleanAiFailures(fails.length ? fails : null);
                    setCleanAiBanner(
                      res.message ??
                        `Saved PLSS on ${okN} target(s).${failN > 0 ? ` ${failN} failed.` : ""}`
                    );
                    const data = await api.areas.cleanPreview();
                    setCleanData(data);
                    setCleanNoPlssSelected(new Set());
                    load();
                  } catch (e) {
                    setError(e instanceof Error ? e.message : "Apply PLSS proposals failed");
                  } finally {
                    setPlssAiApplying(false);
                  }
                }}
                className="px-4 py-2 bg-primary-600 text-white rounded-lg text-sm font-medium hover:bg-primary-700 disabled:opacity-50"
              >
                {plssAiApplying ? "Saving…" : `Apply selected (${plssAiReviewModal.applyIds.size})`}
              </button>
              <button
                type="button"
                disabled={plssAiApplying}
                onClick={() => setPlssAiReviewModal(null)}
                className="px-4 py-2 border border-slate-200 rounded-lg text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Raw JSON modal */}
      {batchControlOpen && (
        <div
          className="fixed inset-0 z-[56] flex items-center justify-center p-4 bg-black/50"
          onClick={() => setBatchControlOpen(false)}
          role="dialog"
          aria-modal="true"
          aria-labelledby="batch-control-title"
        >
          <div
            className="bg-white rounded-xl shadow-xl max-w-md w-full flex flex-col"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="p-4 border-b border-slate-200 flex justify-between items-center shrink-0">
              <h3 id="batch-control-title" className="font-semibold text-slate-900">
                Batch actions — {tableSelectedIds.size} target{tableSelectedIds.size === 1 ? "" : "s"}
              </h3>
              <button
                type="button"
                onClick={() => setBatchControlOpen(false)}
                className="text-slate-500 hover:text-slate-700 text-xl leading-none"
                aria-label="Close"
              >
                &times;
              </button>
            </div>
            <div className="p-4 space-y-4 text-sm text-slate-700">
              <p className="text-slate-600">Pick what to run on the selected rows. The server still processes targets in order within each request.</p>
              <label className="flex items-start gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  className="mt-0.5 rounded border-slate-300"
                  checked={batchOptFetch}
                  onChange={(e) => setBatchOptFetch(e.target.checked)}
                />
                <span>
                  <span className="font-medium text-slate-900">Fetch claim records</span>
                  <span className="block text-xs text-slate-500">MLRS scrape (same as target detail).</span>
                </span>
              </label>
              <label className="flex items-start gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  className="mt-0.5 rounded border-slate-300"
                  checked={batchOptLr2000}
                  onChange={(e) => setBatchOptLr2000(e.target.checked)}
                />
                <span>
                  <span className="font-medium text-slate-900">LR2000 / Geographic Index report</span>
                  <span className="block text-xs text-slate-500">BLM MLRS layer query per target.</span>
                </span>
              </label>
              {batchOptFetch && batchOptLr2000 && (
                <p className="text-xs text-slate-500 bg-slate-50 border border-slate-100 rounded-lg px-3 py-2">
                  For each group of targets: fetch runs first, then LR2000, then the optional pause before the next group.
                </p>
              )}
              <div className="grid gap-3 sm:grid-cols-2">
                <label className="flex flex-col gap-1">
                  <span className="text-xs font-medium text-slate-600">Targets per API request</span>
                  <input
                    type="number"
                    min={1}
                    max={AREA_BATCH_MAX_CHUNK}
                    value={batchChunkDraft}
                    onChange={(e) => setBatchChunkDraft(parseInt(e.target.value, 10) || 1)}
                    className="px-3 py-2 border border-slate-200 rounded-lg text-sm focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
                  />
                  <span className="text-xs text-slate-500">Max {AREA_BATCH_MAX_CHUNK} (server limit). Lower = smaller bursts.</span>
                </label>
                <label className="flex flex-col gap-1">
                  <span className="text-xs font-medium text-slate-600">Pause between groups (seconds)</span>
                  <input
                    type="number"
                    min={0}
                    max={120}
                    value={batchPauseDraft}
                    onChange={(e) => setBatchPauseDraft(parseInt(e.target.value, 10) || 0)}
                    className="px-3 py-2 border border-slate-200 rounded-lg text-sm focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
                  />
                  <span className="text-xs text-slate-500">0 = no wait. Helps ease rate limits.</span>
                </label>
              </div>
              <p className="text-xs text-slate-500">Chunk size and pause are remembered in this browser.</p>
            </div>
            <div className="p-4 border-t border-slate-200 flex gap-2 shrink-0">
              <button
                type="button"
                onClick={() => setBatchControlOpen(false)}
                className="flex-1 px-4 py-2 border border-slate-200 rounded-lg text-sm font-medium text-slate-700 hover:bg-slate-50"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => void runConfiguredBatch()}
                disabled={batchRunStatus !== null}
                className="flex-1 px-4 py-2 bg-slate-800 text-white rounded-lg text-sm font-medium hover:bg-slate-900 disabled:opacity-50"
              >
                Run
              </button>
            </div>
          </div>
        </div>
      )}

      {batchResultsModal && (
        <div
          className="fixed inset-0 z-[55] flex items-center justify-center p-4 bg-black/50"
          onClick={() => setBatchResultsModal(null)}
          role="dialog"
          aria-modal="true"
          aria-labelledby="batch-results-title"
        >
          <div
            className="bg-white rounded-xl shadow-xl max-w-4xl w-full max-h-[85vh] flex flex-col"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="p-4 border-b border-slate-200 flex justify-between items-center shrink-0">
              <h3 id="batch-results-title" className="font-semibold text-slate-900">
                {batchResultsModal.title}
              </h3>
              <button
                type="button"
                onClick={() => setBatchResultsModal(null)}
                className="text-slate-500 hover:text-slate-700 text-xl leading-none"
                aria-label="Close"
              >
                &times;
              </button>
            </div>
            <p className="px-4 pt-3 text-sm text-slate-700">{batchResultsModal.summary}</p>
            <div className="p-4 overflow-y-auto flex-1 min-h-0">
              <table className="w-full text-sm border border-slate-200 rounded-lg overflow-hidden">
                <thead>
                  <tr className="bg-slate-50 border-b border-slate-200">
                    <th className="text-left py-2 px-2 font-semibold text-slate-700">ID</th>
                    <th className="text-left py-2 px-2 font-semibold text-slate-700">Name</th>
                    {batchResultsModal.modes.fetch && (
                      <>
                        <th className="text-left py-2 px-2 font-semibold text-slate-700">Fetch</th>
                        <th className="text-left py-2 px-2 font-semibold text-slate-700">Claims</th>
                        <th className="text-left py-2 px-2 font-semibold text-slate-700">Fetch error</th>
                      </>
                    )}
                    {batchResultsModal.modes.lr2000 && (
                      <>
                        <th className="text-left py-2 px-2 font-semibold text-slate-700">LR2000</th>
                        <th className="text-left py-2 px-2 font-semibold text-slate-700">Claims</th>
                        <th className="text-left py-2 px-2 font-semibold text-slate-700">LR error</th>
                      </>
                    )}
                  </tr>
                </thead>
                <tbody>
                  {batchResultsModal.rows.map((r) => (
                    <tr key={r.id} className="border-b border-slate-100 last:border-0">
                      <td className="py-1.5 px-2 font-mono text-xs">{r.id}</td>
                      <td className="py-1.5 px-2">{r.name || "—"}</td>
                      {batchResultsModal.modes.fetch && (
                        <>
                          <td className="py-1.5 px-2">
                            {r.fetchOk === undefined ? "—" : r.fetchOk ? "Yes" : "No"}
                          </td>
                          <td className="py-1.5 px-2">{r.fetchClaims ?? "—"}</td>
                          <td className="py-1.5 px-2 text-xs text-red-700 break-words max-w-[12rem]">
                            {r.fetchError || "—"}
                          </td>
                        </>
                      )}
                      {batchResultsModal.modes.lr2000 && (
                        <>
                          <td className="py-1.5 px-2">
                            {r.lrOk === undefined ? "—" : r.lrOk ? "Yes" : "No"}
                          </td>
                          <td className="py-1.5 px-2">{r.lrClaims ?? "—"}</td>
                          <td className="py-1.5 px-2 text-xs text-red-700 break-words max-w-[12rem]">
                            {r.lrError || "—"}
                          </td>
                        </>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="p-4 border-t border-slate-200 shrink-0">
              <button
                type="button"
                onClick={() => setBatchResultsModal(null)}
                className="w-full px-4 py-2 bg-slate-700 text-white rounded-lg text-sm font-medium hover:bg-slate-800"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}

      {rawJsonModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50" onClick={() => setRawJsonModal(null)}>
          <div className="bg-white rounded-xl shadow-xl max-w-4xl w-full max-h-[90vh] flex flex-col" onClick={(e) => e.stopPropagation()}>
            <div className="p-4 border-b border-slate-200 flex justify-between items-center">
              <h3 className="font-semibold text-slate-900">{rawJsonModal.title}</h3>
              <button type="button" onClick={() => setRawJsonModal(null)} className="text-slate-500 hover:text-slate-700 text-xl leading-none">&times;</button>
            </div>
            <div className="p-4 overflow-y-auto flex-1 min-h-0">
              <pre className="bg-slate-100 rounded-lg p-3 text-xs overflow-x-auto whitespace-pre-wrap">
                {JSON.stringify(rawJsonModal.data, null, 2)}
              </pre>
            </div>
          </div>
        </div>
      )}


    </div>
  );
}
