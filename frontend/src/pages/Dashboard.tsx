import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError, type DiscoveryPrompt, type DiscoveryRunResult, type ReportTarget, type BatchRow } from "../api";
import { DEFAULT_SYSTEM_INSTRUCTION, DEFAULT_USER_PROMPT_TEMPLATE } from "../discoveryDefaultPrompts";

/** Matches backend ``effective_plss_string`` / import gate. */
function batchPayloadHasPlss(t: Record<string, unknown>): boolean {
  const plss = String(t.plss ?? "").trim();
  if (plss) return true;
  const twp = String(t.township ?? "").trim();
  const rng = String(t.range ?? "").trim();
  return !!(twp && rng);
}

function formatBatchNetworkError(err: unknown): string {
  const msg = err instanceof Error ? err.message : String(err);
  if (err instanceof Error && err.name === "AbortError") {
    return "Request timed out. For large batch imports, try fewer rows or open the app at http://localhost:8000 (not the Vite dev port).";
  }
  if (msg === "Failed to fetch" || msg === "Load failed" || msg.includes("NetworkError")) {
    return "Could not reach the API (connection dropped or refused). Keep uvicorn running on port 8000. If you use npm dev (port 5173), restart it after updating — large imports can take many minutes because each target may call BLM to geocode PLSS. Prefer http://localhost:8000 when importing dozens of targets.";
  }
  return msg;
}

export function Dashboard() {
  const [health, setHealth] = useState<"ok" | "error" | "db_unavailable" | null>(null);
  const [mineralCount, setMineralCount] = useState<number | null>(null);
  const [areaCount, setAreaCount] = useState<number | null>(null);
  const [discoveryOpen, setDiscoveryOpen] = useState(false);
  const [prompts, setPrompts] = useState<DiscoveryPrompt[]>([]);
  const [selectedMineral, setSelectedMineral] = useState("");
  const [systemInstruction, setSystemInstruction] = useState("");
  const [userPromptTemplate, setUserPromptTemplate] = useState("");
  const [saving, setSaving] = useState(false);
  const [replaceOnRun, setReplaceOnRun] = useState(false);
  const [running, setRunning] = useState(false);
  const [runResult, setRunResult] = useState<DiscoveryRunResult | null>(null);
  const [runLog, setRunLog] = useState<string[]>([]);
  const [minerals, setMinerals] = useState<{ id: number; name: string }[]>([]);

  // Process Mine Report modal state
  const [reportOpen, setReportOpen] = useState(false);
  const [reportFile, setReportFile] = useState<File | null>(null);
  const [reportMineral, setReportMineral] = useState("");
  const [reportState, setReportState] = useState("");
  const [reportProcessing, setReportProcessing] = useState(false);
  const [reportTargets, setReportTargets] = useState<ReportTarget[]>([]);
  const [reportSelected, setReportSelected] = useState<Set<number>>(new Set());
  const [reportError, setReportError] = useState("");
  const [reportImporting, setReportImporting] = useState(false);
  const [reportImportResult, setReportImportResult] = useState<{imported: number; errors: string[]} | null>(null);
  const [reportStep, setReportStep] = useState<"upload" | "review" | "done">("upload");
  const [reportPdfUrl, setReportPdfUrl] = useState("");
  const [reportPdfFilename, setReportPdfFilename] = useState("");

  // Batch processing state
  const [batchOpen, setBatchOpen] = useState(false);
  const [batchStep, setBatchStep] = useState<"upload" | "parsed" | "processing" | "review" | "done">("upload");
  const [batchFile, setBatchFile] = useState<File | null>(null);
  const [batchParsing, setBatchParsing] = useState(false);
  const [batchRows, setBatchRows] = useState<BatchRow[]>([]);
  const [batchSelected, setBatchSelected] = useState<Set<number>>(new Set());
  const [batchProcessing, setBatchProcessing] = useState(false);
  const [batchProgress, setBatchProgress] = useState(0);
  const [batchResults, setBatchResults] = useState<BatchRow[]>([]);
  const [batchError, setBatchError] = useState("");
  const [batchImporting, setBatchImporting] = useState(false);
  const [batchImportResult, setBatchImportResult] = useState<{
    imported: number;
    errors: string[];
    skipped?: { name: string; reason: string }[];
    note?: string;
  } | null>(null);
  const [batchStateFilter, setBatchStateFilter] = useState("");
  const [batchExpandedRows, setBatchExpandedRows] = useState<Set<number>>(new Set());
  const [batchReportSeries, setBatchReportSeries] = useState<"OME" | "DMEA" | "DMA">("OME");

  const batchImportPreview = useMemo(() => {
    let importable = 0;
    let skipped = 0;
    for (const row of batchResults) {
      if (row.pdf_targets && row.pdf_targets.length > 0) {
        for (const t of row.pdf_targets) {
          if (batchPayloadHasPlss(t as unknown as Record<string, unknown>)) importable += 1;
          else skipped += 1;
        }
      } else {
        skipped += 1;
      }
    }
    return { importable, skipped };
  }, [batchResults]);

  const loadPrompts = useCallback(() => {
    api.discovery.getPrompts().then(setPrompts).catch(() => setPrompts([]));
  }, []);

  useEffect(() => {
    api.health().then(() => setHealth("ok")).catch(() => setHealth("error"));
    api.minerals
      .list()
      .then((r) => { setMineralCount(r.length); setHealth("ok"); })
      .catch((e) => {
        if (e instanceof ApiError && e.status === 503 && e.body?.error === "database_unavailable") {
          setHealth("db_unavailable");
        } else {
          setMineralCount(0);
        }
      });
    api.areas.list({ limit: 500 }).then((r) => setAreaCount(r.length)).catch(() => setAreaCount(0));
    loadPrompts();
  }, [loadPrompts]);

  useEffect(() => {
    if (discoveryOpen) {
      loadPrompts();
      api.minerals.list().then(setMinerals).catch(() => setMinerals([]));
    }
  }, [discoveryOpen, loadPrompts]);

  useEffect(() => {
    const isDefault = selectedMineral === "" || selectedMineral == null;
    const p = prompts.find((x) =>
      isDefault ? (x.mineral_name === "" || x.mineral_name == null) : x.mineral_name === selectedMineral
    );
    if (p && (p.system_instruction ?? "").trim() && (p.user_prompt_template ?? "").trim()) {
      setSystemInstruction(p.system_instruction ?? "");
      setUserPromptTemplate(p.user_prompt_template ?? "");
    } else if (isDefault) {
      setSystemInstruction(DEFAULT_SYSTEM_INSTRUCTION);
      setUserPromptTemplate(DEFAULT_USER_PROMPT_TEMPLATE);
    } else {
      setSystemInstruction("");
      setUserPromptTemplate("");
    }
  }, [prompts, selectedMineral]);

  const openReportModal = () => {
    setReportOpen(true);
    setReportFile(null);
    setReportMineral("");
    setReportState("");
    setReportError("");
    setReportTargets([]);
    setReportSelected(new Set());
    setReportImportResult(null);
    setReportStep("upload");
    setReportPdfUrl("");
    setReportPdfFilename("");
    api.minerals.list().then(setMinerals).catch(() => setMinerals([]));
  };

  const handleProcessReport = async () => {
    if (!reportFile) return;
    setReportProcessing(true);
    setReportError("");
    setReportTargets([]);
    try {
      const form = new FormData();
      form.append("file", reportFile);
      if (reportMineral.trim()) form.append("mineral", reportMineral.trim());
      if (reportState.trim()) form.append("state", reportState.trim());
      const result = await api.mineReport.process(form);
      if (!result.ok) {
        setReportError(result.error || "Processing failed.");
        return;
      }
      if (result.targets.length === 0) {
        setReportError("No mining targets found in this report.");
        return;
      }
      setReportTargets(result.targets);
      setReportSelected(new Set(result.targets.map((_, i) => i)));
      setReportPdfUrl(result.pdf_url || "");
      setReportPdfFilename(result.pdf_filename || "");
      setReportStep("review");
    } catch (e) {
      const msg = e instanceof ApiError ? (e.body?.detail || e.message) : String(e);
      setReportError(typeof msg === "string" ? msg : JSON.stringify(msg));
    } finally {
      setReportProcessing(false);
    }
  };

  const handleImportSelected = async () => {
    const targets = reportTargets.filter((_, i) => reportSelected.has(i));
    if (targets.length === 0) return;
    setReportImporting(true);
    try {
      const result = await api.mineReport.importTargets(targets, reportPdfUrl, reportPdfFilename);
      setReportImportResult(result);
      setReportStep("done");
      api.areas.list({ limit: 500 }).then((r) => setAreaCount(r.length)).catch(() => {});
    } catch (e) {
      const msg = e instanceof ApiError ? (e.body?.detail || e.message) : String(e);
      setReportError(typeof msg === "string" ? msg : JSON.stringify(msg));
    } finally {
      setReportImporting(false);
    }
  };

  const toggleTarget = (idx: number) => {
    setReportSelected((prev) => {
      const next = new Set(prev);
      if (next.has(idx)) next.delete(idx);
      else next.add(idx);
      return next;
    });
  };

  const toggleAllTargets = () => {
    if (reportSelected.size === reportTargets.length) {
      setReportSelected(new Set());
    } else {
      setReportSelected(new Set(reportTargets.map((_, i) => i)));
    }
  };

  const openDiscovery = () => {
    setDiscoveryOpen(true);
    setSelectedMineral("");
    setRunResult(null);
    setSystemInstruction(DEFAULT_SYSTEM_INSTRUCTION);
    setUserPromptTemplate(DEFAULT_USER_PROMPT_TEMPLATE);
    api.discovery.getPrompts().then(setPrompts).catch(() => setPrompts([]));
    api.discovery.getDefaultPrompt().then((p) => {
      if ((p.system_instruction ?? "").trim()) setSystemInstruction(p.system_instruction ?? "");
      if ((p.user_prompt_template ?? "").trim()) setUserPromptTemplate(p.user_prompt_template ?? "");
    }).catch(() => {});
  };

  const handleSavePrompt = async () => {
    setSaving(true);
    try {
      await api.discovery.savePrompt(selectedMineral, systemInstruction, userPromptTemplate);
      loadPrompts();
    } catch (e) {
      console.error(e);
    } finally {
      setSaving(false);
    }
  };

  const handleRun = async () => {
    setRunning(true);
    setRunResult(null);
    setRunLog(["Discovery agent starting…"]);
    try {
      const result = await api.discovery.run(replaceOnRun);
      setRunResult(result);
      setRunLog(result.log ?? ["Done."]);
      api.areas.list({ limit: 500 }).then((r) => setAreaCount(r.length)).catch(() => {});
    } catch (e) {
      let msg = e instanceof ApiError ? (e.body?.detail as string) || e.message : String(e);
      if (msg === "Failed to fetch" || (e as Error).message === "Failed to fetch") {
        msg = "Could not reach the API. Start backend and frontend: from repo root run bash scripts/dev.sh, then open http://localhost:5173. Or run bash scripts/start-backend.sh in one terminal and cd frontend && npm run dev in another. If discovery was running a long time, the request may have timed out—try again.";
      }
      setRunResult({ status: "error", message: msg });
      setRunLog((prev) => [...prev, `Error: ${msg}`]);
    } finally {
      setRunning(false);
    }
  };


  return (
    <div>
      <div className="flex items-start justify-between gap-4 mb-8">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 mb-2">Dashboard</h1>
          <p className="text-slate-600">Overview of your minerals and targets.</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={openReportModal}
            className="flex items-center gap-2 px-3 py-2 rounded-xl border border-slate-200 bg-white text-slate-700 hover:bg-slate-50 hover:border-primary-300 hover:text-primary-700 transition-colors shadow-sm"
            title="Upload a mining PDF report and extract targets using AI"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
            </svg>
            <span className="text-sm font-medium">Process Mine PDF Report</span>
          </button>
          <button
            type="button"
            onClick={() => {
              setBatchOpen(true);
              setBatchStep("upload");
              setBatchFile(null);
              setBatchRows([]);
              setBatchSelected(new Set());
              setBatchResults([]);
              setBatchError("");
              setBatchImportResult(null);
              setBatchStateFilter("");
              setBatchExpandedRows(new Set());
              setBatchReportSeries("OME");
            }}
            className="flex items-center gap-2 px-3 py-2 rounded-xl border border-slate-200 bg-white text-slate-700 hover:bg-slate-50 hover:border-primary-300 hover:text-primary-700 transition-colors shadow-sm"
            title="Upload a CSV of report metadata for batch processing"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 7v10c0 2 1 3 3 3h10c2 0 3-1 3-3V7M4 7l4-4h8l4 4M4 7h16M10 11v6m4-6v6" />
            </svg>
            <span className="text-sm font-medium">Batch Process Reports</span>
          </button>
          <button
            type="button"
            onClick={openDiscovery}
            className="flex items-center gap-2 px-3 py-2 rounded-xl border border-slate-200 bg-white text-slate-700 hover:bg-slate-50 hover:border-primary-300 hover:text-primary-700 transition-colors shadow-sm"
            title="Discovery agent: define prompts and run internet + AI search"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M13 10V3L4 14h7v7l9-11h-7z" />
            </svg>
            <span className="text-sm font-medium">Discovery agent</span>
          </button>
        </div>
      </div>

      <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
        <Link
          to="/minerals"
          className="block p-6 bg-white rounded-xl border border-slate-200 shadow-card hover:shadow-card-hover hover:border-primary-200 transition-all"
        >
          <div className="flex items-center gap-3">
            <div className="w-12 h-12 rounded-lg bg-primary-100 flex items-center justify-center text-primary-600 font-semibold">
              {mineralCount ?? "—"}
            </div>
            <div>
              <h2 className="font-semibold text-slate-900">Minerals of interest</h2>
              <p className="text-sm text-slate-500">Priority minerals driving discovery</p>
            </div>
          </div>
        </Link>

        <Link
          to="/areas"
          className="block p-6 bg-white rounded-xl border border-slate-200 shadow-card hover:shadow-card-hover hover:border-primary-200 transition-all"
        >
          <div className="flex items-center gap-3">
            <div className="w-12 h-12 rounded-lg bg-slate-100 flex items-center justify-center text-slate-600 font-semibold">
              {areaCount ?? "—"}
            </div>
            <div>
              <h2 className="font-semibold text-slate-900">Targets</h2>
              <p className="text-sm text-slate-500">Claims & mines with location and status</p>
            </div>
          </div>
        </Link>

        <Link
          to="/map"
          className="block p-6 bg-white rounded-xl border border-slate-200 shadow-card hover:shadow-card-hover hover:border-primary-200 transition-all"
        >
          <div className="flex items-center gap-3">
            <div className="w-12 h-12 rounded-lg bg-slate-100 flex items-center justify-center text-slate-600">
              <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 20l-5.447-2.724A1 1 0 013 16.382V5.618a1 1 0 011.447-.894L9 7m0 13l6-3m-6 3V7m6 10l4.553 2.276A1 1 0 0021 18.382V7.618a1 1 0 00-.553-.894L15 4m0 13V4m0 0L9 7" />
              </svg>
            </div>
            <div>
              <h2 className="font-semibold text-slate-900">Map</h2>
              <p className="text-sm text-slate-500">View targets by location and status</p>
            </div>
          </div>
        </Link>

        <Link
          to="/discoveries"
          className="block p-6 bg-white rounded-xl border border-slate-200 shadow-card hover:shadow-card-hover hover:border-primary-200 transition-all"
        >
          <div className="flex items-center gap-3">
            <div className="w-12 h-12 rounded-lg bg-primary-100 flex items-center justify-center text-primary-600">
              <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-3 7h3m-3 4h3m-6-4h.01M9 16h.01" />
              </svg>
            </div>
            <div>
              <h2 className="font-semibold text-slate-900">Discoveries</h2>
              <p className="text-sm text-slate-500">Log of every discovery run and output</p>
            </div>
          </div>
        </Link>
      </div>

      {health === "ok" && (
        <div className="mt-8 p-4 bg-primary-50 border border-primary-200 rounded-lg text-primary-800 text-sm">
          API connected. Use <strong>Ingest data files</strong> on the Targets page to load your CSVs.
        </div>
      )}
      {health === "db_unavailable" && (
        <div className="mt-8 p-6 bg-amber-50 border border-amber-200 rounded-xl text-amber-900">
          <h3 className="font-semibold mb-2">Set up the database</h3>
          <p className="text-sm mb-4">The app needs Postgres running. Do this once:</p>
          <ol className="text-sm list-decimal list-inside space-y-2 font-mono bg-amber-100/60 p-4 rounded-lg">
            <li>Install <a href="https://www.docker.com/products/docker-desktop/" target="_blank" rel="noreferrer" className="text-primary-600 underline">Docker Desktop</a> and open it.</li>
            <li>In Terminal, from the project folder:
              <pre className="mt-2 p-2 bg-white rounded text-xs overflow-x-auto">cd /Users/craiglutz/Agents/Mining_OS\ndocker compose up -d</pre>
            </li>
            <li>After Postgres is up (~30 sec), run:
              <pre className="mt-2 p-2 bg-white rounded text-xs overflow-x-auto">.venv/bin/python -m mining_os.pipelines.run_all --init-db</pre>
            </li>
          </ol>
          <p className="text-sm mt-3">Then refresh this page.</p>
        </div>
      )}
      {health === "error" && (
        <div className="mt-8 p-4 bg-amber-50 border border-amber-200 rounded-lg text-amber-800 text-sm">
          Cannot reach the API. Start the backend: <code className="bg-amber-100 px-1 rounded">uvicorn mining_os.api.main:app --port 8000</code>
        </div>
      )}

      {reportOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50"
          onClick={() => !reportProcessing && !reportImporting && setReportOpen(false)}
          role="dialog"
          aria-modal="true"
          aria-labelledby="report-modal-title"
        >
          <div
            className="bg-white rounded-2xl shadow-xl max-w-5xl w-full max-h-[90vh] overflow-hidden flex flex-col"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between px-6 py-4 border-b border-slate-200">
              <div className="flex items-center gap-3">
                <svg className="w-6 h-6 text-primary-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                </svg>
                <h2 id="report-modal-title" className="text-xl font-bold text-slate-900">
                  Process Mine PDF Report
                </h2>
              </div>
              <button
                type="button"
                onClick={() => setReportOpen(false)}
                disabled={reportProcessing || reportImporting}
                className="p-2 text-slate-400 hover:text-slate-600 rounded-lg hover:bg-slate-100 disabled:opacity-50"
                aria-label="Close"
              >
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>

            <div className="px-6 py-4 overflow-y-auto flex-1">
              {/* Step indicators */}
              <div className="flex items-center gap-2 mb-6">
                {[
                  { key: "upload", label: "Upload & Process" },
                  { key: "review", label: "Review Targets" },
                  { key: "done", label: "Import" },
                ].map((s, i) => (
                  <div key={s.key} className="flex items-center gap-2">
                    {i > 0 && <div className={`w-8 h-px ${reportStep === s.key || (s.key === "done" && reportStep === "done") || (s.key === "review" && reportStep !== "upload") ? "bg-primary-400" : "bg-slate-200"}`} />}
                    <div className={`flex items-center gap-1.5 text-xs font-medium px-2.5 py-1 rounded-full ${reportStep === s.key ? "bg-primary-100 text-primary-700" : reportStep === "done" || (reportStep === "review" && s.key === "upload") ? "bg-primary-50 text-primary-500" : "bg-slate-100 text-slate-400"}`}>
                      <span className={`w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold ${reportStep === s.key ? "bg-primary-600 text-white" : reportStep === "done" || (reportStep === "review" && s.key === "upload") ? "bg-primary-400 text-white" : "bg-slate-300 text-white"}`}>
                        {reportStep === "done" || (reportStep === "review" && s.key === "upload") ? "✓" : i + 1}
                      </span>
                      {s.label}
                    </div>
                  </div>
                ))}
              </div>

              {/* Step 1: Upload */}
              {reportStep === "upload" && (
                <div className="space-y-5">
                  <p className="text-sm text-slate-600">
                    Upload a historical mining PDF report and AI will extract all mining targets (PLSS locations, coordinates, mine names) for your review.
                  </p>

                  {/* File drop zone */}
                  <div
                    className={`border-2 border-dashed rounded-xl p-8 text-center transition-colors ${reportFile ? "border-primary-300 bg-primary-50" : "border-slate-300 hover:border-primary-300 hover:bg-slate-50"}`}
                    onDragOver={(e) => { e.preventDefault(); e.stopPropagation(); }}
                    onDrop={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      const f = e.dataTransfer.files[0];
                      if (f?.type === "application/pdf") setReportFile(f);
                    }}
                  >
                    {reportFile ? (
                      <div className="flex items-center justify-center gap-3">
                        <svg className="w-8 h-8 text-primary-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                        </svg>
                        <div className="text-left">
                          <p className="text-sm font-medium text-slate-900">{reportFile.name}</p>
                          <p className="text-xs text-slate-500">{(reportFile.size / 1024 / 1024).toFixed(1)} MB</p>
                        </div>
                        <button type="button" onClick={() => setReportFile(null)} className="ml-4 text-slate-400 hover:text-red-500">
                          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                          </svg>
                        </button>
                      </div>
                    ) : (
                      <>
                        <svg className="w-10 h-10 text-slate-400 mx-auto mb-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
                        </svg>
                        <p className="text-sm text-slate-600 mb-1">Drag and drop a PDF here, or</p>
                        <label className="inline-block px-4 py-2 bg-white border border-slate-300 rounded-lg text-sm font-medium text-slate-700 hover:bg-slate-50 cursor-pointer">
                          Browse files
                          <input type="file" accept=".pdf" className="hidden" onChange={(e) => { if (e.target.files?.[0]) setReportFile(e.target.files[0]); }} />
                        </label>
                        <p className="text-xs text-slate-400 mt-2">PDF only, max 50 MB</p>
                      </>
                    )}
                  </div>

                  {/* Optional filters */}
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <label className="block text-xs font-medium text-slate-500 mb-1">Mineral (optional)</label>
                      <select
                        value={reportMineral}
                        onChange={(e) => setReportMineral(e.target.value)}
                        className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm"
                      >
                        <option value="">All minerals</option>
                        {minerals.map((m) => (
                          <option key={m.id} value={m.name}>{m.name}</option>
                        ))}
                      </select>
                      <p className="text-xs text-slate-400 mt-1">Focus extraction on a specific mineral</p>
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-slate-500 mb-1">State (optional)</label>
                      <input
                        type="text"
                        value={reportState}
                        onChange={(e) => setReportState(e.target.value)}
                        placeholder="e.g. Utah, NV, Wyoming"
                        className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm"
                      />
                      <p className="text-xs text-slate-400 mt-1">Default state if not in document</p>
                    </div>
                  </div>

                  {reportError && (
                    <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">{reportError}</div>
                  )}

                  <button
                    type="button"
                    onClick={handleProcessReport}
                    disabled={!reportFile || reportProcessing}
                    className="px-5 py-2.5 bg-primary-600 text-white rounded-lg text-sm font-medium hover:bg-primary-700 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
                  >
                    {reportProcessing ? (
                      <>
                        <svg className="animate-spin h-4 w-4 text-white" fill="none" viewBox="0 0 24 24">
                          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                        </svg>
                        Processing with AI... this may take a minute
                      </>
                    ) : (
                      <>
                        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
                        </svg>
                        Process Report
                      </>
                    )}
                  </button>
                </div>
              )}

              {/* Step 2: Review extracted targets */}
              {reportStep === "review" && (
                <div className="space-y-4">
                  <div className="flex items-center justify-between">
                    <p className="text-sm text-slate-600">
                      Found <strong>{reportTargets.length}</strong> target{reportTargets.length !== 1 ? "s" : ""} in the report.
                      Select which to import.
                    </p>
                    <div className="flex items-center gap-3">
                      <button type="button" onClick={() => { setReportStep("upload"); setReportError(""); }} className="text-xs text-slate-500 hover:text-slate-700 underline">
                        Back to upload
                      </button>
                      <label className="flex items-center gap-1.5 text-xs text-slate-600 cursor-pointer">
                        <input
                          type="checkbox"
                          checked={reportSelected.size === reportTargets.length}
                          onChange={toggleAllTargets}
                          className="rounded text-primary-600"
                        />
                        Select all
                      </label>
                    </div>
                  </div>

                  <div className="border border-slate-200 rounded-xl overflow-hidden">
                    <div className="overflow-x-auto max-h-[50vh]">
                      <table className="w-full text-sm">
                        <thead className="bg-slate-50 text-slate-600 text-xs uppercase tracking-wider sticky top-0">
                          <tr>
                            <th className="px-3 py-2.5 text-left w-8"></th>
                            <th className="px-3 py-2.5 text-left">Name</th>
                            <th className="px-3 py-2.5 text-left">State</th>
                            <th className="px-3 py-2.5 text-left">PLSS</th>
                            <th className="px-3 py-2.5 text-left">Lat / Long</th>
                            <th className="px-3 py-2.5 text-left">Minerals</th>
                            <th className="px-3 py-2.5 text-left">County</th>
                            <th className="px-3 py-2.5 text-left max-w-[200px]">Notes</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-100">
                          {reportTargets.map((t, i) => (
                            <tr
                              key={i}
                              className={`transition-colors cursor-pointer ${reportSelected.has(i) ? "bg-primary-50/50" : "hover:bg-slate-50"}`}
                              onClick={() => toggleTarget(i)}
                            >
                              <td className="px-3 py-2.5">
                                <input
                                  type="checkbox"
                                  checked={reportSelected.has(i)}
                                  onChange={() => toggleTarget(i)}
                                  onClick={(e) => e.stopPropagation()}
                                  className="rounded text-primary-600"
                                />
                              </td>
                              <td className="px-3 py-2.5 font-medium text-slate-900 whitespace-nowrap">{t.name}</td>
                              <td className="px-3 py-2.5 text-slate-600">{t.state || "—"}</td>
                              <td className="px-3 py-2.5 text-slate-600 whitespace-nowrap font-mono text-xs">{t.plss || "—"}</td>
                              <td className="px-3 py-2.5 text-slate-600 whitespace-nowrap text-xs">
                                {t.latitude != null && t.longitude != null ? `${t.latitude}, ${t.longitude}` : "—"}
                              </td>
                              <td className="px-3 py-2.5">
                                {t.minerals?.length ? (
                                  <div className="flex flex-wrap gap-1">
                                    {t.minerals.map((m, mi) => (
                                      <span key={mi} className="px-1.5 py-0.5 bg-primary-100 text-primary-700 rounded text-xs">{m}</span>
                                    ))}
                                  </div>
                                ) : "—"}
                              </td>
                              <td className="px-3 py-2.5 text-slate-600 text-xs">{t.county || "—"}</td>
                              <td className="px-3 py-2.5 text-slate-500 text-xs max-w-[200px] truncate" title={t.notes}>{t.notes || "—"}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>

                  {reportError && (
                    <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">{reportError}</div>
                  )}

                  <div className="flex items-center justify-between pt-2">
                    <p className="text-xs text-slate-500">
                      {reportSelected.size} of {reportTargets.length} selected
                    </p>
                    <button
                      type="button"
                      onClick={handleImportSelected}
                      disabled={reportSelected.size === 0 || reportImporting}
                      className="px-5 py-2.5 bg-primary-600 text-white rounded-lg text-sm font-medium hover:bg-primary-700 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
                    >
                      {reportImporting ? (
                        <>
                          <svg className="animate-spin h-4 w-4 text-white" fill="none" viewBox="0 0 24 24">
                            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                          </svg>
                          Importing…
                        </>
                      ) : (
                        <>Import {reportSelected.size} target{reportSelected.size !== 1 ? "s" : ""}</>
                      )}
                    </button>
                  </div>
                </div>
              )}

              {/* Step 3: Done */}
              {reportStep === "done" && reportImportResult && (
                <div className="space-y-4">
                  <div className="p-6 bg-primary-50 border border-primary-200 rounded-xl text-center">
                    <svg className="w-12 h-12 text-primary-500 mx-auto mb-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                    </svg>
                    <p className="text-lg font-semibold text-primary-900">
                      {reportImportResult.imported} target{reportImportResult.imported !== 1 ? "s" : ""} imported
                    </p>
                    <p className="text-sm text-primary-700 mt-1">
                      Targets are now available on the Targets page with source "pdf_report".
                    </p>
                  </div>
                  {reportImportResult.errors.length > 0 && (
                    <div className="p-3 bg-amber-50 border border-amber-200 rounded-lg text-sm text-amber-800">
                      <p className="font-medium mb-1">{reportImportResult.errors.length} error{reportImportResult.errors.length !== 1 ? "s" : ""}:</p>
                      <ul className="list-disc list-inside text-xs space-y-0.5">
                        {reportImportResult.errors.map((err, i) => <li key={i}>{err}</li>)}
                      </ul>
                    </div>
                  )}
                  <div className="flex items-center gap-3 pt-2">
                    <button
                      type="button"
                      onClick={() => { setReportStep("upload"); setReportFile(null); setReportTargets([]); setReportImportResult(null); setReportError(""); }}
                      className="px-4 py-2 bg-white border border-slate-200 rounded-lg text-sm font-medium text-slate-700 hover:bg-slate-50"
                    >
                      Process another report
                    </button>
                    <Link
                      to="/areas"
                      onClick={() => setReportOpen(false)}
                      className="px-4 py-2 bg-primary-600 text-white rounded-lg text-sm font-medium hover:bg-primary-700"
                    >
                      View Targets
                    </Link>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {discoveryOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50"
          onClick={() => setDiscoveryOpen(false)}
          role="dialog"
          aria-modal="true"
          aria-labelledby="discovery-modal-title"
        >
          <div
            className="bg-white rounded-2xl shadow-xl max-w-3xl w-full max-h-[90vh] overflow-hidden flex flex-col"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between px-6 py-4 border-b border-slate-200">
              <h2 id="discovery-modal-title" className="text-xl font-bold text-slate-900">
                Discovery agent
              </h2>
              <button
                type="button"
                onClick={() => setDiscoveryOpen(false)}
                className="p-2 text-slate-400 hover:text-slate-600 rounded-lg hover:bg-slate-100"
                aria-label="Close"
              >
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
            <div className="px-6 py-4 overflow-y-auto flex-1 space-y-6">
              <section>
                <h3 className="text-sm font-semibold text-slate-700 mb-2">Prompts (editable)</h3>
                <p className="text-slate-500 text-sm mb-3">
                  Use <code className="bg-slate-100 px-1 rounded">{"{{mineral}}"}</code> and <code className="bg-slate-100 px-1 rounded">{"{{states}}"}</code> in the user prompt; they are replaced per mineral and with your target states.
                </p>
                <div className="mb-3">
                  <label className="block text-xs font-medium text-slate-500 mb-1">Mineral (empty = default for all)</label>
                  <select
                    value={selectedMineral}
                    onChange={(e) => setSelectedMineral(e.target.value)}
                    className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm"
                  >
                    <option value="">Default (all minerals)</option>
                    {minerals.map((m) => (
                      <option key={m.id} value={m.name}>{m.name}</option>
                    ))}
                  </select>
                </div>
                <div className="mb-3">
                  <label className="block text-xs font-medium text-slate-500 mb-1">System instruction</label>
                  <textarea
                    value={systemInstruction}
                    onChange={(e) => setSystemInstruction(e.target.value)}
                    rows={6}
                    className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm font-mono"
                  />
                </div>
                <div className="mb-3">
                  <label className="block text-xs font-medium text-slate-500 mb-1">User prompt template</label>
                  <textarea
                    value={userPromptTemplate}
                    onChange={(e) => setUserPromptTemplate(e.target.value)}
                    rows={5}
                    className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm font-mono"
                  />
                </div>
                <button
                  type="button"
                  onClick={handleSavePrompt}
                  disabled={saving}
                  className="px-4 py-2 bg-primary-600 text-white rounded-lg text-sm font-medium hover:bg-primary-700 disabled:opacity-50"
                >
                  {saving ? "Saving…" : "Save prompts"}
                </button>
              </section>

              <section className="pt-4 border-t border-slate-200">
                <h3 className="text-sm font-semibold text-slate-700 mb-2">Run discovery</h3>
                <p className="text-slate-500 text-sm mb-3">
                  Uses AI and web search to find locations/mines, then BLM for claim status.
                </p>
                <div className="flex flex-wrap items-center gap-4 mb-4">
                  <label className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="radio"
                      checked={!replaceOnRun}
                      onChange={() => setReplaceOnRun(false)}
                      className="text-primary-600"
                    />
                    <span className="text-sm">Add to / supplement existing list</span>
                  </label>
                  <label className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="radio"
                      checked={replaceOnRun}
                      onChange={() => setReplaceOnRun(true)}
                      className="text-primary-600"
                    />
                    <span className="text-sm">Replace discovery list (remove existing discovery-sourced targets first)</span>
                  </label>
                </div>
                <button
                  type="button"
                  onClick={handleRun}
                  disabled={running}
                  className="px-4 py-2 bg-slate-800 text-white rounded-lg text-sm font-medium hover:bg-slate-900 disabled:opacity-50 flex items-center gap-2"
                >
                  {running ? (
                    <>
                      <svg className="animate-spin h-4 w-4 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" aria-hidden>
                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                      </svg>
                      Running discovery…
                    </>
                  ) : (
                    "Run discovery"
                  )}
                </button>
                {(running || runLog.length > 0) && (
                  <div className="mt-4 rounded-xl border border-slate-200 bg-slate-900 text-slate-100 overflow-hidden">
                    <div className="px-3 py-2 border-b border-slate-700 flex items-center gap-2">
                      {running && (
                        <svg className="animate-spin h-4 w-4 text-primary-400" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                        </svg>
                      )}
                      <span className="text-xs font-medium text-slate-300">
                        {running ? "Discovery agent is running…" : "Run log"}
                      </span>
                    </div>
                    <div className="p-3 font-mono text-xs max-h-48 overflow-y-auto space-y-0.5">
                      {runLog.map((line, i) => (
                        <div key={i} className="text-slate-300">{line}</div>
                      ))}
                    </div>
                  </div>
                )}
                {runResult && !running && (
                  <div className={`mt-4 p-4 rounded-xl text-sm ${runResult.status === "ok" ? "bg-primary-50 border border-primary-200 text-primary-900" : "bg-amber-50 border border-amber-200 text-amber-900"}`}>
                    {runResult.status === "ok" ? (
                      <>
                        <p><strong>Targets added:</strong> {runResult.areas_added ?? 0}</p>
                        {runResult.minerals_checked?.length ? (
                          <p className="mt-1">Minerals checked: {runResult.minerals_checked.join(", ")}</p>
                        ) : null}
                        {runResult.errors?.length ? (
                          <p className="mt-2 text-amber-700">Some errors: {runResult.errors.slice(0, 3).join("; ")}</p>
                        ) : null}
                      </>
                    ) : (
                      <p>{runResult.message ?? runResult.status}</p>
                    )}
                  </div>
                )}
              </section>
            </div>
          </div>
        </div>
      )}

      {/* Batch Process Reports modal */}
      {batchOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50"
          onClick={() => setBatchOpen(false)}
          role="dialog"
          aria-modal="true"
        >
          <div
            className="bg-white rounded-2xl shadow-xl max-w-5xl w-full max-h-[90vh] overflow-hidden flex flex-col"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between px-6 py-4 border-b border-slate-200 shrink-0">
              <h2 className="text-xl font-bold text-slate-900">Batch Process Reports</h2>
              <button
                type="button"
                onClick={() => setBatchOpen(false)}
                className="p-2 text-slate-400 hover:text-slate-600 rounded-lg hover:bg-slate-100"
                aria-label="Close"
              >
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
            <div className="px-6 py-4 overflow-y-auto flex-1 min-h-0">
              {batchError && (
                <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-lg text-red-800 text-sm">{batchError}</div>
              )}

              {batchStep === "upload" && (
                <div className="space-y-4">
                  <p className="text-sm text-slate-600">
                    Upload a CSV with report metadata. Supported columns: <strong>Docket</strong>, <strong>Property name</strong>, <strong>State abbreviation</strong>, <strong>County</strong>, <strong>All commodities</strong>, <strong>File size</strong>. Pick the correct <strong>report list type</strong> so USGS URLs match: <strong>OME / DMEA</strong> use <code className="text-xs bg-slate-100 px-1 rounded">…/ome/&#123;docket&#125;_OME.pdf</code>; <strong>DMA</strong> uses <code className="text-xs bg-slate-100 px-1 rounded">…/dma/NNNN_DMA.pdf</code> (4-digit docket). Filename hints (DMA_Report, DMEA_Report, OME_Report) adjust the type automatically when you choose OME first.
                  </p>
                  <div className="flex flex-wrap items-end gap-4">
                    <label className="flex flex-col gap-1">
                      <span className="text-xs text-slate-500">CSV File</span>
                      <input
                        type="file"
                        accept=".csv"
                        onChange={(e) => {
                          const f = e.target.files?.[0] ?? null;
                          setBatchFile(f);
                          const n = (f?.name || "").toLowerCase();
                          if (n.includes("dmea")) setBatchReportSeries("DMEA");
                          else if (n.includes("dma")) setBatchReportSeries("DMA");
                          else if (n.includes("ome")) setBatchReportSeries("OME");
                        }}
                        className="text-sm"
                      />
                    </label>
                    <label className="flex flex-col gap-1">
                      <span className="text-xs text-slate-500">List type (USGS path)</span>
                      <select
                        value={batchReportSeries}
                        onChange={(e) => setBatchReportSeries(e.target.value as "OME" | "DMEA" | "DMA")}
                        className="px-3 py-2 border border-slate-200 rounded-lg text-sm min-w-[10rem]"
                      >
                        <option value="OME">OME (Order of Exclusion)</option>
                        <option value="DMEA">DMEA (same scans as OME)</option>
                        <option value="DMA">DMA (4-digit docket path)</option>
                      </select>
                    </label>
                    <button
                      type="button"
                      disabled={!batchFile || batchParsing}
                      onClick={async () => {
                        if (!batchFile) return;
                        setBatchParsing(true);
                        setBatchError("");
                        try {
                          const form = new FormData();
                          form.set("file", batchFile);
                          const result = await api.batchReport.parseCSV(form, batchReportSeries);
                          if (result.ok) {
                            setBatchRows(result.rows);
                            const downloadableIndices = new Set(
                              result.rows.map((_, i) => i).filter((i) => result.rows[i].downloadable)
                            );
                            setBatchSelected(downloadableIndices);
                            setBatchStep("parsed");
                          } else {
                            setBatchError("Failed to parse CSV");
                          }
                        } catch (err) {
                          setBatchError(formatBatchNetworkError(err));
                        } finally {
                          setBatchParsing(false);
                        }
                      }}
                      className="px-4 py-2 bg-slate-700 text-white rounded-lg text-sm font-medium hover:bg-slate-800 disabled:opacity-50"
                    >
                      {batchParsing ? "Parsing…" : "Parse CSV"}
                    </button>
                  </div>
                </div>
              )}

              {batchStep === "parsed" && (
                <div className="space-y-4">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <p className="text-sm text-slate-600">
                      <strong>{batchRows.length}</strong> rows parsed. <strong>{batchRows.filter((r) => r.downloadable).length}</strong> have downloadable PDFs.
                      Select rows to process with AI extraction.
                    </p>
                    <div className="flex items-center gap-2">
                      <select
                        value={batchStateFilter}
                        onChange={(e) => setBatchStateFilter(e.target.value)}
                        className="px-2 py-1 border border-slate-200 rounded text-sm"
                      >
                        <option value="">All states</option>
                        {Array.from(new Set(batchRows.map((r) => r.state_abbr).filter(Boolean))).sort().map((st) => (
                          <option key={st} value={st}>{st}</option>
                        ))}
                      </select>
                      <button
                        type="button"
                        onClick={() => {
                          const filtered = batchRows.map((r, i) => ({ r, i })).filter(({ r }) => r.downloadable && (!batchStateFilter || r.state_abbr === batchStateFilter));
                          setBatchSelected(new Set(filtered.map(({ i }) => i)));
                        }}
                        className="px-2 py-1 text-xs text-primary-600 hover:underline"
                      >
                        Select all downloadable
                      </button>
                      <button
                        type="button"
                        onClick={() => setBatchSelected(new Set())}
                        className="px-2 py-1 text-xs text-slate-500 hover:underline"
                      >
                        Deselect all
                      </button>
                    </div>
                  </div>
                  <div className="border border-slate-200 rounded-lg overflow-hidden max-h-[50vh] overflow-y-auto">
                    <table className="w-full text-sm">
                      <thead className="sticky top-0 bg-slate-50 z-10">
                        <tr className="border-b border-slate-200">
                          <th className="w-10 py-2 px-2"></th>
                          <th className="text-left py-2 px-3 font-semibold text-slate-700">Docket</th>
                          <th className="text-left py-2 px-3 font-semibold text-slate-700">Property Name</th>
                          <th className="text-left py-2 px-3 font-semibold text-slate-700">State</th>
                          <th className="text-left py-2 px-3 font-semibold text-slate-700">County</th>
                          <th className="text-left py-2 px-3 font-semibold text-slate-700">Minerals</th>
                          <th className="text-left py-2 px-3 font-semibold text-slate-700">Size</th>
                          <th className="text-left py-2 px-3 font-semibold text-slate-700">Status</th>
                        </tr>
                      </thead>
                      <tbody>
                        {batchRows
                          .map((row, i) => ({ row, i }))
                          .filter(({ row }) => !batchStateFilter || row.state_abbr === batchStateFilter)
                          .map(({ row, i }) => (
                          <tr key={i} className="border-b border-slate-100 last:border-0 hover:bg-slate-50/50">
                            <td className="py-1.5 px-2">
                              <input
                                type="checkbox"
                                checked={batchSelected.has(i)}
                                disabled={!row.downloadable}
                                onChange={(e) => {
                                  const next = new Set(batchSelected);
                                  if (e.target.checked) next.add(i); else next.delete(i);
                                  setBatchSelected(next);
                                }}
                                className="rounded border-slate-300"
                              />
                            </td>
                            <td className="py-1.5 px-3 text-slate-600 font-mono text-xs">{row.docket}</td>
                            <td className="py-1.5 px-3 font-medium text-slate-900 max-w-[200px] truncate">{row.name}</td>
                            <td className="py-1.5 px-3 text-slate-600">{row.state_abbr}</td>
                            <td className="py-1.5 px-3 text-slate-600 text-xs">{row.county}</td>
                            <td className="py-1.5 px-3">
                              <div className="flex flex-wrap gap-0.5">
                                {row.minerals.slice(0, 3).map((m, mi) => (
                                  <span key={mi} className="inline-block px-1.5 py-0.5 bg-primary-100 text-primary-700 rounded text-xs">{m}</span>
                                ))}
                                {row.minerals.length > 3 && <span className="text-xs text-slate-400">+{row.minerals.length - 3}</span>}
                              </div>
                            </td>
                            <td className="py-1.5 px-3 text-xs text-slate-500">{row.file_size}</td>
                            <td className="py-1.5 px-3">
                              {row.downloadable ? (
                                <span className="inline-block px-1.5 py-0.5 bg-emerald-100 text-emerald-700 rounded text-xs">Ready</span>
                              ) : (
                                <span className="inline-block px-1.5 py-0.5 bg-slate-100 text-slate-500 rounded text-xs" title={row.skipped_reason || ""}>{row.skipped_reason?.includes("large") ? "Too large" : "No scan"}</span>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <div className="flex items-center justify-between pt-2">
                    <p className="text-sm text-slate-500">{batchSelected.size} selected for processing</p>
                    <div className="flex gap-2">
                      <button
                        type="button"
                        onClick={async () => {
                          const selectedRows = Array.from(batchSelected).map((i) => batchRows[i]);
                          const csvTargets = selectedRows.map((r) => ({
                            name: r.name,
                            state: r.state_abbr,
                            state_abbr: r.state_abbr,
                            county: r.county,
                            minerals: r.minerals,
                            notes: `OME Docket ${r.docket}. County: ${r.county}`,
                            url: r.url,
                          }));
                          setBatchImporting(true);
                          setBatchError("");
                          try {
                            const result = await api.batchReport.importTargets(csvTargets);
                            setBatchImportResult(result);
                            setBatchStep("done");
                          } catch (err) {
                            setBatchError(formatBatchNetworkError(err));
                          } finally {
                            setBatchImporting(false);
                          }
                        }}
                        disabled={batchSelected.size === 0 || batchImporting}
                        className="px-4 py-2 bg-slate-600 text-white rounded-lg text-sm font-medium hover:bg-slate-700 disabled:opacity-50"
                      >
                        {batchImporting ? "Importing…" : "Import as Targets (skip PDF)"}
                      </button>
                      <button
                        type="button"
                        onClick={async () => {
                          const selectedRows = Array.from(batchSelected)
                            .map((i) => batchRows[i])
                            .filter((r) => r.downloadable);
                          if (selectedRows.length === 0) return;
                          setBatchProcessing(true);
                          setBatchProgress(0);
                          setBatchError("");
                          setBatchStep("processing");
                          const allResults: BatchRow[] = [];
                          const CHUNK = 3;
                          for (let i = 0; i < selectedRows.length; i += CHUNK) {
                            const chunk = selectedRows.slice(i, i + CHUNK);
                            try {
                              const res = await api.batchReport.processRows(chunk);
                              allResults.push(...res.rows);
                            } catch (err) {
                              chunk.forEach((r) => allResults.push({ ...r, pdf_error: "Request failed", pdf_processed: false, pdf_targets: [] }));
                            }
                            setBatchProgress(Math.min(allResults.length, selectedRows.length));
                          }
                          setBatchResults(allResults);
                          setBatchProcessing(false);
                          setBatchStep("review");
                        }}
                        disabled={batchSelected.size === 0 || batchProcessing}
                        className="px-4 py-2 bg-primary-600 text-white rounded-lg text-sm font-medium hover:bg-primary-700 disabled:opacity-50"
                      >
                        Process with AI ({batchSelected.size} reports)
                      </button>
                    </div>
                  </div>
                </div>
              )}

              {batchStep === "processing" && (
                <div className="py-8 text-center space-y-4">
                  <div className="animate-spin w-8 h-8 border-4 border-primary-200 border-t-primary-600 rounded-full mx-auto"></div>
                  <p className="text-slate-700 font-medium">
                    Processing reports… {batchProgress} / {batchSelected.size}
                  </p>
                  <p className="text-xs text-slate-500">Downloading PDFs and extracting targets with AI. This may take several minutes.</p>
                  <div className="w-full bg-slate-200 rounded-full h-2 max-w-md mx-auto">
                    <div
                      className="bg-primary-600 h-2 rounded-full transition-all"
                      style={{ width: `${batchSelected.size > 0 ? (batchProgress / batchSelected.size) * 100 : 0}%` }}
                    ></div>
                  </div>
                </div>
              )}

              {batchStep === "review" && (
                <div className="space-y-4">
                  <p className="text-xs text-amber-900 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
                    <span className="font-medium">PLSS required.</span> Only targets with a PLSS string or both township and range are saved.
                    Rows with no PDF extraction (metadata only) or AI output without location are skipped on import.
                  </p>
                  <div className="flex flex-wrap items-center gap-4 text-sm">
                    <span className="font-medium text-emerald-700">{batchResults.filter((r) => r.pdf_processed).length} processed</span>
                    <span className="font-medium text-slate-600">{batchResults.reduce((acc, r) => acc + (r.pdf_targets?.length || 0), 0)} targets extracted</span>
                    <span className="text-red-600">{batchResults.filter((r) => r.pdf_error).length} PDF/read errors</span>
                    <span className="text-amber-700">{batchResults.filter((r) => r.pdf_processed && !r.pdf_error && (r.pdf_targets?.length ?? 0) === 0 && r.pdf_note).length} no targets (PDF OK)</span>
                    <button
                      type="button"
                      onClick={() => setBatchExpandedRows(batchExpandedRows.size > 0 ? new Set() : new Set(batchResults.map((_, i) => i)))}
                      className="ml-auto text-xs text-primary-600 hover:underline"
                    >
                      {batchExpandedRows.size > 0 ? "Collapse all" : "Expand all"}
                    </button>
                  </div>
                  <div className="border border-slate-200 rounded-lg overflow-hidden max-h-[60vh] overflow-y-auto">
                    <table className="w-full text-sm">
                      <thead className="sticky top-0 bg-slate-50 z-10">
                        <tr className="border-b border-slate-200">
                          <th className="w-8 py-2 px-2"></th>
                          <th className="text-left py-2 px-3 font-semibold text-slate-700">Docket</th>
                          <th className="text-left py-2 px-3 font-semibold text-slate-700">Property</th>
                          <th className="text-left py-2 px-3 font-semibold text-slate-700">State</th>
                          <th className="text-left py-2 px-3 font-semibold text-slate-700">County</th>
                          <th className="text-left py-2 px-3 font-semibold text-slate-700">Minerals</th>
                          <th className="text-left py-2 px-3 font-semibold text-slate-700">PDF</th>
                          <th className="text-left py-2 px-3 font-semibold text-slate-700">Targets</th>
                          <th className="text-left py-2 px-3 font-semibold text-slate-700">Location Preview</th>
                        </tr>
                      </thead>
                      <tbody>
                        {batchResults.map((row, i) => {
                          const isExpanded = batchExpandedRows.has(i);
                          const targets = row.pdf_targets || [];
                          const firstTarget = targets[0];
                          const locationPreview = firstTarget
                            ? (firstTarget.plss || (firstTarget.latitude != null ? `${firstTarget.latitude?.toFixed(3)}, ${firstTarget.longitude?.toFixed(3)}` : ""))
                            : "";
                          return (
                            <React.Fragment key={i}>
                              <tr
                                className={`border-b border-slate-100 hover:bg-slate-50/50 cursor-pointer ${isExpanded ? "bg-primary-50/30" : ""}`}
                                onClick={() => {
                                  const next = new Set(batchExpandedRows);
                                  if (isExpanded) next.delete(i); else next.add(i);
                                  setBatchExpandedRows(next);
                                }}
                              >
                                <td className="py-1.5 px-2 text-slate-400 text-xs">{isExpanded ? "▼" : "▶"}</td>
                                <td className="py-1.5 px-3 font-mono text-xs text-slate-600">{row.docket}</td>
                                <td className="py-1.5 px-3 font-medium text-slate-900 max-w-[180px] truncate">{row.name}</td>
                                <td className="py-1.5 px-3 text-slate-600">{row.state_abbr}</td>
                                <td className="py-1.5 px-3 text-slate-500 text-xs">{row.county}</td>
                                <td className="py-1.5 px-3">
                                  <div className="flex flex-wrap gap-0.5">
                                    {row.minerals.slice(0, 2).map((m, mi) => (
                                      <span key={mi} className="inline-block px-1.5 py-0.5 bg-primary-100 text-primary-700 rounded text-xs">{m}</span>
                                    ))}
                                    {row.minerals.length > 2 && <span className="text-xs text-slate-400">+{row.minerals.length - 2}</span>}
                                  </div>
                                </td>
                                <td className="py-1.5 px-3">
                                  {row.pdf_error ? (
                                    <span className="inline-block px-1.5 py-0.5 bg-red-100 text-red-700 rounded text-xs max-w-[14rem] truncate align-middle" title={row.pdf_error}>
                                      {row.pdf_error.length > 24 ? row.pdf_error.slice(0, 22) + "…" : row.pdf_error}
                                    </span>
                                  ) : row.pdf_processed && (row.pdf_targets?.length ?? 0) === 0 && row.pdf_note ? (
                                    <span className="inline-block px-1.5 py-0.5 bg-amber-100 text-amber-900 rounded text-xs" title={row.pdf_note}>
                                      Read OK · 0 targets
                                    </span>
                                  ) : row.pdf_processed ? (
                                    <span className="inline-block px-1.5 py-0.5 bg-emerald-100 text-emerald-700 rounded text-xs">OK</span>
                                  ) : (
                                    <span className="inline-block px-1.5 py-0.5 bg-slate-100 text-slate-600 rounded text-xs">—</span>
                                  )}
                                </td>
                                <td className="py-1.5 px-3 text-slate-700 font-medium">{targets.length}</td>
                                <td className="py-1.5 px-3 text-xs text-slate-500 font-mono max-w-[160px] truncate" title={locationPreview}>
                                  {locationPreview || "—"}
                                </td>
                              </tr>
                              {isExpanded && targets.length > 0 && (
                                <tr>
                                  <td colSpan={9} className="p-0">
                                    <div className="bg-slate-50/70 border-t border-b border-slate-200 px-6 py-3">
                                      <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2">
                                        Extracted from PDF — {targets.length} target{targets.length !== 1 ? "s" : ""}
                                      </p>
                                      <div className="space-y-2">
                                        {targets.map((t, ti) => (
                                          <div key={ti} className="bg-white rounded-lg border border-slate-200 px-4 py-3">
                                            <div className="flex items-start justify-between gap-3">
                                              <div className="flex-1 min-w-0">
                                                <p className="font-medium text-slate-900">{t.name}</p>
                                                <div className="flex flex-wrap gap-x-4 gap-y-1 mt-1 text-xs text-slate-600">
                                                  {t.plss && (
                                                    <span title="PLSS"><span className="font-semibold text-slate-500">PLSS:</span> {t.plss}</span>
                                                  )}
                                                  {t.latitude != null && t.longitude != null && (
                                                    <span title="Coordinates"><span className="font-semibold text-slate-500">Coords:</span> {t.latitude?.toFixed(4)}, {t.longitude?.toFixed(4)}</span>
                                                  )}
                                                  {t.state && (
                                                    <span><span className="font-semibold text-slate-500">State:</span> {t.state}</span>
                                                  )}
                                                  {t.county && (
                                                    <span><span className="font-semibold text-slate-500">County:</span> {t.county}</span>
                                                  )}
                                                </div>
                                                {(t.minerals || []).length > 0 && (
                                                  <div className="flex flex-wrap gap-1 mt-1.5">
                                                    {t.minerals!.map((m, mi) => (
                                                      <span key={mi} className="inline-block px-1.5 py-0.5 bg-primary-100 text-primary-700 rounded text-xs">{m}</span>
                                                    ))}
                                                  </div>
                                                )}
                                                {t.notes && (
                                                  <p className="mt-1.5 text-xs text-slate-500 italic">{t.notes}</p>
                                                )}
                                              </div>
                                              <div className="shrink-0 flex flex-col items-end gap-1">
                                                {t.plss && <span className="inline-block px-1.5 py-0.5 bg-blue-100 text-blue-700 rounded text-xs">PLSS</span>}
                                                {t.latitude != null && <span className="inline-block px-1.5 py-0.5 bg-emerald-100 text-emerald-700 rounded text-xs">Coords</span>}
                                                {!t.plss && t.latitude == null && <span className="inline-block px-1.5 py-0.5 bg-amber-100 text-amber-700 rounded text-xs">No location</span>}
                                              </div>
                                            </div>
                                          </div>
                                        ))}
                                      </div>
                                    </div>
                                  </td>
                                </tr>
                              )}
                              {isExpanded && targets.length === 0 && (
                                <tr>
                                  <td colSpan={9} className="p-0">
                                    <div className="bg-slate-50/70 border-t border-b border-slate-200 px-6 py-3">
                                      <p className="text-xs text-slate-500">
                                        {row.pdf_error ? (
                                          <span className="text-red-600">{row.pdf_error}</span>
                                        ) : row.pdf_note ? (
                                          <span className="text-amber-900">{row.pdf_note}</span>
                                        ) : (
                                          "No targets extracted from this report."
                                        )}
                                      </p>
                                      {row.pdf_error && (row.pdf_document_opened === false || row.had_extractable_text === false) && (
                                        <p className="text-[11px] text-slate-500 mt-2">
                                          Historic USGS scans are often image-only. For OCR on the server, install Tesseract and run{" "}
                                          <code className="bg-slate-200 px-0.5 rounded">pip install &apos;mining-os[pdf-ocr]&apos;</code>.
                                        </p>
                                      )}
                                      <p className="text-xs text-slate-400 mt-1">
                                        CSV metadata: {row.name} — {row.state_abbr}{row.county ? `, ${row.county}` : ""}{row.minerals.length > 0 ? ` — ${row.minerals.join(", ")}` : ""}
                                      </p>
                                    </div>
                                  </td>
                                </tr>
                              )}
                            </React.Fragment>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                  <div className="flex justify-end gap-2 pt-2">
                    <button
                      type="button"
                      onClick={async () => {
                        const allTargets: Record<string, unknown>[] = [];
                        for (const row of batchResults) {
                          if (row.pdf_targets && row.pdf_targets.length > 0) {
                            for (const t of row.pdf_targets) {
                              allTargets.push({ ...t, url: row.url, report_url: row.url });
                            }
                          } else {
                            allTargets.push({
                              name: row.name,
                              state: row.state_abbr,
                              state_abbr: row.state_abbr,
                              county: row.county,
                              minerals: row.minerals,
                              notes: `${row.report_series || "OME"} Docket ${row.docket}. ${row.pdf_error ? `PDF: ${row.pdf_error}` : row.pdf_note ? `Note: ${row.pdf_note}` : ""}`.trim(),
                              url: row.url,
                            });
                          }
                        }
                        setBatchImporting(true);
                        setBatchError("");
                        try {
                          const result = await api.batchReport.importTargets(allTargets);
                          setBatchImportResult(result);
                          setBatchStep("done");
                        } catch (err) {
                          setBatchError(formatBatchNetworkError(err));
                        } finally {
                          setBatchImporting(false);
                        }
                      }}
                      disabled={batchImporting || batchImportPreview.importable === 0}
                      className="px-4 py-2 bg-primary-600 text-white rounded-lg text-sm font-medium hover:bg-primary-700 disabled:opacity-50"
                    >
                      {batchImporting
                        ? "Importing…"
                        : batchImportPreview.importable === 0
                          ? "Nothing to import (no PLSS on any row)"
                          : `Import ${batchImportPreview.importable} with PLSS${
                              batchImportPreview.skipped > 0 ? ` (skip ${batchImportPreview.skipped} without location)` : ""
                            }`}
                    </button>
                  </div>
                </div>
              )}

              {batchStep === "done" && batchImportResult && (
                <div className="py-8 text-center space-y-4">
                  <div className="w-12 h-12 bg-emerald-100 rounded-full flex items-center justify-center mx-auto">
                    <svg className="w-6 h-6 text-emerald-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                    </svg>
                  </div>
                  <p className="text-lg font-medium text-slate-900">
                    {batchImportResult.imported} targets imported
                  </p>
                  {batchImportResult.note && (
                    <p className="text-sm text-slate-600 max-w-md mx-auto">{batchImportResult.note}</p>
                  )}
                  {batchImportResult.skipped && batchImportResult.skipped.length > 0 && (
                    <div className="text-sm text-amber-800 max-w-lg mx-auto text-left">
                      <p className="font-medium mb-1">Skipped ({batchImportResult.skipped.length}) — no PLSS</p>
                      <ul className="list-disc pl-5 space-y-0.5 max-h-32 overflow-y-auto text-xs">
                        {batchImportResult.skipped.slice(0, 20).map((s, i) => (
                          <li key={i}>
                            {s.name}: {s.reason}
                          </li>
                        ))}
                      </ul>
                      {batchImportResult.skipped.length > 20 && (
                        <p className="text-xs mt-1">…and {batchImportResult.skipped.length - 20} more</p>
                      )}
                    </div>
                  )}
                  {batchImportResult.errors.length > 0 && (
                    <div className="text-sm text-red-600">
                      {batchImportResult.errors.slice(0, 5).map((e, i) => <p key={i}>{e}</p>)}
                      {batchImportResult.errors.length > 5 && <p>…and {batchImportResult.errors.length - 5} more</p>}
                    </div>
                  )}
                  <button
                    type="button"
                    onClick={() => {
                      setBatchOpen(false);
                      window.location.reload();
                    }}
                    className="px-4 py-2 bg-slate-700 text-white rounded-lg text-sm font-medium hover:bg-slate-800"
                  >
                    Done
                  </button>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

    </div>
  );
}
