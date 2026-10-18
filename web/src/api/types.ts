export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface Device {
  id: string;
  host_id: string;
  udid: string;
  name: string;
  model: string;
  model_family: string;
  ios_version: string;
  kind: string;
  site: string;
  region: string;
  labels: string[];
  state: string;
  battery_pct: number | null;
  storage_free_gb: number | null;
  thermal: string;
  connectivity: string;
  last_seen: string | null;
  consecutive_infra_failures: number;
  quarantine_reason: string | null;
  state_changed_at: string | null;
}

export interface HealthSample {
  ts: string;
  state: string;
  thermal: string;
  connectivity: string;
  battery_pct: number | null;
}

export interface Host {
  id: string;
  name: string;
  site: string;
  region: string;
  max_concurrency: number;
  connected: boolean;
  last_seen: string | null;
  agent_version: string | null;
}

export interface FleetSummary {
  devices: Record<string, number>;
  jobs: Record<string, number>;
  hosts_total: number;
  hosts_connected: number;
  leases_active: number;
  queue_depth: number;
  sites: string[];
}

export interface Job {
  id: string;
  template_name: string;
  params: Record<string, unknown>;
  requirement: Record<string, unknown>;
  priority: number;
  state: string;
  attempts: number;
  max_attempts: number;
  submitted_by: string;
  schedule_id: string | null;
  queue_wait_s: number | null;
  created_at: string;
  queued_at: string;
  started_at: string | null;
  finished_at: string | null;
  failure_class: string | null;
  error: string | null;
}

export interface Execution {
  id: string;
  job_id: string;
  device_id: string;
  host_id: string;
  attempt: number;
  state: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  failure_class: string | null;
  error: string | null;
  session_restarts: number;
  reboots: number;
}

export interface JobDetail extends Job {
  executions: Execution[];
}

export interface Step {
  index: number;
  step_id: string;
  type: string;
  name: string;
  state: string;
  attempts: number;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
  failure_class: string | null;
  error: string | null;
  output: Record<string, unknown>;
}

export interface Artifact {
  id: string;
  execution_id: string;
  name: string;
  kind: string;
  content_type: string;
  size_bytes: number;
  step_index: number | null;
  created_at: string;
}

export interface ExecutionDetail extends Execution {
  steps: Step[];
  artifacts: Artifact[];
}

export interface LogLine {
  id: number | null;
  ts: string;
  level: string;
  message: string;
  step_index: number | null;
}

export interface Template {
  id: string;
  name: string;
  version: number;
  description: string;
  spec: Record<string, unknown>;
  updated_at: string;
  created_by: string;
}

export interface TemplateReport {
  valid: boolean;
  name: string | null;
  step_count: number;
  step_types: string[];
  errors: { path?: string; message: string }[];
}

export interface Schedule {
  id: string;
  name: string;
  template_name: string;
  params: Record<string, unknown>;
  requirement: Record<string, unknown>;
  priority: number;
  cron: string;
  timezone: string;
  misfire_policy: string;
  enabled: boolean;
  next_fire_at: string | null;
  last_fire_at: string | null;
}

export interface Alert {
  id: string;
  kind: string;
  severity: string;
  dedupe_key: string;
  message: string;
  payload: Record<string, unknown>;
  state: string;
  occurrences: number;
  first_seen: string;
  last_seen: string;
  last_notified_at: string | null;
  resolved_at: string | null;
}

export interface AlertRule {
  id: string;
  name: string;
  kind: string;
  params: Record<string, unknown>;
  enabled: boolean;
  cooldown_s: number;
}

export interface Webhook {
  id: string;
  name: string;
  url: string;
  enabled: boolean;
  created_at: string;
}

export interface ApiKey {
  id: string;
  name: string;
  prefix: string;
  role: string;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
}

export interface ApiKeyCreated extends ApiKey {
  secret: string;
}

export interface AuditEntry {
  id: number | null;
  ts: string;
  actor: string;
  action: string;
  target_type: string;
  target_id: string;
  details: Record<string, unknown>;
  correlation_id: string | null;
}

export interface DailyReport {
  date: string;
  executions: { total: number; by_state: Record<string, number> };
  success_rate: number | null;
  failure_classes: Record<string, number>;
  duration_s: { p50: number; p95: number };
  queue_wait_s: { p50: number; p95: number; p99: number };
  devices: Record<string, number>;
}

export interface EnrolmentToken {
  token: string;
  host_name: string;
  expires_at: string;
  command: string;
}
