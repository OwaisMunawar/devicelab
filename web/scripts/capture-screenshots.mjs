// Captures the README screenshots from a running stack.
//   PW_CHANNEL=chrome DEVICELAB_URL=http://localhost:8080 node scripts/capture-screenshots.mjs ../docs/screenshots
import { mkdir } from "node:fs/promises";
import path from "node:path";
import { chromium } from "@playwright/test";

const base = process.env.DEVICELAB_URL ?? "http://localhost:8080";
const key = process.env.DEVICELAB_KEY ?? "dlk_demo_key_for_local_compose_only";
const out = path.resolve(process.argv[2] ?? "../docs/screenshots");
await mkdir(out, { recursive: true });

async function get(pathname) {
    const response = await fetch(`${base}${pathname}`, {
        headers: { Authorization: `Bearer ${key}` },
    });
    if (!response.ok) throw new Error(`${pathname}: ${response.status}`);
    return response.json();
}

const browser = await chromium.launch({ channel: process.env.PW_CHANNEL || undefined });
const context = await browser.newContext({
    viewport: { width: 1440, height: 960 },
    deviceScaleFactor: 2,
    colorScheme: "light",
});
const page = await context.newPage();

async function shot(name, { fullPage = false, settle = 600 } = {}) {
    await page.waitForTimeout(settle);
    await page.screenshot({ path: path.join(out, `${name}.png`), fullPage });
    console.log("captured", name);
}

// Fleet
await page.goto(`${base}/`);
await page.getByTestId("device-tile").first().waitFor();
await page.getByText("Live", { exact: true }).waitFor();
await shot("fleet-overview", { settle: 800 });
await page.emulateMedia({ colorScheme: "dark" });
await shot("fleet-overview-dark");
await page.emulateMedia({ colorScheme: "light" });

// Device detail: the one with the most history
const devices = (await get("/v1/devices?limit=200")).items;
let best = devices[0];
let bestCount = -1;
for (const d of devices.slice(0, 12)) {
    const count = (await get(`/v1/devices/${d.id}/timeline?hours=48`)).length;
    if (count > bestCount) [best, bestCount] = [d, count];
}
await page.goto(`${base}/devices/${best.id}`);
await page.getByRole("heading", { level: 1 }).waitFor();
await shot("device-detail", { fullPage: true, settle: 1200 });

// Jobs and queue
await page.goto(`${base}/jobs`);
await page.getByRole("heading", { name: "Jobs and queue" }).waitFor();
await shot("jobs-and-queue", { fullPage: false, settle: 1200 });

// Execution with timeline, logs and artifacts
const done = (await get("/v1/executions?state=succeeded&limit=60")).items;
let chosen;
for (const e of done) {
    const detail = await get(`/v1/executions/${e.id}`);
    if (detail.artifacts.some((a) => a.kind === "screenshot") && detail.steps.length >= 4) {
        chosen = detail;
        break;
    }
}
await page.goto(`${base}/executions/${chosen.id}`);
await page.getByRole("list", { name: "Execution steps" }).waitFor();
await page
    .getByRole("img", { name: /Screenshot/ })
    .first()
    .waitFor();
await shot("execution-detail", { fullPage: true, settle: 1500 });

// A running execution, for the live log view
const running = (await get("/v1/executions?state=running&limit=5")).items[0];
if (running) {
    await page.goto(`${base}/executions/${running.id}`);
    await page.getByRole("log", { name: "Execution log" }).waitFor();
    await shot("execution-live", { fullPage: true, settle: 2500 });
}

// Failure recovery: a job that failed on one device and succeeded on another
const jobs = (await get("/v1/jobs?state=succeeded&limit=200")).items.filter((j) => j.attempts > 1);
if (jobs[0]) {
    await page.goto(`${base}/jobs/${jobs[0].id}`);
    await page.getByRole("list", { name: "Job attempts" }).waitFor();
    await shot("failure-recovery", { settle: 1000 });
}

// Templates, with a real example loaded
await page.goto(`${base}/templates`);
await page.getByRole("button", { name: /app-store-screenshots/ }).click();
await page.getByText("Valid", { exact: true }).waitFor();
await shot("templates", { fullPage: true, settle: 800 });

// Schedules, alerts, admin
await page.goto(`${base}/schedules`);
await page.getByRole("heading", { name: "Schedules", level: 1 }).waitFor();
await shot("schedules", { fullPage: true, settle: 800 });
await page.goto(`${base}/alerts`);
await page.getByRole("heading", { name: "Alerts", level: 1 }).waitFor();
await page.getByRole("tab", { name: "resolved" }).click();
await shot("alerts", { fullPage: true, settle: 800 });
await page.goto(`${base}/admin`);
await page.getByRole("heading", { name: "Admin", level: 1 }).waitFor();
await shot("admin-keys-audit", { fullPage: true, settle: 800 });

// Phone-width layout
const phone = await browser.newContext({
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 2,
});
const mobile = await phone.newPage();
await mobile.goto(`${base}/`);
await mobile.getByTestId("device-tile").first().waitFor();
await mobile.waitForTimeout(800);
await mobile.screenshot({ path: path.join(out, "fleet-overview-mobile.png") });
console.log("captured fleet-overview-mobile");

await browser.close();
