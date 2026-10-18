import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import type { SyntheticEvent, ReactNode } from "react";
import { ApiError, api, getApiKey, setApiKey } from "../api/client";
import type { FleetSummary } from "../api/types";
import { Button, Card, TextField } from "./ui";

interface RuntimeConfig {
  demoKey?: string;
}

async function loadConfig(): Promise<RuntimeConfig> {
  try {
    const response = await fetch("/config.json", { cache: "no-store" });
    if (!response.ok) return {};
    const body: unknown = await response.json();
    return typeof body === "object" && body !== null ? body : {};
  } catch {
    return {};
  }
}

type Phase = "checking" | "needs-key" | "ready";

/** Holds the dashboard back until there is an API key the server accepts. */
export function KeyGate({ children }: { children: ReactNode }) {
  const client = useQueryClient();
  const [phase, setPhase] = useState<Phase>("checking");
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState("");

  async function verify(): Promise<boolean> {
    try {
      await api<FleetSummary>("/v1/fleet/summary");
      return true;
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) return false;
      throw caught;
    }
  }

  useEffect(() => {
    void (async () => {
      if (!getApiKey()) {
        const config = await loadConfig();
        if (config.demoKey) setApiKey(config.demoKey);
      }
      try {
        setPhase(getApiKey() && (await verify()) ? "ready" : "needs-key");
      } catch {
        setError("The control plane is not reachable.");
        setPhase("needs-key");
      }
    })();
  }, []);

  async function submit(event: SyntheticEvent) {
    event.preventDefault();
    setApiKey(draft.trim());
    setError(null);
    try {
      if (await verify()) {
        client.clear();
        setPhase("ready");
      } else {
        setError("That key was not accepted.");
      }
    } catch {
      setError("The control plane is not reachable.");
    }
  }

  if (phase === "ready") return <>{children}</>;
  if (phase === "checking") {
    return <p className="p-8 text-sm text-muted">Connecting to the control plane...</p>;
  }
  return (
    <main className="mx-auto grid min-h-screen max-w-md place-items-center p-4">
      <Card
        title="Sign in to DeviceLab"
        description="Paste an API key. It stays in this browser only."
      >
        <form onSubmit={(e) => void submit(e)} className="space-y-3">
          <TextField
            label="API key"
            type="password"
            autoComplete="off"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="dlk_..."
            required
          />
          {error && (
            <p role="alert" className="text-sm text-danger">
              {error}
            </p>
          )}
          <Button type="submit" variant="primary" className="w-full">
            Continue
          </Button>
        </form>
      </Card>
    </main>
  );
}
