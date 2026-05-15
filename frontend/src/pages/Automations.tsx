import { useEffect, useState } from "react";
import {
  automations,
  type AutomationRule,
  type AutomationRun,
  type AutomationMeta,
  type AutomationRunLogRow,
} from "../api";

const ACTION_LABELS: Record<string, string> = {
  fetch_claim_records: "Fetch Claim Records (MLRS)",
  lr2000_report: "LR2000 / Geographic Index",
  check_blm_status: "Check BLM Status",
  generate_report: "Generate Report",
};

const OUTCOME_LABELS: Record<string, string> = {
  log_only: "Log only",
  email_always: "Email after every run",
  email_on_change: "Email only when changes detected",
  email_on_error: "Email only on errors",
};

const PRIORITY_OPTIONS = [
  { value: "", label: "Any" },
  { value: "monitoring_low", label: "Monitoring - Low" },
  { value: "monitoring_med", label: "Monitoring - Med" },
  { value: "monitoring_high", label: "Monitoring - High" },
  { value: "negotiation", label: "Negotiation" },
  { value: "due_diligence", label: "Due Diligence" },
  { value: "ownership", label: "Ownership" },
];

const CRON_PRESETS = [
  { label: "On-demand only", value: "" },
  { label: "Every day at 8am UTC", value: "0 8 * * *" },
  { label: "Every Monday at 8am UTC", value: "0 8 * * 1" },
  { label: "Every hour", value: "0 * * * *" },
  { label: "Every 6 hours", value: "0 */6 * * *" },
  { label: "Custom", value: "__custom__" },
];

const INCLUDE_EXISTING_CLAIM_STATUS_KEY = "include_targets_with_claim_status";

type Tab = "rules" | "runs";
type ModalMode = "create" | "edit";

export function Automations() {
  const [tab, setTab] = useState<Tab>("rules");
  const [meta, setMeta] = useState<AutomationMeta | null>(null);
  const [rules, setRules] = useState<AutomationRule[]>([]);
  const [runs, setRuns] = useState<AutomationRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [runsFilterRuleId, setRunsFilterRuleId] = useState<number | null>(null);

  // Modal state
  const [modalOpen, setModalOpen] = useState(false);
  const [modalMode, setModalMode] = useState<ModalMode>("create");
  const [editId, setEditId] = useState<number | null>(null);
  const [formName, setFormName] = useState("");
  const [formAction, setFormAction] = useState("fetch_claim_records");
  const [formOutcome, setFormOutcome] = useState("log_only");
  const [formCronPreset, setFormCronPreset] = useState("");
  const [formCronCustom, setFormCronCustom] = useState("");
  const [formMaxTargets, setFormMaxTargets] = useState(50);
  const [formEnabled, setFormEnabled] = useState(true);
  const [formFilterPriority, setFormFilterPriority] = useState("");
  const [formFilterMineral, setFormFilterMineral] = useState("");
  const [formFilterStatus, setFormFilterStatus] = useState("");
  const [formFilterState, setFormFilterState] = useState("");
  const [formFilterName, setFormFilterName] = useState("");
  const [formIncludeTargetsWithClaimStatus, setFormIncludeTargetsWithClaimStatus] = useState(false);
  const [formSaving, setFormSaving] = useState(false);

  // Run detail modal
  const [runDetail, setRunDetail] = useState<AutomationRun | null>(null);

  // Trigger state
  const [triggering, setTriggering] = useState<number | null>(null);

  const loadRules = async () => {
    try {
      const [m, r] = await Promise.all([automations.meta(), automations.listRules()]);
      setMeta(m);
      setRules(r);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load rules");
    }
  };

  const loadRuns = async () => {
    try {
      const r = await automations.listRuns({
        rule_id: runsFilterRuleId ?? undefined,
        limit: 200,
      });
      setRuns(r);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load runs");
    }
  };

  const loadRunDetail = async (runId: number) => {
    try {
      const run = await automations.getRun(runId);
      setRunDetail(run);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load run");
    }
  };

  const loadAll = async () => {
    setLoading(true);
    setError(null);
    await Promise.all([loadRules(), loadRuns()]);
    setLoading(false);
  };

  useEffect(() => {
    void loadAll();
  }, []);

  useEffect(() => {
    if (tab === "runs") void loadRuns();
  }, [runsFilterRuleId, tab]);

  useEffect(() => {
    const shouldPoll =
      tab === "runs" &&
      (runs.some((run) => run.status === "running") || runDetail?.status === "running");
    if (!shouldPoll) return;

    const timer = window.setInterval(() => {
      void loadRuns();
      if (runDetail?.id) void loadRunDetail(runDetail.id);
    }, 3000);

    return () => window.clearInterval(timer);
  }, [runs, runDetail?.id, runDetail?.status, tab]);

  const openCreate = () => {
    setModalMode("create");
    setEditId(null);
    setFormName("");
    setFormAction("fetch_claim_records");
    setFormOutcome("log_only");
    setFormCronPreset("");
    setFormCronCustom("");
    setFormMaxTargets(50);
    setFormEnabled(true);
    setFormFilterPriority("");
    setFormFilterMineral("");
    setFormFilterStatus("");
    setFormFilterState("");
    setFormFilterName("");
    setFormIncludeTargetsWithClaimStatus(false);
    setModalOpen(true);
  };

  const openEdit = (rule: AutomationRule) => {
    setModalMode("edit");
    setEditId(rule.id);
    setFormName(rule.name);
    setFormAction(rule.action_type);
    setFormOutcome(rule.outcome_type);
    const cron = rule.schedule_cron || "";
    const preset = CRON_PRESETS.find((p) => p.value === cron && p.value !== "__custom__");
    if (preset) {
      setFormCronPreset(cron);
      setFormCronCustom("");
    } else if (cron) {
      setFormCronPreset("__custom__");
      setFormCronCustom(cron);
    } else {
      setFormCronPreset("");
      setFormCronCustom("");
    }
    setFormMaxTargets(rule.max_targets);
    setFormEnabled(rule.enabled);
    const fc = rule.filter_config || {};
    setFormFilterPriority(typeof fc.priority === "string" ? fc.priority : "");
    setFormFilterMineral(typeof fc.mineral === "string" ? fc.mineral : "");
    setFormFilterStatus(typeof fc.status === "string" ? fc.status : "");
    setFormFilterState(typeof fc.state_abbr === "string" ? fc.state_abbr : "");
    setFormFilterName(typeof fc.name === "string" ? fc.name : "");
    setFormIncludeTargetsWithClaimStatus(Boolean(fc[INCLUDE_EXISTING_CLAIM_STATUS_KEY]));
    setModalOpen(true);
  };

  const saveRule = async () => {
    setFormSaving(true);
    setError(null);
    const cron = formCronPreset === "__custom__" ? formCronCustom.trim() : formCronPreset;
    const filter_config: Record<string, string | boolean> = {};
    if (formFilterPriority) filter_config.priority = formFilterPriority;
    if (formFilterMineral.trim()) filter_config.mineral = formFilterMineral.trim();
    if (formFilterStatus) filter_config.status = formFilterStatus;
    if (formFilterState.trim()) filter_config.state_abbr = formFilterState.trim();
    if (formFilterName.trim()) filter_config.name = formFilterName.trim();
    if (formAction === "fetch_claim_records" && formIncludeTargetsWithClaimStatus) {
      filter_config[INCLUDE_EXISTING_CLAIM_STATUS_KEY] = true;
    }

    try {
      if (modalMode === "create") {
        await automations.createRule({
          name: formName.trim(),
          action_type: formAction,
          outcome_type: formOutcome,
          schedule_cron: cron || null,
          max_targets: formMaxTargets,
          enabled: formEnabled,
          filter_config,
        });
      } else if (editId) {
        await automations.updateRule(editId, {
          name: formName.trim(),
          action_type: formAction,
          outcome_type: formOutcome,
          schedule_cron: cron || null,
          max_targets: formMaxTargets,
          enabled: formEnabled,
          filter_config,
        } as Partial<AutomationRule>);
      }
      setModalOpen(false);
      await loadRules();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Save failed");
    } finally {
      setFormSaving(false);
    }
  };

  const deleteRule = async (id: number) => {
    if (!confirm("Delete this automation rule? All run history for it will also be deleted.")) return;
    try {
      await automations.deleteRule(id);
      await loadAll();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Delete failed");
    }
  };

  const triggerRule = async (id: number) => {
    setTriggering(id);
    setError(null);
    try {
      const res = await automations.triggerRule(id);
      if (!res.ok && res.error) {
        setError(res.error);
      }
      setTab("runs");
      await loadRuns();
      if (res.run_id) {
        await loadRunDetail(res.run_id);
      }
      await loadRules();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Trigger failed");
    } finally {
      setTriggering(null);
    }
  };

  const toggleEnabled = async (rule: AutomationRule) => {
    try {
      await automations.updateRule(rule.id, { enabled: !rule.enabled } as Partial<AutomationRule>);
      await loadRules();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Toggle failed");
    }
  };

  const fmtDate = (s: string | null | undefined) => {
    if (!s) return "—";
    try {
      return new Date(s).toLocaleString();
    } catch {
      return s;
    }
  };

  const visibleFilterEntries = (rule: AutomationRule) =>
    Object.entries(rule.filter_config || {}).filter(([k]) => k !== INCLUDE_EXISTING_CLAIM_STATUS_KEY);

  return (
    <div className="max-w-7xl mx-auto px-6 py-8 space-y-6">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">Automations</h1>
          <p className="text-sm text-slate-500 mt-1">
            Define rules to automatically run actions on your targets on a schedule or on-demand.
            {meta?.scheduler_running && (
              <span className="ml-2 inline-flex items-center gap-1 text-xs text-emerald-700">
                <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse inline-block" />
                Scheduler running
              </span>
            )}
          </p>
        </div>
        <button
          type="button"
          onClick={openCreate}
          className="px-4 py-2 bg-slate-800 text-white rounded-lg text-sm font-medium hover:bg-slate-900"
        >
          + New rule
        </button>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-3 text-sm text-red-800 flex justify-between items-start gap-2">
          <pre className="whitespace-pre-wrap break-words flex-1">{error}</pre>
          <button type="button" onClick={() => setError(null)} className="text-red-500 hover:text-red-700 shrink-0">&times;</button>
        </div>
      )}

      {/* Tabs */}
      <div className="flex gap-2 border-b border-slate-200">
        <button
          type="button"
          onClick={() => setTab("rules")}
          className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
            tab === "rules"
              ? "border-slate-800 text-slate-900"
              : "border-transparent text-slate-500 hover:text-slate-700"
          }`}
        >
          Rules ({rules.length})
        </button>
        <button
          type="button"
          onClick={() => setTab("runs")}
          className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
            tab === "runs"
              ? "border-slate-800 text-slate-900"
              : "border-transparent text-slate-500 hover:text-slate-700"
          }`}
        >
          Run history
        </button>
      </div>

      {loading ? (
        <div className="py-12 text-center text-slate-500">Loading…</div>
      ) : tab === "rules" ? (
        /* ---- RULES TAB ---- */
        rules.length === 0 ? (
          <div className="py-12 text-center text-slate-500">
            No automation rules yet. Click <strong>+ New rule</strong> to create one.
          </div>
        ) : (
          <div className="space-y-3">
            {rules.map((rule) => (
              <div
                key={rule.id}
                className={`bg-white border rounded-xl p-4 shadow-sm ${
                  rule.enabled ? "border-slate-200" : "border-slate-100 opacity-60"
                }`}
              >
                <div className="flex items-start justify-between gap-3 flex-wrap">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <h3 className="font-semibold text-slate-900 truncate">{rule.name}</h3>
                      <span
                        className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${
                          rule.enabled
                            ? "bg-emerald-100 text-emerald-800"
                            : "bg-slate-100 text-slate-500"
                        }`}
                      >
                        {rule.enabled ? "Enabled" : "Disabled"}
                      </span>
                    </div>
                    <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-500">
                      <span>
                        Action: <span className="text-slate-700 font-medium">{ACTION_LABELS[rule.action_type] || rule.action_type}</span>
                      </span>
                      <span>
                        Outcome: <span className="text-slate-700">{OUTCOME_LABELS[rule.outcome_type] || rule.outcome_type}</span>
                      </span>
                      <span>
                        Schedule:{" "}
                        <span className="text-slate-700 font-mono">
                          {rule.schedule_cron || "on-demand"}
                        </span>
                      </span>
                      <span>Max targets: {rule.max_targets}</span>
                    </div>
                    {rule.action_type === "fetch_claim_records" && (
                      <div className="mt-1 text-xs text-slate-500">
                        Existing claim status:
                        <span className="ml-1 text-slate-700">
                          {rule.filter_config?.[INCLUDE_EXISTING_CLAIM_STATUS_KEY]
                            ? "Include paid/unpaid targets"
                            : "Skip paid/unpaid targets by default"}
                        </span>
                      </div>
                    )}
                    {visibleFilterEntries(rule).length > 0 && (
                      <div className="mt-1 text-xs text-slate-500">
                        Filters:{" "}
                        {visibleFilterEntries(rule)
                          .map(([k, v]) => `${k}=${v}`)
                          .join(", ")}
                      </div>
                    )}
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <button
                      type="button"
                      disabled={triggering === rule.id}
                      onClick={() => triggerRule(rule.id)}
                      className="px-3 py-1.5 text-xs font-medium text-white bg-emerald-700 rounded-lg hover:bg-emerald-800 disabled:opacity-50"
                    >
                      {triggering === rule.id ? "Running…" : "Run now"}
                    </button>
                    <button
                      type="button"
                      onClick={() => toggleEnabled(rule)}
                      className="px-3 py-1.5 text-xs font-medium text-slate-700 bg-white border border-slate-200 rounded-lg hover:bg-slate-50"
                    >
                      {rule.enabled ? "Disable" : "Enable"}
                    </button>
                    <button
                      type="button"
                      onClick={() => openEdit(rule)}
                      className="px-3 py-1.5 text-xs font-medium text-slate-700 bg-white border border-slate-200 rounded-lg hover:bg-slate-50"
                    >
                      Edit
                    </button>
                    <button
                      type="button"
                      onClick={() => deleteRule(rule.id)}
                      className="px-3 py-1.5 text-xs font-medium text-red-700 bg-white border border-red-200 rounded-lg hover:bg-red-50"
                    >
                      Delete
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )
      ) : (
        /* ---- RUNS TAB ---- */
        <div className="space-y-4">
          <div className="flex items-center gap-3 flex-wrap">
            <label className="flex items-center gap-2 text-sm text-slate-600">
              Filter by rule:
              <select
                value={runsFilterRuleId ?? ""}
                onChange={(e) => setRunsFilterRuleId(e.target.value ? parseInt(e.target.value, 10) : null)}
                className="px-3 py-1.5 border border-slate-200 rounded-lg text-sm"
              >
                <option value="">All rules</option>
                {rules.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.name}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              onClick={() => void loadRuns()}
              className="px-3 py-1.5 text-xs font-medium text-slate-700 bg-white border border-slate-200 rounded-lg hover:bg-slate-50"
            >
              Refresh
            </button>
          </div>
          {runs.length === 0 ? (
            <div className="py-12 text-center text-slate-500">No runs recorded yet.</div>
          ) : (
            <div className="bg-white border border-slate-200 rounded-xl shadow-sm overflow-hidden">
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="bg-slate-50 border-b border-slate-200">
                      <th className="text-left py-2.5 px-3 font-semibold text-slate-700">Run #</th>
                      <th className="text-left py-2.5 px-3 font-semibold text-slate-700">Rule</th>
                      <th className="text-left py-2.5 px-3 font-semibold text-slate-700">Action</th>
                      <th className="text-left py-2.5 px-3 font-semibold text-slate-700">Trigger</th>
                      <th className="text-left py-2.5 px-3 font-semibold text-slate-700">Started</th>
                      <th className="text-left py-2.5 px-3 font-semibold text-slate-700">Status</th>
                      <th className="text-left py-2.5 px-3 font-semibold text-slate-700">Targets</th>
                      <th className="text-left py-2.5 px-3 font-semibold text-slate-700">Changes</th>
                      <th className="text-left py-2.5 px-3 font-semibold text-slate-700">Email</th>
                      <th className="text-left py-2.5 px-3 font-semibold text-slate-700"></th>
                    </tr>
                  </thead>
                  <tbody>
                    {runs.map((run) => (
                      <tr key={run.id} className="border-b border-slate-100 last:border-0 hover:bg-slate-50">
                        <td className="py-2 px-3 font-mono text-xs">{run.id}</td>
                        <td className="py-2 px-3 truncate max-w-[10rem]">{run.rule_name || `#${run.rule_id}`}</td>
                        <td className="py-2 px-3 text-xs">{ACTION_LABELS[run.action_type ?? ""] || run.action_type}</td>
                        <td className="py-2 px-3">
                          <span
                            className={`inline-flex px-2 py-0.5 rounded text-xs font-medium ${
                              run.trigger_type === "scheduled"
                                ? "bg-blue-100 text-blue-800"
                                : "bg-slate-100 text-slate-700"
                            }`}
                          >
                            {run.trigger_type}
                          </span>
                        </td>
                        <td className="py-2 px-3 text-xs text-slate-600 whitespace-nowrap">{fmtDate(run.started_at)}</td>
                        <td className="py-2 px-3">
                          <span
                            className={`inline-flex px-2 py-0.5 rounded text-xs font-medium ${
                              run.status === "completed"
                                ? "bg-emerald-100 text-emerald-800"
                                : run.status === "failed"
                                ? "bg-red-100 text-red-800"
                                : "bg-amber-100 text-amber-800"
                            }`}
                          >
                            {run.status}
                          </span>
                        </td>
                        <td className="py-2 px-3 text-xs">
                          {(run.results?.length ?? 0)}/{run.targets_total}
                          {run.targets_err > 0 && (
                            <span className="text-red-600 ml-1">({run.targets_err} err)</span>
                          )}
                        </td>
                        <td className="py-2 px-3 text-xs">{run.changes_found}</td>
                        <td className="py-2 px-3 text-xs">{run.email_sent ? "Yes" : "—"}</td>
                        <td className="py-2 px-3">
                          <button
                            type="button"
                            onClick={() => setRunDetail(run)}
                            className="text-xs text-primary-600 hover:underline font-medium"
                          >
                            Details
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Create/Edit Rule Modal */}
      {modalOpen && (
        <div
          className="fixed inset-0 z-[60] flex items-center justify-center p-4 bg-black/50"
          onClick={() => !formSaving && setModalOpen(false)}
          role="dialog"
          aria-modal="true"
        >
          <div
            className="bg-white rounded-xl shadow-xl max-w-lg w-full max-h-[90vh] flex flex-col"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="p-4 border-b border-slate-200 flex justify-between items-center shrink-0">
              <h3 className="font-semibold text-slate-900">
                {modalMode === "create" ? "New automation rule" : "Edit rule"}
              </h3>
              <button
                type="button"
                disabled={formSaving}
                onClick={() => setModalOpen(false)}
                className="text-slate-500 hover:text-slate-700 text-xl leading-none"
              >
                &times;
              </button>
            </div>
            <div className="p-4 overflow-y-auto flex-1 min-h-0 space-y-4 text-sm">
              <p className="text-xs text-slate-500 leading-relaxed bg-slate-50 rounded-lg px-3 py-2">
                A rule defines an automated workflow: pick a set of targets using filters, choose an action to run against each one, and decide whether to be notified. Rules can run on a cron schedule or be triggered manually whenever you need fresh data.
              </p>

              <label className="flex flex-col gap-1">
                <span className="font-medium text-slate-700">Rule name</span>
                <input
                  type="text"
                  value={formName}
                  onChange={(e) => setFormName(e.target.value)}
                  placeholder="e.g. Weekly high-priority BLM check"
                  className="px-3 py-2 border border-slate-200 rounded-lg text-sm focus:ring-2 focus:ring-primary-500"
                />
                <span className="text-xs text-slate-400">A short, descriptive label so you can identify this rule at a glance.</span>
              </label>

              <label className="flex flex-col gap-1">
                <span className="font-medium text-slate-700">Action</span>
                <select
                  value={formAction}
                  onChange={(e) => setFormAction(e.target.value)}
                  className="px-3 py-2 border border-slate-200 rounded-lg text-sm"
                >
                  {Object.entries(ACTION_LABELS).map(([k, v]) => (
                    <option key={k} value={k}>
                      {v}
                    </option>
                  ))}
                </select>
                <span className="text-xs text-slate-400">
                  {formAction === "fetch_claim_records"
                    ? "Queries BLM MLRS for the latest claim records associated with each target's PLSS location."
                    : formAction === "lr2000_report"
                    ? "Runs a geographic index query against BLM's LR2000 FeatureServer to find nearby mining claims."
                    : formAction === "check_blm_status"
                    ? "Checks BLM land status at each target's coordinates to detect payment or status changes."
                    : "Generates a summary report for each target pulling together available data."}
                </span>
              </label>

              {formAction === "fetch_claim_records" && (
                <label className="flex items-start gap-2 cursor-pointer rounded-lg border border-slate-200 p-3 bg-slate-50">
                  <input
                    type="checkbox"
                    checked={formIncludeTargetsWithClaimStatus}
                    onChange={(e) => setFormIncludeTargetsWithClaimStatus(e.target.checked)}
                    className="rounded border-slate-300 mt-0.5"
                  />
                  <span className="text-sm text-slate-700">
                    Include targets that already have claim status.
                    <span className="block text-xs text-slate-500 mt-1">
                      Off by default. When unchecked, this automation will skip targets already marked
                      paid or unpaid so it does not overwrite existing claim-status decisions unless you opt in.
                    </span>
                  </span>
                </label>
              )}

              <label className="flex flex-col gap-1">
                <span className="font-medium text-slate-700">After run (outcome)</span>
                <select
                  value={formOutcome}
                  onChange={(e) => setFormOutcome(e.target.value)}
                  className="px-3 py-2 border border-slate-200 rounded-lg text-sm"
                >
                  {Object.entries(OUTCOME_LABELS).map(([k, v]) => (
                    <option key={k} value={k}>
                      {v}
                    </option>
                  ))}
                </select>
                <span className="text-xs text-slate-400">
                  Controls when you receive an email summary. "Log only" still records every run in the Run History tab.
                </span>
              </label>

              <label className="flex flex-col gap-1">
                <span className="font-medium text-slate-700">Schedule</span>
                <select
                  value={formCronPreset}
                  onChange={(e) => {
                    setFormCronPreset(e.target.value);
                    if (e.target.value !== "__custom__") setFormCronCustom("");
                  }}
                  className="px-3 py-2 border border-slate-200 rounded-lg text-sm"
                >
                  {CRON_PRESETS.map((p) => (
                    <option key={p.value} value={p.value}>
                      {p.label}
                    </option>
                  ))}
                </select>
                {formCronPreset === "__custom__" && (
                  <input
                    type="text"
                    value={formCronCustom}
                    onChange={(e) => setFormCronCustom(e.target.value)}
                    placeholder="0 8 * * 1  (minute hour day month weekday)"
                    className="mt-1 px-3 py-2 border border-slate-200 rounded-lg text-sm font-mono"
                  />
                )}
                <span className="text-xs text-slate-400">
                  Leave on-demand to trigger manually via the "Run now" button. Scheduled rules fire automatically while the server is running.
                </span>
              </label>

              <label className="flex flex-col gap-1">
                <span className="font-medium text-slate-700">Max targets per run</span>
                <input
                  type="number"
                  min={1}
                  max={200}
                  value={formMaxTargets}
                  onChange={(e) => setFormMaxTargets(parseInt(e.target.value, 10) || 50)}
                  className="px-3 py-2 border border-slate-200 rounded-lg text-sm w-28"
                />
                <span className="text-xs text-slate-400">
                  Caps how many matching targets are processed per execution to keep API calls manageable (max 200).
                </span>
              </label>

              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={formEnabled}
                  onChange={(e) => setFormEnabled(e.target.checked)}
                  className="rounded border-slate-300"
                />
                <span className="font-medium text-slate-700">Enabled</span>
              </label>

              <div className="border-t border-slate-200 pt-4 space-y-3">
                <span className="font-medium text-slate-700 block">Target filters</span>
                <p className="text-xs text-slate-500">
                  Narrow which targets this rule runs on. Leave blank to include all targets.
                </p>
                <div className="grid gap-3 sm:grid-cols-2">
                  <label className="flex flex-col gap-1">
                    <span className="text-xs text-slate-500">Target status (priority)</span>
                    <select
                      value={formFilterPriority}
                      onChange={(e) => setFormFilterPriority(e.target.value)}
                      className="px-3 py-2 border border-slate-200 rounded-lg text-sm"
                    >
                      {PRIORITY_OPTIONS.map((o) => (
                        <option key={o.value} value={o.value}>
                          {o.label}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="flex flex-col gap-1">
                    <span className="text-xs text-slate-500">Mineral</span>
                    <input
                      type="text"
                      value={formFilterMineral}
                      onChange={(e) => setFormFilterMineral(e.target.value)}
                      placeholder="e.g. Gold"
                      className="px-3 py-2 border border-slate-200 rounded-lg text-sm"
                    />
                  </label>
                  <label className="flex flex-col gap-1">
                    <span className="text-xs text-slate-500">Claim status</span>
                    <select
                      value={formFilterStatus}
                      onChange={(e) => setFormFilterStatus(e.target.value)}
                      className="px-3 py-2 border border-slate-200 rounded-lg text-sm"
                    >
                      <option value="">Any</option>
                      <option value="paid">Paid</option>
                      <option value="unpaid">Unpaid</option>
                      <option value="unknown">Unknown</option>
                    </select>
                  </label>
                  <label className="flex flex-col gap-1">
                    <span className="text-xs text-slate-500">State</span>
                    <input
                      type="text"
                      value={formFilterState}
                      onChange={(e) => setFormFilterState(e.target.value)}
                      placeholder="e.g. NV"
                      className="px-3 py-2 border border-slate-200 rounded-lg text-sm"
                    />
                  </label>
                  <label className="flex flex-col gap-1 sm:col-span-2">
                    <span className="text-xs text-slate-500">Name contains</span>
                    <input
                      type="text"
                      value={formFilterName}
                      onChange={(e) => setFormFilterName(e.target.value)}
                      placeholder="Partial name match"
                      className="px-3 py-2 border border-slate-200 rounded-lg text-sm"
                    />
                  </label>
                </div>
              </div>
            </div>
            <div className="p-4 border-t border-slate-200 flex gap-2 shrink-0">
              <button
                type="button"
                disabled={formSaving}
                onClick={() => setModalOpen(false)}
                className="flex-1 px-4 py-2 border border-slate-200 rounded-lg text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={formSaving || !formName.trim()}
                onClick={() => void saveRule()}
                className="flex-1 px-4 py-2 bg-slate-800 text-white rounded-lg text-sm font-medium hover:bg-slate-900 disabled:opacity-50"
              >
                {formSaving ? "Saving…" : modalMode === "create" ? "Create" : "Save"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Run Detail Modal */}
      {runDetail && (
        <div
          className="fixed inset-0 z-[60] flex items-center justify-center p-4 bg-black/50"
          onClick={() => setRunDetail(null)}
          role="dialog"
          aria-modal="true"
        >
          <div
            className="bg-white rounded-xl shadow-xl max-w-4xl w-full max-h-[90vh] flex flex-col"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="p-4 border-b border-slate-200 flex justify-between items-center shrink-0">
              <h3 className="font-semibold text-slate-900">
                Run #{runDetail.id} — {runDetail.rule_name || `Rule #${runDetail.rule_id}`}
              </h3>
              <button
                type="button"
                onClick={() => setRunDetail(null)}
                className="text-slate-500 hover:text-slate-700 text-xl leading-none"
              >
                &times;
              </button>
            </div>
            <div className="px-4 pt-3 text-sm text-slate-600 space-y-1 shrink-0">
              <p>{runDetail.summary}</p>
              <p className="text-xs text-slate-500">
                {fmtDate(runDetail.started_at)}
                {runDetail.finished_at ? ` → ${fmtDate(runDetail.finished_at)}` : " (still running)"}
                {" · "}
                Trigger: {runDetail.trigger_type}
                {runDetail.email_sent && " · Email sent"}
              </p>
              <p className="text-xs text-slate-500">
                Progress: {runDetail.results?.length ?? 0}/{runDetail.targets_total} targets handled
              </p>
              {runDetail.error_message && (
                <p className="text-xs text-red-700 bg-red-50 px-2 py-1 rounded">{runDetail.error_message}</p>
              )}
            </div>
            <div className="p-4 overflow-y-auto flex-1 min-h-0">
              {(runDetail.results?.length ?? 0) === 0 ? (
                <p className="text-slate-500 text-sm">No per-target results.</p>
              ) : (
                <table className="w-full text-sm border border-slate-200 rounded-lg overflow-hidden">
                  <thead>
                    <tr className="bg-slate-50 border-b border-slate-200">
                      <th className="text-left py-2 px-2 font-semibold text-slate-700">ID</th>
                      <th className="text-left py-2 px-2 font-semibold text-slate-700">Name</th>
                      <th className="text-left py-2 px-2 font-semibold text-slate-700">OK</th>
                      <th className="text-left py-2 px-2 font-semibold text-slate-700">Changed</th>
                      <th className="text-left py-2 px-2 font-semibold text-slate-700">Detail</th>
                      <th className="text-left py-2 px-2 font-semibold text-slate-700">Error</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(runDetail.results as AutomationRunLogRow[]).map((r, i) => (
                      <tr key={`${r.id}-${i}`} className="border-b border-slate-100 last:border-0">
                        <td className="py-1.5 px-2 font-mono text-xs">{r.id}</td>
                        <td className="py-1.5 px-2">{r.name || "—"}</td>
                        <td className="py-1.5 px-2">{r.ok ? "Yes" : "No"}</td>
                        <td className="py-1.5 px-2">{r.skipped ? "Skipped" : r.changed ? "Yes" : "—"}</td>
                        <td className="py-1.5 px-2 text-xs text-slate-600">
                          {r.skipped
                            ? r.skip_reason || "Skipped by rule options"
                            : r.claims_count !== undefined
                            ? `${r.claims_count} claims`
                            : r.old_status && r.status
                            ? `${r.old_status} → ${r.status}`
                            : "—"}
                        </td>
                        <td className="py-1.5 px-2 text-xs text-red-700 break-words max-w-[14rem]">
                          {r.error || "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
            <div className="p-4 border-t border-slate-200 shrink-0">
              <button
                type="button"
                onClick={() => setRunDetail(null)}
                className="w-full px-4 py-2 bg-slate-700 text-white rounded-lg text-sm font-medium hover:bg-slate-800"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
