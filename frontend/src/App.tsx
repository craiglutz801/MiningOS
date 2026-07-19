import { BrowserRouter, Routes, Route } from "react-router-dom";
import { AuthProvider, GuestOnly, RequireAuth } from "./auth";
import { Layout } from "./Layout";
import { Dashboard } from "./pages/Dashboard";
import { Minerals } from "./pages/Minerals";
import { Areas } from "./pages/Areas";
import { MapPage } from "./pages/MapPage";
import { Discoveries } from "./pages/Discoveries";
import { DiscoveryDetail } from "./pages/DiscoveryDetail";
import { lazy, Suspense } from "react";
import { Automations } from "./pages/Automations";
import { Login } from "./pages/Login";
import { BootstrapAdmin } from "./pages/BootstrapAdmin";
import { AdminAccounts } from "./pages/AdminAccounts";
import { SharePage } from "./pages/SharePage";

const TaxSales = lazy(() =>
  import("./pages/TaxSales").then((m) => ({ default: m.TaxSales }))
);

const taxSalesUiEnabled = import.meta.env.VITE_ENABLE_TAX_SALES === "true";

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          {/* Public, no-login shared target view */}
          <Route path="/share/:token" element={<SharePage />} />
          <Route element={<GuestOnly />}>
            <Route path="/login" element={<Login />} />
          </Route>
          <Route element={<GuestOnly bootstrap />}>
            <Route path="/bootstrap" element={<BootstrapAdmin />} />
          </Route>
          <Route element={<RequireAuth />}>
            <Route path="/" element={<Layout />}>
              <Route index element={<Dashboard />} />
              <Route path="minerals" element={<Minerals />} />
              <Route path="areas" element={<Areas />} />
              <Route path="discoveries" element={<Discoveries />} />
              <Route path="discoveries/:id" element={<DiscoveryDetail />} />
              <Route path="map" element={<MapPage />} />
              <Route path="automations" element={<Automations />} />
              {taxSalesUiEnabled && (
                <Route
                  path="tax-sales"
                  element={
                    <Suspense
                      fallback={
                        <div className="p-6 text-sm text-slate-500">Loading Tax Sales…</div>
                      }
                    >
                      <TaxSales />
                    </Suspense>
                  }
                />
              )}
              <Route path="admin/accounts" element={<AdminAccounts />} />
            </Route>
          </Route>
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  );
}
