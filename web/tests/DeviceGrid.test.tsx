import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DeviceGrid, StateLegend } from "../src/components/DeviceGrid";
import { device, host, renderInRouter } from "./helpers";

describe("DeviceGrid", () => {
  it("groups devices under their site and shows the agent connection", async () => {
    await renderInRouter(
      <DeviceGrid
        devices={[
          device({ name: "Berlin A" }),
          device({ name: "Berlin B", state: "busy" }),
          device({ name: "Tokyo A", site: "tokyo" }),
        ]}
        hosts={[host(), host({ id: "h2", site: "tokyo", connected: false })]}
      />,
    );
    const berlin = screen.getByRole("region", { name: "berlin" });
    expect(within(berlin).getAllByTestId("device-tile")).toHaveLength(2);
    expect(within(berlin).getByText("Agent connected")).toBeVisible();
    const tokyo = screen.getByRole("region", { name: "tokyo" });
    expect(within(tokyo).getByText("Agent offline")).toBeVisible();
  });

  it("describes each tile for screen readers and exposes its state", async () => {
    await renderInRouter(
      <DeviceGrid
        devices={[device({ name: "Lab 1", state: "quarantined", battery_pct: 11 })]}
        hosts={[]}
      />,
    );
    const tile = screen.getByRole("link", { name: "Lab 1, Quarantined, battery 11 percent" });
    expect(tile).toHaveAttribute("data-state", "quarantined");
    expect(tile).toHaveAttribute("href", expect.stringMatching(/^\/devices\//));
  });

  it("flags thermal trouble and missing batteries", async () => {
    await renderInRouter(
      <DeviceGrid
        devices={[device({ thermal: "serious" }), device({ name: "Sim", battery_pct: null })]}
        hosts={[]}
      />,
    );
    expect(screen.getByText("Thermal: serious")).toBeVisible();
    expect(screen.getByText("no battery")).toBeVisible();
  });

  it("explains an empty result", async () => {
    await renderInRouter(<DeviceGrid devices={[]} hosts={[]} />);
    expect(screen.getByText("No devices match")).toBeVisible();
  });
});

describe("StateLegend", () => {
  it("shows a count for every state, zero included", () => {
    renderLegend();
    const legend = screen.getByRole("list", { name: "Device states" });
    expect(within(legend).getAllByRole("listitem")).toHaveLength(6);
    expect(within(legend).getByText("Available").parentElement).toHaveTextContent("5");
    expect(within(legend).getByText("Maintenance").parentElement).toHaveTextContent("0");
  });
});

import { render } from "@testing-library/react";
function renderLegend() {
  render(<StateLegend counts={{ available: 5, busy: 2, offline: 1 }} />);
}
