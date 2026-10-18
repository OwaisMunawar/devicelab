import { describe, expect, it } from "vitest";
import { ApiError, parseFrame, toApiError } from "../src/api/client";
import { keysForTopic } from "../src/api/live";
import { formatDuration, percent, relativeTime, titleCase } from "../src/lib/format";
import { isDeviceState, toneFor } from "../src/lib/states";

describe("format", () => {
  it("formats durations across magnitudes", () => {
    expect(formatDuration(null)).toBe("-");
    expect(formatDuration(450)).toBe("450 ms");
    expect(formatDuration(2500)).toBe("2.5 s");
    expect(formatDuration(125_000)).toBe("2m 5s");
  });

  it("describes times relative to now, past and future", () => {
    const now = Date.parse("2026-03-01T12:00:00Z");
    expect(relativeTime(null, now)).toBe("never");
    expect(relativeTime("2026-03-01T11:59:58Z", now)).toBe("just now");
    expect(relativeTime("2026-03-01T11:50:00Z", now)).toBe("10m ago");
    expect(relativeTime("2026-03-01T14:00:00Z", now)).toBe("in 2h");
    expect(relativeTime("2026-02-27T12:00:00Z", now)).toBe("2d ago");
  });

  it("formats percentages and labels", () => {
    expect(percent(0.934)).toBe("93%");
    expect(percent(null)).toBe("-");
    expect(titleCase("wda_crashed")).toBe("Wda crashed");
  });
});

describe("states", () => {
  it("knows device states and maps run states onto tones", () => {
    expect(isDeviceState("busy")).toBe(true);
    expect(isDeviceState("running")).toBe(false);
    expect(toneFor("busy")).toBe("busy");
    expect(toneFor("succeeded")).toBe("success");
    expect(toneFor("lost")).toBe("warning");
    expect(toneFor("who-knows")).toBe("neutral");
  });
});

describe("api errors", () => {
  it("reads the server's error envelope", () => {
    const error = toApiError(409, {
      error: {
        code: "stale_fence",
        message: "fencing token is stale",
        details: { active: 5 },
        correlation_id: "abc",
      },
    });
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      status: 409,
      code: "stale_fence",
      correlationId: "abc",
      details: { active: 5 },
    });
  });

  it("degrades gracefully on a body that is not an envelope", () => {
    expect(toApiError(502, "<html>bad gateway</html>")).toMatchObject({ code: "http_502" });
    expect(toApiError(500, null).message).toBe("Request failed (500)");
  });
});

describe("event stream parsing", () => {
  it("parses a named event with JSON data", () => {
    expect(parseFrame('event: job.updated\ndata: {"id":"1","state":"queued"}')).toEqual({
      event: "job.updated",
      data: { id: "1", state: "queued" },
    });
  });

  it("ignores keepalive comments and malformed data", () => {
    expect(parseFrame(": keepalive")).toBeNull();
    expect(parseFrame("event: x\ndata: {not json")).toBeNull();
  });
});

describe("live invalidation", () => {
  it("maps topics to the queries they make stale", () => {
    expect(keysForTopic("device.updated", { id: "d1" })).toContainEqual(["device", "d1"]);
    expect(keysForTopic("job.updated", { id: "j1" })).toEqual([["jobs"], ["fleet"], ["job", "j1"]]);
    expect(keysForTopic("alert.updated", {})).toEqual([["alerts"]]);
    expect(keysForTopic("something.else", {})).toEqual([]);
  });
});
