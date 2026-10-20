import "@fontsource-variable/geist";
import "@fontsource-variable/geist-mono";
import "./app/globals.css";

import { lazy, StrictMode, Suspense, type ComponentType } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, Outlet, RouterProvider } from "react-router";
import { StudioShell } from "@/studio/StudioShell";
import { EngineGate } from "@/components/EngineGate";

// Each page loads on first visit.
const page = (load: () => Promise<{ default: ComponentType }>) => {
  const Page = lazy(load);
  return (
    <Suspense fallback={null}>
      <Page />
    </Suspense>
  );
};
const OrgWorkspace = lazy(() => import("./studio/OrgWorkspace"));

const router = createBrowserRouter([
  {
    element: (
      <StudioShell>
        <Outlet />
      </StudioShell>
    ),
    children: [
      { index: true, element: page(() => import("./app/page")) },
      { path: "settings", element: page(() => import("./app/settings/page")) },
      { path: "supervisor", element: page(() => import("./app/supervisor/page")) },
      { path: "argus", element: page(() => import("./app/argus/page")) },
      { path: "kronos", element: page(() => import("./app/kronos/page")) },
      { path: "packs", element: page(() => import("./app/packs/page")) },
      { path: "benchmarks", element: page(() => import("./app/benchmarks/page")) },
      { path: "dev/catalog", element: page(() => import("./app/dev/catalog/page")) },
      // An org is one workspace of panes (studio/OrgWorkspace); Pantheon 1's tab paths
      // (/org/<id>/board…) land there too.
      {
        path: "org/:orgId/*",
        element: (
          <Suspense fallback={null}>
            <OrgWorkspace />
          </Suspense>
        ),
      },
    ],
  },
]);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <EngineGate>
      <RouterProvider router={router} />
    </EngineGate>
  </StrictMode>,
);
