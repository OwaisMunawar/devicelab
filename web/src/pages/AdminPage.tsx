import { useState } from "react";
import type { SyntheticEvent } from "react";
import {
  useApiKeys,
  useAudit,
  useCreateApiKey,
  useCreateEnrolment,
  useRevokeApiKey,
} from "../api/hooks";
import { PageHeader, QueryError } from "../components/PageHeader";
import { Badge, Button, Card, SelectField, Table, TextField } from "../components/ui";
import { formatTime, relativeTime } from "../lib/format";

function ApiKeys() {
  const keys = useApiKeys();
  const create = useCreateApiKey();
  const revoke = useRevokeApiKey();
  const [name, setName] = useState("");
  const [role, setRole] = useState<"admin" | "operator" | "viewer">("viewer");

  function onSubmit(event: SyntheticEvent) {
    event.preventDefault();
    create.mutate({ name, role }, { onSuccess: () => setName("") });
  }

  return (
    <Card
      title="API keys"
      description="Keys are stored hashed. The secret is shown once, when created."
      flush
    >
      {create.isSuccess && (
        <div role="status" className="border-b border-line bg-accent-soft p-3 text-sm">
          New key for <strong>{create.data.name}</strong> (copy it now):{" "}
          <code className="break-all font-mono text-xs">{create.data.secret}</code>
        </div>
      )}
      <Table
        caption="API keys"
        rows={keys.data ?? []}
        rowKey={(k) => k.id}
        columns={[
          {
            key: "name",
            header: "Name",
            cell: (k) => <span className="font-medium">{k.name}</span>,
          },
          {
            key: "role",
            header: "Role",
            cell: (k) => (
              <Badge tone="accent" dot={false}>
                {k.role}
              </Badge>
            ),
          },
          {
            key: "prefix",
            header: "Prefix",
            cell: (k) => <code className="text-xs">{k.prefix}...</code>,
          },
          { key: "used", header: "Last used", cell: (k) => relativeTime(k.last_used_at) },
          {
            key: "state",
            header: "Status",
            cell: (k) =>
              k.revoked_at ? (
                <Badge tone="offline">Revoked</Badge>
              ) : (
                <Badge tone="success">Active</Badge>
              ),
          },
          {
            key: "act",
            header: "",
            align: "right",
            cell: (k) =>
              k.revoked_at ? null : (
                <Button size="sm" variant="ghost" onClick={() => revoke.mutate(k.id)}>
                  Revoke
                </Button>
              ),
          },
        ]}
      />
      <form
        onSubmit={onSubmit}
        className="grid gap-3 border-t border-line p-4 md:grid-cols-[1fr_10rem_auto] md:items-end"
      >
        <TextField
          label="New key name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          pattern="[A-Za-z0-9][A-Za-z0-9_\-]{1,62}"
          required
          placeholder="ci-pipeline"
        />
        <SelectField
          label="Role"
          value={role}
          onChange={(e) => setRole(e.target.value as typeof role)}
        >
          <option value="viewer">Viewer</option>
          <option value="operator">Operator</option>
          <option value="admin">Admin</option>
        </SelectField>
        <Button type="submit" variant="primary" busy={create.isPending}>
          Create key
        </Button>
      </form>
      {create.isError && (
        <div className="p-4 pt-0">
          <QueryError error={create.error} />
        </div>
      )}
    </Card>
  );
}

function Enrolment() {
  const create = useCreateEnrolment();
  const [host, setHost] = useState("");
  const [site, setSite] = useState("");

  return (
    <Card
      title="Connect a host"
      description="One-time token, valid for an hour. The agent dials out; no inbound ports are opened."
    >
      <form
        className="grid gap-3 md:grid-cols-[1fr_1fr_auto] md:items-end"
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate({ host_name: host, site, region: "default", max_concurrency: 4 });
        }}
      >
        <TextField
          label="Host name"
          value={host}
          onChange={(e) => setHost(e.target.value)}
          required
          placeholder="mac-lab-berlin-1"
          pattern="[A-Za-z0-9][A-Za-z0-9_.\-]{0,62}"
        />
        <TextField
          label="Site"
          value={site}
          onChange={(e) => setSite(e.target.value)}
          required
          placeholder="berlin"
        />
        <Button type="submit" variant="primary" busy={create.isPending}>
          Create token
        </Button>
      </form>
      {create.isSuccess && (
        <pre
          className="mt-3 overflow-x-auto rounded-lg bg-surface-2 p-3 font-mono text-xs"
          aria-label="Agent command"
        >
          {create.data.command}
        </pre>
      )}
      {create.isError && <QueryError error={create.error} />}
    </Card>
  );
}

function AuditLog() {
  const [action, setAction] = useState("");
  const audit = useAudit(action || undefined);
  return (
    <Card
      title="Audit log"
      description="Append-only. Enforced by a database trigger, not by application code."
      flush
      actions={
        <>
          <label htmlFor="audit-action" className="sr-only">
            Action
          </label>
          <input
            id="audit-action"
            value={action}
            onChange={(e) => setAction(e.target.value)}
            placeholder="filter: job.requeued"
            className="rounded-lg border border-line bg-surface px-2 py-1.5 text-xs"
          />
        </>
      }
    >
      {audit.isError ? (
        <QueryError error={audit.error} />
      ) : (
        <Table
          caption="Audit log"
          rows={audit.data?.items ?? []}
          rowKey={(a) => String(a.id)}
          empty={{ title: "No entries match" }}
          columns={[
            { key: "ts", header: "When", cell: (a) => formatTime(a.ts) },
            { key: "actor", header: "Actor", cell: (a) => a.actor },
            {
              key: "action",
              header: "Action",
              cell: (a) => <code className="text-xs">{a.action}</code>,
            },
            {
              key: "target",
              header: "Target",
              cell: (a) => `${a.target_type} ${a.target_id.slice(0, 8)}`,
            },
            {
              key: "details",
              header: "Details",
              cell: (a) => (
                <span className="font-mono text-xs text-muted">{JSON.stringify(a.details)}</span>
              ),
              className: "max-w-80 truncate",
            },
          ]}
        />
      )}
    </Card>
  );
}

export function AdminPage() {
  return (
    <>
      <PageHeader
        title="Admin"
        description="API keys, host enrolment and the audit trail. Admin role required."
      />
      <div className="space-y-4">
        <Enrolment />
        <ApiKeys />
        <AuditLog />
      </div>
    </>
  );
}
