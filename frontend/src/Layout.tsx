import { Link, Outlet, useLocation } from "react-router-dom";
import { ErrorBoundary } from "./ErrorBoundary";

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
  return (
    <div className="min-h-screen flex flex-col bg-slate-50">
      <header className="bg-white border-b border-slate-200 shadow-card sticky top-0 z-30">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex justify-between h-14">
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
              </nav>
            </div>
          </div>
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
