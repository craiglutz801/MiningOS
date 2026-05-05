import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, formatApiNetworkError } from "../api";
import { useAuth } from "../auth";

export function Login() {
  const navigate = useNavigate();
  const { login } = useAuth();
  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await login({ identifier, password });
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
      <div className="w-full max-w-md bg-white rounded-2xl shadow-card border border-slate-200 p-8">
        <div className="flex items-center gap-3">
          <span className="w-10 h-10 rounded-xl bg-primary-600 flex items-center justify-center text-white font-bold">M</span>
          <div>
            <h1 className="text-xl font-semibold text-slate-900">Sign in to Mining OS</h1>
            <p className="text-sm text-slate-500">Use your username or email to access your targets.</p>
          </div>
        </div>

        <form className="mt-8 space-y-4" onSubmit={onSubmit}>
          <label className="block">
            <span className="block text-sm font-medium text-slate-700 mb-1">Username or email</span>
            <input
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-500"
              value={identifier}
              onChange={(e) => setIdentifier(e.target.value)}
              autoComplete="username"
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
              autoComplete="current-password"
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
            disabled={submitting}
            className="w-full rounded-lg bg-primary-600 text-white py-2.5 text-sm font-medium hover:bg-primary-700 disabled:opacity-60"
          >
            {submitting ? "Signing in…" : "Sign in"}
          </button>
        </form>
      </div>
    </div>
  );
}
