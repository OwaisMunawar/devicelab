import { expect, test } from "@playwright/test";

// Runs against the docker compose stack, which signs the dashboard in with the demo key.
test("fleet overview shows the seeded fleet", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Fleet overview" })).toBeVisible();
  await expect(page.getByTestId("device-tile").first()).toBeVisible();
  expect(await page.getByTestId("device-tile").count()).toBeGreaterThanOrEqual(20);
  for (const site of ["berlin", "austin", "tokyo", "lisbon"]) {
    await expect(page.getByRole("region", { name: site })).toBeVisible();
  }
});

test("a finished execution shows its step timeline and artifacts", async ({ page, request }) => {
  const jobs = await request.get("/v1/jobs?state=succeeded&limit=1", {
    headers: { Authorization: "Bearer dlk_demo_key_for_local_compose_only" },
  });
  expect(jobs.ok()).toBeTruthy();
  const { items } = (await jobs.json()) as { items: { id: string }[] };
  const job = items[0];
  expect(job).toBeDefined();
  await page.goto(`/jobs/${job?.id ?? ""}`);
  await page.getByRole("link", { name: /Attempt 1/ }).click();
  await expect(page.getByRole("list", { name: "Execution steps" })).toBeVisible();
  await expect(page.getByRole("log", { name: "Execution log" })).toBeVisible();
});

test("navigation reaches every view and the theme can be switched", async ({ page }) => {
  await page.goto("/");
  for (const [link, heading] of [
    ["Jobs", "Jobs and queue"],
    ["Templates", "Workflow templates"],
    ["Schedules", "Schedules"],
    ["Alerts", "Alerts"],
    ["Admin", "Admin"],
  ] as const) {
    await page
      .getByRole("navigation", { name: "Primary" })
      .getByRole("link", { name: link })
      .click();
    await expect(page.getByRole("heading", { name: heading, level: 1 })).toBeVisible();
  }
  await page.getByRole("button", { name: /Theme:/ }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
});

test("an invalid template is reported before it can be saved", async ({ page }) => {
  await page.goto("/templates");
  await page
    .getByLabel("Template source")
    .fill("name: broken\nsteps:\n  - id: x\n    type: run_shell\n");
  await expect(page.getByText(/problem\(s\)/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Create" })).toBeDisabled();
});
