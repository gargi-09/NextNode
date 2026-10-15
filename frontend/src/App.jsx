import { lazy, Suspense, useEffect, useState } from "react";
import { BrowserRouter, NavLink, Route, Routes } from "react-router-dom";
import StatusBanner from "./components/StatusBanner";
import { Loading } from "./components/ui";

const Analyze = lazy(() => import("./pages/Analyze"));
const Users = lazy(() => import("./pages/Users"));
const Jobs = lazy(() => import("./pages/Jobs"));
const Ontology = lazy(() => import("./pages/Ontology"));
const System = lazy(() => import("./pages/System"));

const NAV = [
  ["/", "Analyze"],
  ["/users", "Users"],
  ["/jobs", "Jobs"],
  ["/ontology", "Ontology"],
  ["/system", "System"],
];

export default function App() {
  const [dark, setDark] = useState(true);

  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
  }, [dark]);

  return (
    <BrowserRouter>
      <div className="min-h-screen bg-gray-50 dark:bg-gray-950 text-gray-900 dark:text-gray-100 transition-colors">
        <nav className="border-b border-gray-200 dark:border-gray-800 bg-white/80 dark:bg-gray-950/80 backdrop-blur sticky top-0 z-20">
          <div className="max-w-6xl mx-auto px-6 py-3 flex items-center gap-6">
            <span className="text-lg font-bold tracking-tight">
              next<span className="text-violet-500">node</span>
            </span>
            <div className="flex gap-1 overflow-x-auto">
              {NAV.map(([to, label]) => (
                <NavLink key={to} to={to} end={to === "/"}
                  className={({ isActive }) =>
                    `px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
                      isActive
                        ? "bg-violet-100 dark:bg-violet-900/40 text-violet-700 dark:text-violet-300"
                        : "text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800"}`}>
                  {label}
                </NavLink>
              ))}
            </div>
            <button
              onClick={() => setDark(!dark)}
              className="ml-auto text-sm px-3 py-1.5 rounded-lg border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800"
            >
              {dark ? "Light" : "Dark"}
            </button>
          </div>
        </nav>
        <StatusBanner />
        <main className="max-w-6xl mx-auto px-6 py-8">
          <Suspense fallback={<Loading />}>
          <Routes>
            <Route path="/" element={<Analyze />} />
            <Route path="/users" element={<Users />} />
            <Route path="/jobs" element={<Jobs />} />
            <Route path="/ontology" element={<Ontology />} />
            <Route path="/system" element={<System />} />
          </Routes>
          </Suspense>
        </main>
      </div>
    </BrowserRouter>
  );
}
