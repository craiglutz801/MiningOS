import { Link, Outlet, useLocation } from "react-router-dom";
import { useEffect, useRef, useState } from "react";
import { ErrorBoundary } from "./ErrorBoundary";
import { useAuth } from "./auth";
import { automations, formatApiNetworkError, type AutomationRun } from "./api";

const taxSalesUiEnabled = import.meta.env.VITE_ENABLE_TAX_SALES === "true";
const sitlaUiEnabled = import.meta.env.VITE_ENABLE_SITLA === "true";
const activeMinesUiEnabled = import.meta.env.VITE_ENABLE_ACTIVE_MINES === "true";

const nav = [
  { to: "/", label: "Dashboard" },
  { to: "/areas", label: "Targets" },
  { to: "/minerals", label: "Minerals" },
  { to: "/discoveries", label: "Discoveries" },
  { to: "/map", label: "Map" },
  { to: "/automations", label: "Automations" },
  ...(taxSalesUiEnabled ? [{ to: "/tax-sales", label: "Tax Sales" }] : []),
  ...(sitlaUiEnabled ? [{ to: "/sitla", label: "Trust Lands" }] : []),
  ...(activeMinesUiEnabled ? [{ to: "/active-mines", label: "Active Mine Search" }] : []),
];

export function Layout() {
  const loc = useLocation();
  const { me, logout, switchAccount } = useAuth();
  const [busy, setBusy] = useState(false);
  const [accountError, setAccountError] = useState<string | null>(null);
  const [runningRuns, setRunningRuns] = useState<AutomationRun[]>([]);
  const [automationToast, setAutomationToast] = useState<{
    id: number;
    status: string;
    ruleName: string;
    summary: string;
  } | null>(null);
  const runStatusRef = useRef<Record<number, string>>({});

  async function onLogout() {
    setBusy(true);
    setAccountError(null);
    try {
      await logout();
    } catch (err) {
      setAccountError(formatApiNetworkError(err));
    } finally {
      setBusy(false);
    }
  }

  async function onSwitchAccount(accountId: number) {
    if (!me || accountId === me.active_account.id) return;
    setBusy(true);
    setAccountError(null);
    try {
      await switchAccount(accountId);
      window.location.reload();
    } catch (err) {
      setAccountError(formatApiNetworkError(err));
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (!me) {
      setRunningRuns([]);
      runStatusRef.current = {};
      return;
    }

    let cancelled = false;

    const loadAutomationRuns = async () => {
      try {
        const runs = await automations.listRuns({ limit: 20 });
        if (cancelled) return;

        const nextStatuses: Record<number, string> = {};
        for (const run of runs) {
          nextStatuses[run.id] = run.status;
          const previous = runStatusRef.current[run.id];
          const finished = run.status === "completed" || run.status === "failed";
          if (previous === "running" && finished) {
            const ruleName = run.rule_name || `Rule #${run.rule_id}`;
            const summary = run.summary || `${ruleName} finished.`;
            setAutomationToast({
              id: run.id,
              status: run.status,
              ruleName,
              summary,
            });
            if (typeof Notification !== "undefined" && document.hidden && Notification.permission === "granted") {
              try {
                new Notification(`Automation ${run.status}`, {
                  body: `${ruleName}: ${summary}`,
                });
              } catch {
                // Ignore notification failures.
              }
            }
          }
        }
        runStatusRef.current = nextStatuses;
        setRunningRuns(runs.filter((run) => run.status === "running"));
      } catch {
        if (cancelled) return;
      }
    };

    void loadAutomationRuns();
    const timer = window.setInterval(() => {
      void loadAutomationRuns();
    }, 3000);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [me?.active_account.id, me]);

  useEffect(() => {
    if (!automationToast) return;
    const timer = window.setTimeout(() => setAutomationToast(null), 8000);
    return () => window.clearTimeout(timer);
  }, [automationToast]);

  const requestBrowserNotifications = async () => {
    if (typeof Notification === "undefined" || Notification.permission !== "default") return;
    try {
      await Notification.requestPermission();
    } catch {
      // Ignore permission request failures.
    }
  };

  return (
    <div className="min-h-screen flex flex-col bg-slate-50">
      <header className="bg-white border-b border-slate-200 shadow-card sticky top-0 z-30">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex justify-between items-center min-h-14 py-3 gap-4">
            <div className="flex items-center gap-8">
              <Link to="/" className="flex items-center gap-2 text-slate-900 font-semibold text-lg">
                <span className="w-8 h-8 rounded-lg bg-primary-600 flex items-center justify-center text-white text-sm font-bold">M</span>
                Mining AI
              </Link>
              <nav className="hidden sm:flex items-center gap-1">
                {nav.map(({ to, label }) => {
                  const active = to === "/" ? loc.pathname === "/" : loc.pathname === to || loc.pathname.startsWith(to + "/");
                  return (
                    <Link
                      key={to}
                      to={to}
                      className={`px-3 py-2 rounded-md text-sm font-medium transition-colors ${
                        active ? "bg-primary-50 text-primary-700" : "text-slate-600 hover:bg-slate-100 hover:text-slate-900"
                      }`}
                    >
                      {label}
                    </Link>
                  );
                })}
                {me?.user.is_system_admin ? (
                  <Link
                    to="/admin/accounts"
                    className={`px-3 py-2 rounded-md text-sm font-medium transition-colors ${
                      loc.pathname === "/admin/accounts"
                        ? "bg-primary-50 text-primary-700"
                        : "text-slate-600 hover:bg-slate-100 hover:text-slate-900"
                    }`}
                  >
                    Admin
                  </Link>
                ) : null}
              </nav>
            </div>

            <div className="flex items-center gap-3">
              {me ? (
                <>
                  <div className="hidden md:block text-right">
                    <div className="text-sm font-medium text-slate-900">
                      {me.user.display_name || me.user.username}
                    </div>
                    <div className="text-xs text-slate-500">
                      {me.active_account.name}
                    </div>
                  </div>

                  <select
                    className="rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-primary-500"
                    value={me.active_account.id}
                    onChange={(e) => void onSwitchAccount(Number(e.target.value))}
                    disabled={busy}
                  >
                    {me.memberships.map((membership) => (
                      <option key={membership.account_id} value={membership.account_id}>
                        {membership.account_name}
                      </option>
                    ))}
                  </select>

                  <button
                    type="button"
                    onClick={() => void onLogout()}
                    disabled={busy}
                    className="rounded-md border border-slate-300 px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-100 disabled:opacity-60"
                  >
                    {busy ? "Working…" : "Log out"}
                  </button>
                </>
              ) : null}
            </div>
          </div>
          {accountError ? (
            <div className="pb-3 text-sm text-rose-600">{accountError}</div>
          ) : null}
          {runningRuns.length > 0 ? (
            <div className="pb-3">
              <div className="rounded-xl border border-sky-200 bg-sky-50 px-4 py-3 flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
                <div className="min-w-0">
                  <p className="text-sm font-semibold text-sky-900">
                    {runningRuns.length} automation run{runningRuns.length === 1 ? "" : "s"} running in the background
                  </p>
                  <div className="mt-1 space-y-1">
                    {runningRuns.slice(0, 3).map((run) => {
                      const handled = run.results?.length ?? 0;
                      const total = run.targets_total || 0;
                      const progress = total > 0 ? `${handled}/${total}` : `${handled} processed`;
                      return (
                        <p key={run.id} className="text-xs text-sky-800 truncate">
                          {run.rule_name || `Rule #${run.rule_id}`}: {progress}
                        </p>
                      );
                    })}
                  </div>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  {typeof Notification !== "undefined" && Notification.permission === "default" ? (
                    <button
                      type="button"
                      onClick={() => void requestBrowserNotifications()}
                      className="px-3 py-1.5 text-xs font-medium text-sky-700 bg-white border border-sky-200 rounded-lg hover:bg-sky-100"
                    >
                      Enable notifications
                    </button>
                  ) : null}
                  <Link
                    to="/automations?tab=runs"
                    className="px-3 py-1.5 text-xs font-medium text-white bg-sky-700 rounded-lg hover:bg-sky-800"
                  >
                    Check progress
                  </Link>
                </div>
              </div>
            </div>
          ) : null}
        </div>
      </header>

      <main className="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <ErrorBoundary>
          <Outlet />
        </ErrorBoundary>
      </main>

      {automationToast ? (
        <div className="fixed top-4 right-4 z-[70] max-w-sm">
          <div className={`rounded-xl shadow-lg border px-5 py-4 flex items-start gap-3 ${
            automationToast.status === "failed"
              ? "bg-rose-50 border-rose-200"
              : "bg-emerald-50 border-emerald-200"
          }`}>
            <div className="flex-1 min-w-0">
              <p className={`text-sm font-semibold ${
                automationToast.status === "failed" ? "text-rose-900" : "text-emerald-900"
              }`}>
                {automationToast.ruleName} {automationToast.status === "failed" ? "failed" : "completed"}
              </p>
              <p className={`text-xs mt-0.5 ${
                automationToast.status === "failed" ? "text-rose-700" : "text-emerald-700"
              }`}>
                {automationToast.summary}
              </p>
              <Link
                to="/automations?tab=runs"
                className={`inline-block mt-2 text-xs font-medium ${
                  automationToast.status === "failed" ? "text-rose-700" : "text-emerald-700"
                } hover:underline`}
              >
                Open run history
              </Link>
            </div>
            <button
              type="button"
              onClick={() => setAutomationToast(null)}
              className={`shrink-0 p-1 rounded ${
                automationToast.status === "failed"
                  ? "text-rose-400 hover:text-rose-600"
                  : "text-emerald-400 hover:text-emerald-600"
              }`}
              aria-label="Dismiss"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        </div>
      ) : null}

      <footer className="border-t border-slate-200 bg-white py-4 text-center text-slate-500 text-sm">
        Mining AI — Deal intelligence for claims &amp; minerals
      </footer>
    </div>
  );
}
