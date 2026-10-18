import { createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import { AppShell } from "./components/AppShell";
import { AdminPage } from "./pages/AdminPage";
import { AlertsPage } from "./pages/AlertsPage";
import { DevicePage } from "./pages/DevicePage";
import { ExecutionPage } from "./pages/ExecutionPage";
import { FleetPage } from "./pages/FleetPage";
import { JobDetailPage } from "./pages/JobDetailPage";
import { JobsPage } from "./pages/JobsPage";
import { SchedulesPage } from "./pages/SchedulesPage";
import { TemplatesPage } from "./pages/TemplatesPage";

const root = createRootRoute({ component: AppShell });

function page<P extends string>(path: P, component: () => React.JSX.Element) {
  return createRoute({ getParentRoute: () => root, path, component });
}

const routeTree = root.addChildren([
  page("/", FleetPage),
  page("/devices/$deviceId", DevicePage),
  page("/jobs", JobsPage),
  page("/jobs/$jobId", JobDetailPage),
  page("/executions/$executionId", ExecutionPage),
  page("/templates", TemplatesPage),
  page("/schedules", SchedulesPage),
  page("/alerts", AlertsPage),
  page("/admin", AdminPage),
]);

export const router = createRouter({ routeTree });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
