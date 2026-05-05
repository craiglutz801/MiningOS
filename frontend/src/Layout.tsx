import { Link, Outlet, useLocation } from "react-router-dom";
import { useState } from "react";
import { ErrorBoundary } from "./ErrorBoundary";
import { useAuth } from "./auth";
import { formatApiNetworkError } from "./api";

const nav = [
  { to: "/", label: "Dashboard" },
  { to: "/areas", label: "Targets" },
  { to: "/minerals", label: "Minerals" },
  { to: "/discoveries", label: "Discoveries" },
  { to: "/map", label: "Map" },
  { to: "/automations", label: "Automations" },
];

export function Layout() {
  const loc = useLocation();
  const { me, logout, switchAccount } = useAuth();
  const [busy, setBusy] = useState(false);
  const [accountError, setAccountError] = useState<string | null>(null);

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
        </div>
      </header>

      <main className="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <ErrorBoundary>
          <Outlet />
        </ErrorBoundary>
      </main>

      <footer className="border-t border-slate-200 bg-white py-4 text-center text-slate-500 text-sm">
        Mining AI — Deal intelligence for claims &amp; minerals
      </footer>
    </div>
  );
}
