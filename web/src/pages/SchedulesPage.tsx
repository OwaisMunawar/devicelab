import { useState } from "react";
import type { SyntheticEvent } from "react";
import { useCreateSchedule, useScheduleAction, useSchedules, useTemplates } from "../api/hooks";
import { PageHeader, QueryError } from "../components/PageHeader";
import { Badge, Button, Card, SelectField, Table, TextField } from "../components/ui";
import { formatTime, relativeTime } from "../lib/format";

function CreateForm() {
  const templates = useTemplates();
  const create = useCreateSchedule();
  const [name, setName] = useState("");
  const [template, setTemplate] = useState("");
  const [cron, setCron] = useState("0 3 * * *");
  const [timezone, setTimezone] = useState("UTC");
  const [misfire, setMisfire] = useState<"skip" | "run_once">("skip");

  function onSubmit(event: SyntheticEvent) {
    event.preventDefault();
    create.mutate({
      name,
      template: template !== "" ? template : (templates.data?.[0]?.name ?? ""),
      cron,
      timezone,
      misfire_policy: misfire,
      priority: 1,
      params: {},
    });
  }

  return (
    <Card
      title="New schedule"
      description="Recurring work is enqueued as idempotent jobs, one per fire time."
    >
      <form onSubmit={onSubmit} className="grid gap-3 md:grid-cols-5">
        <TextField
          label="Name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          pattern="[a-z0-9][a-z0-9\-]{1,62}"
          required
          placeholder="nightly-check"
        />
        <SelectField
          label="Template"
          value={template}
          onChange={(e) => setTemplate(e.target.value)}
        >
          {(templates.data ?? []).map((t) => (
            <option key={t.id}>{t.name}</option>
          ))}
        </SelectField>
        <TextField
          label="Cron"
          value={cron}
          onChange={(e) => setCron(e.target.value)}
          className="font-mono"
          required
        />
        <TextField
          label="Time zone"
          value={timezone}
          onChange={(e) => setTimezone(e.target.value)}
          required
        />
        <SelectField
          label="If a run is missed"
          value={misfire}
          onChange={(e) => setMisfire(e.target.value as "skip" | "run_once")}
        >
          <option value="skip">Skip it</option>
          <option value="run_once">Run once on recovery</option>
        </SelectField>
        <div className="flex items-center gap-3 md:col-span-5">
          <Button type="submit" variant="primary" busy={create.isPending}>
            Create schedule
          </Button>
          {create.isError && <QueryError error={create.error} />}
        </div>
      </form>
    </Card>
  );
}

export function SchedulesPage() {
  const schedules = useSchedules();
  const act = useScheduleAction();
  return (
    <>
      <PageHeader
        title="Schedules"
        description="Cron expressions with time zones and an explicit policy for runs missed during an outage."
      />
      <Card title="Schedules" flush>
        {schedules.isError ? (
          <QueryError error={schedules.error} />
        ) : (
          <Table
            caption="Schedules"
            rows={schedules.data ?? []}
            rowKey={(s) => s.id}
            empty={{ title: "No schedules", description: "Create one below." }}
            columns={[
              {
                key: "name",
                header: "Name",
                cell: (s) => <span className="font-medium">{s.name}</span>,
              },
              { key: "tpl", header: "Template", cell: (s) => s.template_name },
              {
                key: "cron",
                header: "Cron",
                cell: (s) => <code className="text-xs">{s.cron}</code>,
              },
              { key: "tz", header: "Time zone", cell: (s) => s.timezone },
              {
                key: "mis",
                header: "Missed runs",
                cell: (s) => (s.misfire_policy === "run_once" ? "Run once" : "Skip"),
              },
              {
                key: "next",
                header: "Next run",
                cell: (s) =>
                  s.enabled ? (
                    <span title={formatTime(s.next_fire_at)}>{relativeTime(s.next_fire_at)}</span>
                  ) : (
                    "-"
                  ),
              },
              { key: "last", header: "Last run", cell: (s) => relativeTime(s.last_fire_at) },
              {
                key: "state",
                header: "Status",
                cell: (s) => (
                  <Badge tone={s.enabled ? "success" : "offline"}>
                    {s.enabled ? "Enabled" : "Paused"}
                  </Badge>
                ),
              },
              {
                key: "act",
                header: "",
                align: "right",
                cell: (s) => (
                  <span className="inline-flex gap-1">
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() =>
                        act.mutate({ id: s.id, action: s.enabled ? "disable" : "enable" })
                      }
                    >
                      {s.enabled ? "Pause" : "Resume"}
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => act.mutate({ id: s.id, action: "delete" })}
                    >
                      Delete
                    </Button>
                  </span>
                ),
              },
            ]}
          />
        )}
      </Card>
      <div className="mt-4">
        <CreateForm />
      </div>
    </>
  );
}
