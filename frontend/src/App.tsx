import { BrowserRouter, Routes, Route } from "react-router-dom";
import { Layout } from "./Layout";
import { Dashboard } from "./pages/Dashboard";
import { Minerals } from "./pages/Minerals";
import { Areas } from "./pages/Areas";
import { MapPage } from "./pages/MapPage";
import { Discoveries } from "./pages/Discoveries";
import { DiscoveryDetail } from "./pages/DiscoveryDetail";
import { Automations } from "./pages/Automations";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index element={<Dashboard />} />
          <Route path="minerals" element={<Minerals />} />
          <Route path="areas" element={<Areas />} />
          <Route path="discoveries" element={<Discoveries />} />
          <Route path="discoveries/:id" element={<DiscoveryDetail />} />
          <Route path="map" element={<MapPage />} />
          <Route path="automations" element={<Automations />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
