import { useEffect, useMemo, useState } from "react";
import { useSaveTemplate, useTemplates, useValidateTemplate } from "../api/hooks";
import type { Template, TemplateReport } from "../api/types";
import { PageHeader, QueryError } from "../components/PageHeader";
import { Badge, Button, Card, EmptyState } from "../components/ui";
import { formatTime } from "../lib/format";

const STARTER = `name: my-workflow
description: What this workflow checks.
steps:
  - id: open_settings
    type: launch_app
    bundle_id: com.apple.Preferences
  - id: evidence
    type: screenshot
    artifact_name: settings
`;

function specToText(template: Template): string {
  // JSON is valid YAML, and it round-trips the server's own representation exactly.
  return JSON.stringify(template.spec, null, 2);
}

export function TemplatesPage() {
  const templates = useTemplates();
  const validate = useValidateTemplate();
  const save = useSaveTemplate();
  const [selected, setSelected] = useState<string | null>(null);
  const [text, setText] = useState(STARTER);
  const [report, setReport] = useState<TemplateReport | null>(null);
  const { mutate: runValidate } = validate;

  useEffect(() => {
    const timer = setTimeout(() => runValidate(text, { onSuccess: setReport }), 350);
    return () => clearTimeout(timer);
  }, [text, runValidate]);

  const current = useMemo(
    () => templates.data?.find((t) => t.name === selected) ?? null,
    [templates.data, selected],
  );

  function pick(template: Template | null) {
    setSelected(template?.name ?? null);
    setText(template ? specToText(template) : STARTER);
  }

  return (
    <>
      <PageHeader
        title="Workflow templates"
        description="YAML or JSON, validated as you type. Steps come from a fixed allow-list; there is no shell step."
        actions={<Button onClick={() => pick(null)}>New template</Button>}
      />
      <div className="grid gap-4 lg:grid-cols-[16rem_1fr]">
        <Card title="Templates" flush>
          {templates.isError ? (
            <QueryError error={templates.error} />
          ) : (templates.data ?? []).length === 0 ? (
            <EmptyState title="No templates" />
          ) : (
            <ul>
              {(templates.data ?? []).map((t) => (
                <li key={t.id}>
                  <button
                    type="button"
                    onClick={() => pick(t)}
                    aria-current={t.name === selected}
                    className={`block w-full border-b border-line px-4 py-2.5 text-left text-sm last:border-0 hover:bg-surface-2 ${
                      t.name === selected ? "bg-accent-soft" : ""
                    }`}
                  >
                    <span className="font-medium">{t.name}</span>
                    <span className="block text-xs text-muted">
                      v{t.version} · {formatTime(t.updated_at)}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <div className="min-w-0 space-y-4">
          <Card
            title={current ? `Editing ${current.name}` : "New template"}
            actions={
              <Button
                variant="primary"
                busy={save.isPending}
                disabled={!report?.valid}
                onClick={() => save.mutate({ name: current?.name ?? null, content: text })}
              >
                {current ? "Save new version" : "Create"}
              </Button>
            }
          >
            <label htmlFor="template-editor" className="sr-only">
              Template source
            </label>
            <textarea
              id="template-editor"
              value={text}
              onChange={(e) => setText(e.target.value)}
              spellCheck={false}
              rows={18}
              className="w-full rounded-lg border border-line bg-surface-2 p-3 font-mono text-xs leading-relaxed"
            />
            {save.isError && <QueryError error={save.error} />}
            {save.isSuccess && (
              <p role="status" className="mt-2 text-sm text-muted">
                Saved version {save.data.version}.
              </p>
            )}
          </Card>
          <Card title="Validation">
            <div aria-live="polite">
              {!report ? (
                <p className="text-sm text-muted">Checking...</p>
              ) : report.valid ? (
                <div className="space-y-2">
                  <Badge tone="success">Valid</Badge>
                  <p className="text-sm text-muted">
                    {report.name} · {report.step_count} steps
                  </p>
                  <div className="flex flex-wrap gap-1.5">
                    {report.step_types.map((t, i) => (
                      <Badge key={`${t}-${i}`} tone="neutral" dot={false}>
                        {t}
                      </Badge>
                    ))}
                  </div>
                </div>
              ) : (
                <div className="space-y-2">
                  <Badge tone="danger">{report.errors.length} problem(s)</Badge>
                  <ul className="space-y-1 text-sm">
                    {report.errors.map((err, i) => (
                      <li key={i}>
                        {err.path && (
                          <code className="mr-2 rounded bg-surface-2 px-1 py-0.5 text-xs">
                            {err.path}
                          </code>
                        )}
                        {err.message}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          </Card>
        </div>
      </div>
    </>
  );
}
