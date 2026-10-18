import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Badge, Button, Card, EmptyState, Stat, Table, Timeline } from "../src/components/ui";
import type { Column } from "../src/components/ui";

describe("Badge", () => {
  it("shows its text, so colour is never the only signal", () => {
    render(<Badge tone="quarantined">Quarantined</Badge>);
    expect(screen.getByText("Quarantined")).toBeVisible();
  });

  it("hides the decorative dot from assistive tech", () => {
    const { container } = render(<Badge tone="available">Ready</Badge>);
    expect(container.querySelector("[aria-hidden='true']")).not.toBeNull();
  });
});

describe("Stat", () => {
  it("pairs a label with its value as a description list", () => {
    render(<Stat label="Queue depth" value={7} hint="jobs waiting" />);
    expect(screen.getByRole("term")).toHaveTextContent("Queue depth");
    expect(screen.getByRole("definition")).toHaveTextContent("7");
    expect(screen.getByText("jobs waiting")).toBeVisible();
  });
});

describe("Card", () => {
  it("labels the region by its title", () => {
    render(
      <Card title="Devices" actions={<button type="button">Filter</button>}>
        body
      </Card>,
    );
    expect(screen.getByRole("region", { name: "Devices" })).toHaveTextContent("body");
    expect(screen.getByRole("button", { name: "Filter" })).toBeVisible();
  });
});

describe("Button", () => {
  it("calls onClick, and is inert while busy", async () => {
    const onClick = vi.fn();
    const { rerender } = render(<Button onClick={onClick}>Save</Button>);
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(onClick).toHaveBeenCalledTimes(1);

    rerender(
      <Button onClick={onClick} busy>
        Save
      </Button>,
    );
    const button = screen.getByRole("button", { name: "Save" });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("aria-busy", "true");
  });
});

describe("Table", () => {
  interface Row {
    name: string;
    n: number;
  }
  const columns: Column<Row>[] = [
    { key: "name", header: "Name", cell: (r) => r.name },
    { key: "n", header: "Count", align: "right", cell: (r) => r.n },
  ];

  it("renders a captioned table with column headers", () => {
    render(
      <Table
        caption="Things"
        columns={columns}
        rows={[
          { name: "a", n: 1 },
          { name: "b", n: 2 },
        ]}
        rowKey={(r) => r.name}
      />,
    );
    const table = screen.getByRole("table", { name: "Things" });
    expect(
      within(table)
        .getAllByRole("columnheader")
        .map((h) => h.textContent),
    ).toEqual(["Name", "Count"]);
    expect(within(table).getAllByRole("row")).toHaveLength(3);
  });

  it("falls back to an empty state with no rows", () => {
    render(
      <Table
        caption="Things"
        columns={columns}
        rows={[]}
        rowKey={(r) => r.name}
        empty={{ title: "Nothing", description: "yet" }}
      />,
    );
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.getByText("Nothing")).toBeVisible();
  });
});

describe("EmptyState", () => {
  it("renders title, description and action", () => {
    render(
      <EmptyState
        title="No devices"
        description="Connect an agent."
        action={<button type="button">Add</button>}
      />,
    );
    expect(screen.getByText("No devices")).toBeVisible();
    expect(screen.getByText("Connect an agent.")).toBeVisible();
    expect(screen.getByRole("button", { name: "Add" })).toBeVisible();
  });
});

describe("Timeline", () => {
  it("lists steps in order with their meta text", () => {
    render(
      <Timeline
        label="Steps"
        items={[
          { id: "1", title: "Launch", tone: "success", meta: "120 ms" },
          { id: "2", title: "Screenshot", tone: "danger", detail: "timed out" },
        ]}
      />,
    );
    const list = screen.getByRole("list", { name: "Steps" });
    const items = within(list).getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("Launch");
    expect(items[0]).toHaveTextContent("120 ms");
    expect(items[1]).toHaveTextContent("timed out");
  });
});
