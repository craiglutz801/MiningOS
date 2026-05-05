import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, formatApiNetworkError } from "../api";
import { useAuth } from "../auth";

export function BootstrapAdmin() {
  const navigate = useNavigate();
  const { bootstrapAdmin } = useAuth();
  const [displayName, setDisplayName] = useState("Craig");
  const [username, setUsername] = useState("craig");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (password !== confirmPassword) {
      setError("Passwords do not match.");
      return;
    }
    setSubmitting(true);
    try {
      await bootstrapAdmin({
        email,
        username,
        password,
        display_name: displayName || undefined,
      });
      navigate("/", { replace: true });
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError(formatApiNetworkError(err));
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="min-h-screen bg-slate-50 flex items-center justify-center px-6 py-12">
      <div className="w-full max-w-xl bg-white rounded-2xl shadow-card border border-slate-200 p-8">
        <h1 className="text-2xl font-semibold text-slate-900">Set up Mining OS</h1>
        <p className="mt-2 text-sm text-slate-600">
          No users exist yet. Create the first system administrator for the default <strong>Craig</strong> account.
        </p>

        <form className="mt-8 grid grid-cols-1 md:grid-cols-2 gap-4" onSubmit={onSubmit}>
          <label className="block md:col-span-2">
            <span className="block text-sm font-medium text-slate-700 mb-1">Display name</span>
            <input
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-500"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
            />
          </label>

          <label className="block">
            <span className="block text-sm font-medium text-slate-700 mb-1">Username</span>
            <input
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-500"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              required
            />
          </label>

          <label className="block">
            <span className="block text-sm font-medium text-slate-700 mb-1">Email</span>
            <input
              type="email"
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-500"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="email"
              required
            />
          </label>

          <label className="block">
            <span className="block text-sm font-medium text-slate-700 mb-1">Password</span>
            <input
              type="password"
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-500"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="new-password"
              required
            />
          </label>

          <label className="block">
            <span className="block text-sm font-medium text-slate-700 mb-1">Confirm password</span>
            <input
              type="password"
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-500"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              autoComplete="new-password"
              required
            />
          </label>

          {error ? (
            <div className="md:col-span-2 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
              {error}
            </div>
          ) : null}

          <button
            type="submit"
            disabled={submitting}
            className="md:col-span-2 rounded-lg bg-primary-600 text-white py-2.5 text-sm font-medium hover:bg-primary-700 disabled:opacity-60"
          >
            {submitting ? "Creating administrator…" : "Create administrator"}
          </button>
        </form>
      </div>
    </div>
  );
}
