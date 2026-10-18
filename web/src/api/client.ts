const STORAGE_KEY = "devicelab.apiKey";

let memoryKey: string | null = null;

export function getApiKey(): string | null {
  if (memoryKey) return memoryKey;
  try {
    memoryKey = window.localStorage.getItem(STORAGE_KEY);
  } catch {
    // Storage can be unavailable (private mode); the key then lives for this tab only.
  }
  return memoryKey;
}

export function setApiKey(key: string | null): void {
  memoryKey = key;
  try {
    if (key) window.localStorage.setItem(STORAGE_KEY, key);
    else window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    // see getApiKey
  }
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details: Record<string, unknown> = {},
    readonly correlationId: string | null = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

/** Turns the server's `{error: {code, message, details}}` envelope into a typed error. */
export function toApiError(status: number, body: unknown): ApiError {
  if (isRecord(body) && isRecord(body.error)) {
    const e = body.error;
    return new ApiError(
      status,
      typeof e.code === "string" ? e.code : `http_${status}`,
      typeof e.message === "string" ? e.message : `Request failed (${status})`,
      isRecord(e.details) ? e.details : {},
      typeof e.correlation_id === "string" ? e.correlation_id : null,
    );
  }
  return new ApiError(status, `http_${status}`, `Request failed (${status})`);
}

function headers(extra?: HeadersInit): Headers {
  const h = new Headers(extra);
  const key = getApiKey();
  if (key) h.set("Authorization", `Bearer ${key}`);
  return h;
}

async function parse(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return text;
  }
}

export async function api<T>(
  path: string,
  init: { method?: string; body?: unknown; idempotencyKey?: string } = {},
): Promise<T> {
  const h = headers();
  if (init.body !== undefined) h.set("Content-Type", "application/json");
  if (init.idempotencyKey) h.set("Idempotency-Key", init.idempotencyKey);
  const response = await fetch(path, {
    method: init.method ?? "GET",
    headers: h,
    ...(init.body !== undefined ? { body: JSON.stringify(init.body) } : {}),
  });
  const body = await parse(response);
  if (!response.ok) throw toApiError(response.status, body);
  return body as T;
}

export async function apiBlob(path: string): Promise<Blob> {
  const response = await fetch(path, { headers: headers() });
  if (!response.ok) throw toApiError(response.status, await parse(response));
  return response.blob();
}

export async function apiText(path: string): Promise<string> {
  const response = await fetch(path, { headers: headers() });
  if (!response.ok) throw toApiError(response.status, await parse(response));
  return response.text();
}

export interface StreamEvent {
  event: string;
  data: unknown;
}

/** Parses a server-sent-events body. Uses fetch rather than EventSource so the API key stays
 *  in a header instead of a URL. */
export async function streamEvents(
  path: string,
  signal: AbortSignal,
  onEvent: (event: StreamEvent) => void,
  onOpen?: () => void,
): Promise<void> {
  const response = await fetch(path, { headers: headers({ Accept: "text/event-stream" }), signal });
  if (!response.ok || !response.body) throw toApiError(response.status, null);
  onOpen?.();
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    buffer += value;
    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const parsed = parseFrame(buffer.slice(0, boundary));
      buffer = buffer.slice(boundary + 2);
      if (parsed) onEvent(parsed);
      boundary = buffer.indexOf("\n\n");
    }
  }
}

export function parseFrame(frame: string): StreamEvent | null {
  let event = "message";
  const data: string[] = [];
  for (const line of frame.split("\n")) {
    if (line.startsWith(":")) continue;
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data.push(line.slice(5).trim());
  }
  if (data.length === 0) return null;
  try {
    return { event, data: JSON.parse(data.join("\n")) as unknown };
  } catch {
    return null;
  }
}
