import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { KeyGate } from "../src/components/KeyGate";
import { ThemeToggle } from "../src/components/ThemeToggle";
import { setApiKey } from "../src/api/client";

function urlOf(input: RequestInfo | URL): string {
  return input instanceof Request ? input.url : input.toString();
}

afterEach(() => {
  vi.restoreAllMocks();
  setApiKey(null);
});

describe("ThemeToggle", () => {
  it("cycles system, light, dark and applies the attribute", async () => {
    render(<ThemeToggle />);
    const button = screen.getByRole("button", { name: /theme: system/i });
    expect(document.documentElement).not.toHaveAttribute("data-theme");

    await userEvent.click(button);
    expect(document.documentElement).toHaveAttribute("data-theme", "light");
    await userEvent.click(screen.getByRole("button", { name: /theme: light/i }));
    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
    expect(window.localStorage.getItem("devicelab.theme")).toBe("dark");
    await userEvent.click(screen.getByRole("button", { name: /theme: dark/i }));
    expect(document.documentElement).not.toHaveAttribute("data-theme");
  });
});

describe("KeyGate", () => {
  function renderGate() {
    return render(
      <QueryClientProvider client={new QueryClient()}>
        <KeyGate>
          <p>dashboard</p>
        </KeyGate>
      </QueryClientProvider>,
    );
  }

  it("asks for a key when none is stored, then lets the user in", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = urlOf(input);
      if (url.endsWith("/config.json")) return Promise.resolve(new Response("{}", { status: 404 }));
      return Promise.resolve(new Response(JSON.stringify({ devices: {} }), { status: 200 }));
    });
    renderGate();
    const field = await screen.findByLabelText("API key");
    await userEvent.type(field, "dlk_secret");
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByText("dashboard")).toBeVisible();
    expect(window.localStorage.getItem("devicelab.apiKey")).toBe("dlk_secret");
  });

  it("rejects a key the server refuses", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      if (urlOf(input).endsWith("/config.json"))
        return Promise.resolve(new Response("{}", { status: 404 }));
      return Promise.resolve(
        new Response(JSON.stringify({ error: { code: "invalid_api_key", message: "no" } }), {
          status: 401,
        }),
      );
    });
    renderGate();
    await userEvent.type(await screen.findByLabelText("API key"), "dlk_wrong");
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("not accepted");
    expect(screen.queryByText("dashboard")).toBeNull();
  });

  it("signs in with the demo key from runtime config", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      if (urlOf(input).endsWith("/config.json")) {
        return Promise.resolve(
          new Response(JSON.stringify({ demoKey: "dlk_demo" }), { status: 200 }),
        );
      }
      return Promise.resolve(new Response("{}", { status: 200 }));
    });
    renderGate();
    await waitFor(() => expect(screen.getByText("dashboard")).toBeVisible());
  });
});
