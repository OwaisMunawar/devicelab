import { Link, useParams } from "@tanstack/react-router";
import { useJob } from "../api/hooks";
import { PageHeader, QueryError } from "../components/PageHeader";
import { StateBadge } from "../components/StateBadge";
import { Badge, Card, Timeline } from "../components/ui";
import type { TimelineItem } from "../components/ui";
import { formatSeconds, formatTime, titleCase } from "../lib/format";
import { toneFor } from "../lib/states";

export function JobDetailPage() {
  const { jobId } = useParams({ from: "/jobs/$jobId" });
  const job = useJob(jobId);
  if (job.isError) return <QueryError error={job.error} />;
  const j = job.data;
  if (!j) return <p className="text-sm text-muted">Loading job...</p>;

  const attempts: TimelineItem[] = j.executions.map((e) => ({
    id: e.id,
    tone: toneFor(e.state),
    title: (
      <Link
        to="/executions/$executionId"
        params={{ executionId: e.id }}
        className="text-accent hover:underline"
      >
        Attempt {e.attempt} · {titleCase(e.state)}
      </Link>
    ),
    meta: formatTime(e.finished_at ?? e.created_at),
    detail: (
      <>
        {e.failure_class && (
          <span className="mr-2">
            <Badge tone={e.state === "lost" ? "warning" : "danger"}>
              {titleCase(e.failure_class)}
            </Badge>
          </span>
        )}
        {e.error ?? (e.state === "succeeded" ? "Completed all steps." : null)}
        {e.session_restarts > 0 && ` Session restarted ${e.session_restarts}x.`}
        {e.reboots > 0 && ` Device rebooted ${e.reboots}x.`}
        {e.state !== "succeeded" && j.attempts > e.attempt && (
          <span className="mt-1 block text-xs">Requeued on a different device.</span>
        )}
      </>
    ),
  }));

  return (
    <>
      <PageHeader
        title={`Job ${j.id.slice(0, 8)}`}
        description={`${j.template_name} · priority ${j.priority} · submitted by ${j.submitted_by}`}
        actions={<StateBadge state={j.state} />}
      />
      <div className="grid gap-4 lg:grid-cols-[1fr_18rem]">
        <Card
          title="Attempts"
          description="Infrastructure failures requeue the job on another device; test failures do not."
        >
          {attempts.length === 0 ? (
            <p className="text-sm text-muted">Waiting for a matching device.</p>
          ) : (
            <Timeline items={attempts} label="Job attempts" />
          )}
        </Card>
        <Card title="Details">
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
            <dt className="text-muted">Attempts</dt>
            <dd>
              {j.attempts} of {j.max_attempts}
            </dd>
            <dt className="text-muted">Queue wait</dt>
            <dd>{formatSeconds(j.queue_wait_s)}</dd>
            <dt className="text-muted">Created</dt>
            <dd>{formatTime(j.created_at)}</dd>
            <dt className="text-muted">Finished</dt>
            <dd>{formatTime(j.finished_at)}</dd>
            <dt className="text-muted">Requirement</dt>
            <dd className="font-mono text-xs">{JSON.stringify(j.requirement)}</dd>
          </dl>
          {j.error && <p className="mt-3 text-sm text-danger">{j.error}</p>}
        </Card>
      </div>
    </>
  );
}
