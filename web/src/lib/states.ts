export const DEVICE_STATES = [
  "available",
  "leased",
  "busy",
  "offline",
  "quarantined",
  "maintenance",
] as const;
export type DeviceState = (typeof DEVICE_STATES)[number];

export const STATE_LABEL: Record<DeviceState, string> = {
  available: "Available",
  leased: "Leased",
  busy: "Busy",
  offline: "Offline",
  quarantined: "Quarantined",
  maintenance: "Maintenance",
};

export function isDeviceState(value: string): value is DeviceState {
  return (DEVICE_STATES as readonly string[]).includes(value);
}

export type Tone = DeviceState | "neutral" | "success" | "danger" | "warning" | "info" | "accent";

/** Colours come from CSS tokens; each tone maps to a background, foreground and dot token. */
export const TONE_VARS: Record<Tone, { bg: string; fg: string; dot: string }> = {
  available: tokens("available"),
  leased: tokens("leased"),
  busy: tokens("busy"),
  offline: tokens("offline"),
  quarantined: tokens("quarantined"),
  maintenance: tokens("maintenance"),
  neutral: tokens("offline"),
  success: tokens("available"),
  danger: tokens("quarantined"),
  warning: tokens("maintenance"),
  info: tokens("leased"),
  accent: { bg: "var(--accent-soft)", fg: "var(--accent)", dot: "var(--accent)" },
};

function tokens(name: string): { bg: string; fg: string; dot: string } {
  return { bg: `var(--${name}-bg)`, fg: `var(--${name}-fg)`, dot: `var(--${name}-dot)` };
}

const EXECUTION_TONE: Record<string, Tone> = {
  succeeded: "success",
  running: "busy",
  pending: "neutral",
  failed: "danger",
  lost: "warning",
  cancelled: "neutral",
  skipped: "neutral",
  queued: "info",
  leased: "leased",
};

export function toneFor(state: string): Tone {
  if (isDeviceState(state)) return state;
  return EXECUTION_TONE[state] ?? "neutral";
}
