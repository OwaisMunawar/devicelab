import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, api, getApiKey, setApiKey } from "../src/api/client";

afterEach(() => {
  vi.restoreAllMocks();
  setApiKey(null);
});

function initOf(spy: { mock: { calls: unknown[][] } }): RequestInit {
  const call = spy.mock.calls.at(0);
  if (!call) throw new Error("fetch was not called");
  return call[1] as RequestInit;
}

function mockFetch(status: number, body: unknown) {
  return vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );
}

describe("api()", () => {
  it("sends the key as a bearer token and parses JSON", async () => {
    setApiKey("dlk_test");
    const spy = mockFetch(200, { ok: true });
    await expect(api<{ ok: boolean }>("/v1/fleet/summary")).resolves.toEqual({ ok: true });
    const headers = new Headers(initOf(spy).headers);
    expect(headers.get("Authorization")).toBe("Bearer dlk_test");
  });

  it("sends JSON bodies and idempotency keys", async () => {
    const spy = mockFetch(201, { id: "j1" });
    await api("/v1/jobs", { method: "POST", body: { template: "x" }, idempotencyKey: "k-1" });
    const init = initOf(spy);
    expect(init.method).toBe("POST");
    expect(init.body).toBe('{"template":"x"}');
    const headers = new Headers(init.headers);
    expect(headers.get("Idempotency-Key")).toBe("k-1");
    expect(headers.get("Content-Type")).toBe("application/json");
  });

  it("throws a typed ApiError carrying the code", async () => {
    mockFetch(403, { error: { code: "forbidden", message: "requires role admin", details: {} } });
    const failure = await api("/v1/api-keys").catch((e: unknown) => e);
    expect(failure).toBeInstanceOf(ApiError);
    expect(failure).toMatchObject({ status: 403, code: "forbidden" });
  });

  it("returns null for an empty 204 response", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(null, { status: 204 }));
    await expect(api("/v1/schedules/x", { method: "DELETE" })).resolves.toBeNull();
  });
});

describe("key storage", () => {
  it("round-trips through localStorage and clears", () => {
    setApiKey("dlk_abc");
    expect(window.localStorage.getItem("devicelab.apiKey")).toBe("dlk_abc");
    setApiKey(null);
    expect(getApiKey()).toBeNull();
  });
});
