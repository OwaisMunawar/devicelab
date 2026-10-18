import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import { render } from "@testing-library/react";
import type { ReactNode } from "react";
import type { Device, Host } from "../src/api/types";

/** Renders a component inside a throwaway router, with the route params the app uses. */
export async function renderInRouter(ui: ReactNode) {
  const root = createRootRoute();
  const index = createRoute({ getParentRoute: () => root, path: "/", component: () => <>{ui}</> });
  const device = createRoute({
    getParentRoute: () => root,
    path: "/devices/$deviceId",
    component: () => <p>device page</p>,
  });
  const router = createRouter({
    routeTree: root.addChildren([index, device]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await router.load();
  return render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

export function device(overrides: Partial<Device> = {}): Device {
  return {
    id: crypto.randomUUID(),
    host_id: "h1",
    udid: "fake-berlin-01",
    name: "Berlin iPhone 15 #1",
    model: "iPhone 15",
    model_family: "iPhone",
    ios_version: "18.2",
    kind: "fake",
    site: "berlin",
    region: "eu",
    labels: [],
    state: "available",
    battery_pct: 82,
    storage_free_gb: 64,
    thermal: "nominal",
    connectivity: "connected",
    last_seen: new Date().toISOString(),
    consecutive_infra_failures: 0,
    quarantine_reason: null,
    state_changed_at: null,
    ...overrides,
  };
}

export function host(overrides: Partial<Host> = {}): Host {
  return {
    id: "h1",
    name: "demo-berlin",
    site: "berlin",
    region: "eu",
    max_concurrency: 6,
    connected: true,
    last_seen: new Date().toISOString(),
    agent_version: "0.1.0",
    ...overrides,
  };
}
