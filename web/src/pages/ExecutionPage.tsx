import { Link, useParams } from "@tanstack/react-router";
import { useDevice, useExecution } from "../api/hooks";
import { ArtifactImage } from "../components/ArtifactImage";
import { LogViewer } from "../components/LogViewer";
import { PageHeader, QueryError } from "../components/PageHeader";
import { StateBadge } from "../components/StateBadge";
import { Badge, Card, EmptyState, Timeline } from "../components/ui";
import type { TimelineItem } from "../components/ui";
import { formatDuration, formatTime, titleCase } from "../lib/format";
import { toneFor } from "../lib/states";

export function ExecutionPage() {
  const { executionId } = useParams({ from: "/executions/$executionId" });
  const execution = useExecution(executionId);
  const device = useDevice(execution.data?.device_id ?? "");
  if (execution.isError) return <QueryError error={execution.error} />;
  const e = execution.data;
  if (!e) return <p className="text-sm text-muted">Loading execution...</p>;

  const running = e.state === "running" || e.state === "pending";
  const steps: TimelineItem[] = e.steps.map((s) => ({
    id: String(s.index),
    tone: toneFor(s.state),
    title: (
      <>
        {s.index + 1}. {s.name} <span className="ml-1 font-mono text-xs text-muted">{s.type}</span>
      </>
    ),
    meta: `${titleCase(s.state)}${s.duration_ms !== null ? ` · ${formatDuration(s.duration_ms)}` : ""}${
      s.attempts > 1 ? ` · ${s.attempts} attempts` : ""
    }`,
    detail: s.error ? (
      <>
        {s.failure_class && <Badge tone="danger">{titleCase(s.failure_class)}</Badge>} {s.error}
      </>
    ) : null,
  }));
  const shots = e.artifacts.filter((a) => a.kind === "screenshot");
  const files = e.artifacts.filter((a) => a.kind !== "screenshot");

  return (
    <>
      <PageHeader
        title={`Execution ${e.id.slice(0, 8)}`}
        description={
          <>
            <Link
              to="/jobs/$jobId"
              params={{ jobId: e.job_id }}
              className="text-accent hover:underline"
            >
              Job {e.job_id.slice(0, 8)}
            </Link>{" "}
            · attempt {e.attempt} · on{" "}
            {device.data ? (
              <Link
                to="/devices/$deviceId"
                params={{ deviceId: device.data.id }}
                className="text-accent hover:underline"
              >
                {device.data.name}
              </Link>
            ) : (
              "device"
            )}
            {" · "}started {formatTime(e.started_at)}
          </>
        }
        actions={<StateBadge state={e.state} />}
      />
      {e.error && (
        <p role="alert" className="mb-4 rounded-lg border border-line bg-surface p-3 text-sm">
          {e.failure_class && (
            <Badge tone={e.state === "lost" ? "warning" : "danger"}>
              {titleCase(e.failure_class)}
            </Badge>
          )}{" "}
          {e.error}
        </p>
      )}
      <div className="grid gap-4 xl:grid-cols-[22rem_1fr]">
        <Card title="Step timeline" description={`${e.steps.length} steps`}>
          <Timeline items={steps} label="Execution steps" />
        </Card>
        <div className="min-w-0 space-y-4">
          <Card title="Live log" flush>
            <LogViewer key={e.id} executionId={e.id} live={running} />
          </Card>
          <Card
            title="Artifacts"
            description="Captured on the device and stored by the control plane."
          >
            {e.artifacts.length === 0 ? (
              <EmptyState
                title="No artifacts"
                description="Screenshots and logs appear here as steps finish."
              />
            ) : (
              <div className="flex flex-wrap items-start gap-4">
                {shots.map((a) => (
                  <ArtifactImage key={a.id} artifact={a} />
                ))}
                {files.map((a) => (
                  <Badge key={a.id} tone="neutral" dot={false}>
                    {a.name} · {(a.size_bytes / 1024).toFixed(1)} KB
                  </Badge>
                ))}
              </div>
            )}
          </Card>
        </div>
      </div>
    </>
  );
}
