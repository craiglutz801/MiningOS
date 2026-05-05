import { useEffect, useState } from "react";
import { ApiError, api, formatApiNetworkError, type AdminAccountSummary } from "../api";
import { useAuth } from "../auth";

export function AdminAccounts() {
  const { me } = useAuth();
  const [accounts, setAccounts] = useState<AdminAccountSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState({
    account_name: "",
    admin_email: "",
    admin_username: "",
    admin_password: "",
    admin_display_name: "",
  });

  async function loadAccounts() {
    setLoading(true);
    setError(null);
    try {
      const res = await api.auth.adminAccounts();
      setAccounts(res.accounts);
    } catch (err) {
      setError(formatApiNetworkError(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadAccounts();
  }, []);

  async function onCreate(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await api.auth.createAdminAccount({
        account_name: form.account_name,
        admin_email: form.admin_email,
        admin_username: form.admin_username,
        admin_password: form.admin_password,
        admin_display_name: form.admin_display_name || undefined,
      });
      setForm({
        account_name: "",
        admin_email: "",
        admin_username: "",
        admin_password: "",
        admin_display_name: "",
      });
      await loadAccounts();
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError(formatApiNetworkError(err));
      }
    } finally {
      setSaving(false);
    }
  }

  if (!me?.user.is_system_admin) {
    return (
      <div className="rounded-2xl border border-amber-200 bg-amber-50 px-5 py-4 text-amber-800">
        System admin access is required for account management.
      </div>
    );
  }

  return (
    <div className="space-y-8">
      <section>
        <h1 className="text-3xl font-semibold text-slate-900">Accounts</h1>
        <p className="mt-2 text-sm text-slate-600">
          Create new customer accounts and seed the first admin user for each workspace.
        </p>
      </section>

      <section className="grid grid-cols-1 xl:grid-cols-3 gap-6">
        <div className="xl:col-span-2 bg-white rounded-2xl border border-slate-200 shadow-card overflow-hidden">
          <div className="px-5 py-4 border-b border-slate-200 flex items-center justify-between">
            <h2 className="text-lg font-semibold text-slate-900">Existing accounts</h2>
            <button
              type="button"
              onClick={() => void loadAccounts()}
              className="text-sm font-medium text-primary-700 hover:text-primary-800"
            >
              Refresh
            </button>
          </div>
          {loading ? (
            <div className="px-5 py-8 text-sm text-slate-500">Loading accounts…</div>
          ) : (
            <div className="divide-y divide-slate-200">
              {accounts.map((account) => (
                <div key={account.id} className="px-5 py-4 flex items-center justify-between gap-4">
                  <div>
                    <div className="text-sm font-semibold text-slate-900">{account.name}</div>
                    <div className="text-xs text-slate-500">
                      {account.member_count} member{account.member_count === 1 ? "" : "s"} •{" "}
                      {account.target_count} target{account.target_count === 1 ? "" : "s"}
                    </div>
                  </div>
                  <div className="text-xs text-slate-400">{new Date(account.created_at).toLocaleDateString()}</div>
                </div>
              ))}
              {!accounts.length ? <div className="px-5 py-8 text-sm text-slate-500">No accounts found.</div> : null}
            </div>
          )}
        </div>

        <form className="bg-white rounded-2xl border border-slate-200 shadow-card p-5 space-y-4" onSubmit={onCreate}>
          <div>
            <h2 className="text-lg font-semibold text-slate-900">Create account</h2>
            <p className="mt-1 text-sm text-slate-500">You’ll automatically be added to the new account as an admin.</p>
          </div>

          <label className="block">
            <span className="block text-sm font-medium text-slate-700 mb-1">Account name</span>
            <input
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-500"
              value={form.account_name}
              onChange={(e) => setForm((cur) => ({ ...cur, account_name: e.target.value }))}
              required
            />
          </label>

          <label className="block">
            <span className="block text-sm font-medium text-slate-700 mb-1">Admin display name</span>
            <input
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-500"
              value={form.admin_display_name}
              onChange={(e) => setForm((cur) => ({ ...cur, admin_display_name: e.target.value }))}
            />
          </label>

          <label className="block">
            <span className="block text-sm font-medium text-slate-700 mb-1">Admin email</span>
            <input
              type="email"
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-500"
              value={form.admin_email}
              onChange={(e) => setForm((cur) => ({ ...cur, admin_email: e.target.value }))}
              required
            />
          </label>

          <label className="block">
            <span className="block text-sm font-medium text-slate-700 mb-1">Admin username</span>
            <input
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-500"
              value={form.admin_username}
              onChange={(e) => setForm((cur) => ({ ...cur, admin_username: e.target.value }))}
              required
            />
          </label>

          <label className="block">
            <span className="block text-sm font-medium text-slate-700 mb-1">Temporary password</span>
            <input
              type="password"
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-500"
              value={form.admin_password}
              onChange={(e) => setForm((cur) => ({ ...cur, admin_password: e.target.value }))}
              required
            />
          </label>

          {error ? (
            <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
              {error}
            </div>
          ) : null}

          <button
            type="submit"
            disabled={saving}
            className="w-full rounded-lg bg-primary-600 text-white py-2.5 text-sm font-medium hover:bg-primary-700 disabled:opacity-60"
          >
            {saving ? "Creating account…" : "Create account"}
          </button>
        </form>
      </section>
    </div>
  );
}
