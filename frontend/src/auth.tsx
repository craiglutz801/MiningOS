import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { Navigate, Outlet } from "react-router-dom";
import { api, ApiError, formatApiNetworkError, type AuthMe } from "./api";

type AuthContextValue = {
  loading: boolean;
  error: string | null;
  me: AuthMe | null;
  needsBootstrap: boolean;
  refreshAuth: () => Promise<void>;
  login: (body: { identifier: string; password: string }) => Promise<AuthMe>;
  logout: () => Promise<void>;
  bootstrapAdmin: (body: { email: string; username: string; password: string; display_name?: string }) => Promise<AuthMe>;
  switchAccount: (accountId: number) => Promise<AuthMe>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

function LoadingScreen({ label }: { label: string }) {
  return (
    <div className="min-h-screen bg-slate-50 flex items-center justify-center px-6">
      <div className="bg-white border border-slate-200 shadow-card rounded-2xl p-8 w-full max-w-md text-center">
        <div className="w-10 h-10 mx-auto rounded-full border-4 border-slate-200 border-t-primary-600 animate-spin" />
        <h1 className="mt-5 text-lg font-semibold text-slate-900">{label}</h1>
        <p className="mt-2 text-sm text-slate-500">Checking your Mining OS session…</p>
      </div>
    </div>
  );
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [me, setMe] = useState<AuthMe | null>(null);
  const [needsBootstrap, setNeedsBootstrap] = useState(false);

  const refreshAuth = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const payload = await api.auth.me();
      setMe(payload);
      setNeedsBootstrap(false);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setMe(null);
        try {
          const bootstrap = await api.auth.bootstrapStatus();
          setNeedsBootstrap(Boolean(bootstrap.needs_bootstrap));
        } catch (bootstrapErr) {
          setError(formatApiNetworkError(bootstrapErr));
        }
        return;
      }
      setError(formatApiNetworkError(err));
      setMe(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refreshAuth();
  }, [refreshAuth]);

  const login = useCallback(async (body: { identifier: string; password: string }) => {
    const payload = await api.auth.login(body);
    setMe(payload);
    setNeedsBootstrap(false);
    setError(null);
    return payload;
  }, []);

  const logout = useCallback(async () => {
    await api.auth.logout();
    setMe(null);
    setError(null);
    const bootstrap = await api.auth.bootstrapStatus();
    setNeedsBootstrap(Boolean(bootstrap.needs_bootstrap));
  }, []);

  const bootstrapAdmin = useCallback(
    async (body: { email: string; username: string; password: string; display_name?: string }) => {
      const payload = await api.auth.bootstrapAdmin(body);
      setMe(payload);
      setNeedsBootstrap(false);
      setError(null);
      return payload;
    },
    [],
  );

  const switchAccount = useCallback(async (accountId: number) => {
    const payload = await api.auth.switchAccount(accountId);
    setMe(payload);
    setNeedsBootstrap(false);
    setError(null);
    return payload;
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      loading,
      error,
      me,
      needsBootstrap,
      refreshAuth,
      login,
      logout,
      bootstrapAdmin,
      switchAccount,
    }),
    [loading, error, me, needsBootstrap, refreshAuth, login, logout, bootstrapAdmin, switchAccount],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}

export function RequireAuth() {
  const { loading, me, needsBootstrap, error } = useAuth();
  if (loading) return <LoadingScreen label="Loading Mining OS" />;
  if (error && !me) {
    return (
      <div className="min-h-screen bg-slate-50 flex items-center justify-center px-6">
        <div className="bg-white border border-rose-200 rounded-2xl p-8 w-full max-w-lg shadow-card">
          <h1 className="text-lg font-semibold text-slate-900">Could not load authentication</h1>
          <p className="mt-3 text-sm text-slate-600 whitespace-pre-wrap">{error}</p>
        </div>
      </div>
    );
  }
  if (!me) {
    return <Navigate to={needsBootstrap ? "/bootstrap" : "/login"} replace />;
  }
  return <Outlet />;
}

export function GuestOnly({ bootstrap = false }: { bootstrap?: boolean }) {
  const { loading, me, needsBootstrap } = useAuth();
  if (loading) return <LoadingScreen label="Loading Mining OS" />;
  if (me) return <Navigate to="/" replace />;
  if (bootstrap && !needsBootstrap) return <Navigate to="/login" replace />;
  if (!bootstrap && needsBootstrap) return <Navigate to="/bootstrap" replace />;
  return <Outlet />;
}
