import { BrowserRouter, Routes, Route } from "react-router-dom";
import { AuthProvider, GuestOnly, RequireAuth } from "./auth";
import { Layout } from "./Layout";
import { Dashboard } from "./pages/Dashboard";
import { Minerals } from "./pages/Minerals";
import { Areas } from "./pages/Areas";
import { MapPage } from "./pages/MapPage";
import { Discoveries } from "./pages/Discoveries";
import { DiscoveryDetail } from "./pages/DiscoveryDetail";
import { Automations } from "./pages/Automations";
import { Login } from "./pages/Login";
import { BootstrapAdmin } from "./pages/BootstrapAdmin";
import { AdminAccounts } from "./pages/AdminAccounts";

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
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
              <Route path="admin/accounts" element={<AdminAccounts />} />
            </Route>
          </Route>
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  );
}
