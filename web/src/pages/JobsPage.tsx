import { Link } from "@tanstack/react-router";
import { useState } from "react";
import type { SyntheticEvent } from "react";
import { apiText } from "../api/client";
import { useCancelJob, useFleetSummary, useJobs, useSubmitJob, useTemplates } from "../api/hooks";
import { PageHeader, QueryError } from "../components/PageHeader";
import { StateBadge } from "../components/StateBadge";
import { Button, Card, SelectField, Stat, Table, TextAreaField, TextField } from "../components/ui";
import { formatSeconds, relativeTime, shortId } from "../lib/format";

const STATES = ["", "queued", "leased", "running", "succeeded", "failed", "cancelled"];

function SubmitForm() {
  const templates = useTemplates();
  const submit = useSubmitJob();
  const [template, setTemplate] = useState("");
  const [priority, setPriority] = useState("0");
  const [params, setParams] = useState("{}");
  const [site, setSite] = useState("");
  const [parseError, setParseError] = useState<string | null>(null);
  const chosen = template !== "" ? template : (templates.data?.[0]?.name ?? "");

  function onSubmit(event: SyntheticEvent) {
    event.preventDefault();
    let parsed: unknown;
    try {
      parsed = JSON.parse(params || "{}");
    } catch {
      setParseError("Parameters must be valid JSON.");
      return;
    }
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      setParseError("Parameters must be a JSON object.");
      return;
    }
    setParseError(null);
    submit.mutate({
      template: chosen,
      priority: Number(priority),
      params: parsed as Record<string, unknown>,
      ...(site ? { requirement: { site } } : {}),
    });
  }

  return (
    <Card title="Submit a job" description="Runs a template on the first devices that match.">
      <form onSubmit={onSubmit} className="grid gap-3 md:grid-cols-[1fr_8rem_10rem]">
        <SelectField label="Template" value={chosen} onChange={(e) => setTemplate(e.target.value)}>
          {(templates.data ?? []).map((t) => (
            <option key={t.id} value={t.name}>
              {t.name}
            </option>
          ))}
        </SelectField>
        <TextField
          label="Priority (0-100)"
          type="number"
          min={0}
          max={100}
          value={priority}
          onChange={(e) => setPriority(e.target.value)}
        />
        <TextField
          label="Site (optional)"
          value={site}
          onChange={(e) => setSite(e.target.value)}
          placeholder="berlin"
        />
        <div className="md:col-span-3">
          <TextAreaField
            label="Parameters (JSON)"
            rows={3}
            value={params}
            onChange={(e) => setParams(e.target.value)}
          />
        </div>
        <div className="flex items-center gap-3 md:col-span-3">
          <Button type="submit" variant="primary" busy={submit.isPending} disabled={!chosen}>
            Submit job
          </Button>
          {parseError && (
            <p role="alert" className="text-sm text-danger">
              {parseError}
            </p>
          )}
          {submit.isError && <QueryError error={submit.error} />}
          {submit.isSuccess && (
            <p role="status" className="text-sm text-muted">
              Queued {shortId(submit.data.id)}.
            </p>
          )}
        </div>
      </form>
    </Card>
  );
}

export function JobsPage() {
  const [state, setState] = useState("");
  const jobs = useJobs({ state });
  const summary = useFleetSummary();
  const cancel = useCancelJob();
  const counts = summary.data?.jobs ?? {};

  async function downloadCsv() {
    const text = await apiText("/v1/reports/executions.csv");
    const url = URL.createObjectURL(new Blob([text], { type: "text/csv" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = "executions.csv";
    link.click();
    URL.revokeObjectURL(url);
  }

  return (
    <>
      <PageHeader
        title="Jobs and queue"
        description="Priority queue with aging, so old low-priority work still gets its turn."
        actions={<Button onClick={() => void downloadCsv()}>Export CSV (24 h)</Button>}
      />
      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-5">
        <Stat label="Queued" value={counts.queued ?? 0} />
        <Stat label="Leased" value={counts.leased ?? 0} />
        <Stat label="Running" value={counts.running ?? 0} />
        <Stat label="Succeeded" value={counts.succeeded ?? 0} tone="good" />
        <Stat
          label="Failed"
          value={counts.failed ?? 0}
          tone={(counts.failed ?? 0) > 0 ? "bad" : "default"}
        />
      </div>
      <SubmitForm />
      <Card
        className="mt-4"
        title="Jobs"
        flush
        actions={
          <>
            <label htmlFor="job-state" className="sr-only">
              State
            </label>
            <select
              id="job-state"
              value={state}
              onChange={(e) => setState(e.target.value)}
              className="rounded-lg border border-line bg-surface px-2 py-1.5 text-xs"
            >
              {STATES.map((s) => (
                <option key={s} value={s}>
                  {s || "All states"}
                </option>
              ))}
            </select>
          </>
        }
      >
        {jobs.isError ? (
          <QueryError error={jobs.error} />
        ) : (
          <Table
            caption="Jobs"
            rows={jobs.data?.items ?? []}
            rowKey={(j) => j.id}
            empty={{
              title: "No jobs yet",
              description: "Submit one above or let a schedule enqueue work.",
            }}
            columns={[
              {
                key: "id",
                header: "Job",
                cell: (j) => (
                  <Link
                    to="/jobs/$jobId"
                    params={{ jobId: j.id }}
                    className="font-mono text-accent hover:underline"
                  >
                    {shortId(j.id)}
                  </Link>
                ),
              },
              { key: "tpl", header: "Template", cell: (j) => j.template_name },
              { key: "state", header: "State", cell: (j) => <StateBadge state={j.state} /> },
              { key: "prio", header: "Priority", cell: (j) => j.priority },
              { key: "att", header: "Attempts", cell: (j) => `${j.attempts}/${j.max_attempts}` },
              { key: "wait", header: "Queue wait", cell: (j) => formatSeconds(j.queue_wait_s) },
              {
                key: "by",
                header: "Submitted by",
                cell: (j) => <span className="text-muted">{j.submitted_by}</span>,
              },
              { key: "age", header: "Created", cell: (j) => relativeTime(j.created_at) },
              {
                key: "act",
                header: "",
                align: "right",
                cell: (j) =>
                  j.state === "queued" || j.state === "leased" || j.state === "running" ? (
                    <Button size="sm" variant="ghost" onClick={() => cancel.mutate(j.id)}>
                      Cancel
                    </Button>
                  ) : null,
              },
            ]}
          />
        )}
      </Card>
    </>
  );
}
