import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import type {
  Alert,
  AlertRule,
  ApiKey,
  ApiKeyCreated,
  AuditEntry,
  DailyReport,
  Device,
  EnrolmentToken,
  Execution,
  ExecutionDetail,
  FleetSummary,
  HealthSample,
  Host,
  Job,
  JobDetail,
  LogLine,
  Page,
  Schedule,
  Template,
  TemplateReport,
  Webhook,
} from "./types";

function qs(params: Record<string, string | number | boolean | undefined | null>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

const LIVE = { refetchInterval: 15_000 } as const;

export const useFleetSummary = () =>
  useQuery({ queryKey: ["fleet"], queryFn: () => api<FleetSummary>("/v1/fleet/summary"), ...LIVE });

export const useHosts = () =>
  useQuery({ queryKey: ["hosts"], queryFn: () => api<Host[]>("/v1/hosts"), ...LIVE });

export const useDevices = (filters: { state?: string; site?: string; label?: string } = {}) =>
  useQuery({
    queryKey: ["devices", filters],
    queryFn: () => api<Page<Device>>(`/v1/devices${qs({ ...filters, limit: 200 })}`),
    placeholderData: keepPreviousData,
    ...LIVE,
  });

export const useDevice = (id: string) =>
  useQuery({
    queryKey: ["device", id],
    queryFn: () => api<Device>(`/v1/devices/${id}`),
    enabled: id !== "",
    ...LIVE,
  });

export const useDeviceTimeline = (id: string, hours = 24) =>
  useQuery({
    queryKey: ["device", id, "timeline", hours],
    queryFn: () => api<HealthSample[]>(`/v1/devices/${id}/timeline?hours=${hours}`),
  });

export const useJobs = (filters: { state?: string; template?: string; limit?: number } = {}) =>
  useQuery({
    queryKey: ["jobs", filters],
    queryFn: () => api<Page<Job>>(`/v1/jobs${qs({ limit: 50, ...filters })}`),
    placeholderData: keepPreviousData,
    ...LIVE,
  });

export const useJob = (id: string) =>
  useQuery({ queryKey: ["job", id], queryFn: () => api<JobDetail>(`/v1/jobs/${id}`), ...LIVE });

export const useExecutions = (filters: { device_id?: string; job_id?: string; state?: string }) =>
  useQuery({
    queryKey: ["executions", filters],
    queryFn: () => api<Page<Execution>>(`/v1/executions${qs({ limit: 30, ...filters })}`),
    ...LIVE,
  });

export const useExecution = (id: string) =>
  useQuery({
    queryKey: ["execution", id],
    queryFn: () => api<ExecutionDetail>(`/v1/executions/${id}`),
    refetchInterval: (query) => {
      const state = query.state.data?.state;
      return state === "pending" || state === "running" ? 3000 : false;
    },
  });

export const useExecutionLogs = (id: string) =>
  useQuery({
    queryKey: ["execution", id, "logs"],
    queryFn: () => api<LogLine[]>(`/v1/executions/${id}/logs?limit=2000`),
  });

export const useTemplates = () =>
  useQuery({ queryKey: ["templates"], queryFn: () => api<Template[]>("/v1/templates") });

export const useSchedules = () =>
  useQuery({ queryKey: ["schedules"], queryFn: () => api<Schedule[]>("/v1/schedules"), ...LIVE });

export const useAlerts = (state?: string) =>
  useQuery({
    queryKey: ["alerts", state],
    queryFn: () => api<Page<Alert>>(`/v1/alerts${qs({ state, limit: 100 })}`),
    ...LIVE,
  });

export const useAlertRules = () =>
  useQuery({ queryKey: ["alert-rules"], queryFn: () => api<AlertRule[]>("/v1/alert-rules") });

export const useWebhooks = () =>
  useQuery({ queryKey: ["webhooks"], queryFn: () => api<Webhook[]>("/v1/webhooks") });

export const useApiKeys = () =>
  useQuery({ queryKey: ["api-keys"], queryFn: () => api<ApiKey[]>("/v1/api-keys") });

export const useAudit = (action?: string) =>
  useQuery({
    queryKey: ["audit", action],
    queryFn: () => api<Page<AuditEntry>>(`/v1/audit${qs({ action, limit: 100 })}`),
    placeholderData: keepPreviousData,
  });

export const useDailyReport = () =>
  useQuery({ queryKey: ["report", "daily"], queryFn: () => api<DailyReport>("/v1/reports/daily") });

/** A mutation that refreshes the listed query families on success. */
function useAction<TIn, TOut>(run: (input: TIn) => Promise<TOut>, invalidate: string[]) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: run,
    onSuccess: async () => {
      await Promise.all(invalidate.map((key) => client.invalidateQueries({ queryKey: [key] })));
    },
  });
}

export const useSubmitJob = () =>
  useAction(
    (body: {
      template: string;
      priority: number;
      params: Record<string, unknown>;
      requirement?: Record<string, unknown>;
    }) =>
      api<Job>("/v1/jobs", {
        method: "POST",
        body,
        idempotencyKey: crypto.randomUUID(),
      }),
    ["jobs", "fleet"],
  );

export const useCancelJob = () =>
  useAction(
    (id: string) => api<Job>(`/v1/jobs/${id}/cancel`, { method: "POST" }),
    ["jobs", "job", "fleet"],
  );

export const useDeviceAction = () =>
  useAction(
    (input: {
      id: string;
      action: "quarantine" | "return-to-service" | "maintenance";
      enabled?: boolean;
    }) =>
      api<Device>(`/v1/devices/${input.id}/${input.action}`, {
        method: "POST",
        body:
          input.action === "maintenance"
            ? { enabled: input.enabled ?? true }
            : input.action === "quarantine"
              ? { reason: "operator action from dashboard" }
              : undefined,
      }),
    ["devices", "device", "fleet"],
  );

export const useSaveLabels = () =>
  useAction(
    (input: { id: string; labels: string[] }) =>
      api<Device>(`/v1/devices/${input.id}/labels`, {
        method: "PUT",
        body: { labels: input.labels },
      }),
    ["devices", "device"],
  );

export const useValidateTemplate = () =>
  useMutation({
    mutationFn: (content: string) =>
      api<TemplateReport>("/v1/templates/validate", { method: "POST", body: { content } }),
  });

export const useSaveTemplate = () =>
  useAction(
    (input: { name: string | null; content: string }) =>
      input.name
        ? api<Template>(`/v1/templates/${input.name}`, {
            method: "PUT",
            body: { content: input.content },
          })
        : api<Template>("/v1/templates", { method: "POST", body: { content: input.content } }),
    ["templates"],
  );

export const useScheduleAction = () =>
  useAction(
    (input: { id: string; action: "enable" | "disable" | "delete" }) =>
      input.action === "delete"
        ? api<null>(`/v1/schedules/${input.id}`, { method: "DELETE" })
        : api<Schedule>(`/v1/schedules/${input.id}/${input.action}`, { method: "POST" }),
    ["schedules"],
  );

export const useCreateSchedule = () =>
  useAction(
    (body: {
      name: string;
      template: string;
      cron: string;
      timezone: string;
      misfire_policy: "skip" | "run_once";
      priority: number;
      params: Record<string, unknown>;
    }) => api<Schedule>("/v1/schedules", { method: "POST", body }),
    ["schedules"],
  );

export const useAcknowledgeAlert = () =>
  useAction(
    (id: string) => api<Alert>(`/v1/alerts/${id}/acknowledge`, { method: "POST" }),
    ["alerts"],
  );

export const useToggleRule = () =>
  useAction(
    (input: { id: string; enabled: boolean }) =>
      api<AlertRule>(`/v1/alert-rules/${input.id}`, {
        method: "PATCH",
        body: { enabled: input.enabled },
      }),
    ["alert-rules"],
  );

export const useCreateApiKey = () =>
  useAction(
    (body: { name: string; role: "admin" | "operator" | "viewer" }) =>
      api<ApiKeyCreated>("/v1/api-keys", { method: "POST", body }),
    ["api-keys", "audit"],
  );

export const useRevokeApiKey = () =>
  useAction(
    (id: string) => api<null>(`/v1/api-keys/${id}`, { method: "DELETE" }),
    ["api-keys", "audit"],
  );

export const useCreateEnrolment = () =>
  useAction(
    (body: { host_name: string; site: string; region: string; max_concurrency: number }) =>
      api<EnrolmentToken>("/v1/enrolment-tokens", { method: "POST", body }),
    ["audit"],
  );
