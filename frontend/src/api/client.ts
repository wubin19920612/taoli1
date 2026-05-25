import type {
  AlertEvent,
  AlertMessageTemplateSettings,
  AlertRule,
  AstroActionResult,
  AstroCardCreateRequest,
  AstroCardSettings,
  AstroPairPlan,
  AstroSdkStatus,
  HealthStatus,
  LivePilotPreview,
  LivePilotSettings,
  Opportunity,
  OpportunityFilters,
  RiskSettings,
  ServiceControlStatus,
  ServiceRestartResult
} from "./types";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api";

function buildUrl(path: string, params?: object) {
  const url = new URL(`${API_BASE}${path}`, window.location.origin);
  Object.entries((params ?? {}) as Record<string, string | number | boolean | string[] | undefined>).forEach(([key, value]) => {
    if (value !== undefined && value !== "") {
      if (Array.isArray(value)) {
        if (value.length > 0) {
          url.searchParams.set(key, value.join(","));
        }
      } else {
        url.searchParams.set(key, String(value));
      }
    }
  });
  return url.toString();
}

function authHeaders(): HeadersInit {
  const password = window.localStorage.getItem("dashboard_password") ?? "";
  return password ? { "X-Dashboard-Password": password } : {};
}

function extractErrorMessage(text: string, status: number): string {
  if (!text) {
    return `Request failed: ${status}`;
  }
  try {
    const parsed = JSON.parse(text) as { detail?: unknown };
    if (parsed && typeof parsed.detail === "string" && parsed.detail.trim()) {
      return parsed.detail;
    }
  } catch {
    // Fall through to raw text.
  }
  return text;
}

async function fetchJson<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(buildUrl(path), {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(),
      ...options.headers
    }
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(extractErrorMessage(text, response.status));
  }
  return response.json() as Promise<T>;
}

export function listOpportunities(filters: OpportunityFilters): Promise<Opportunity[]> {
  const url = buildUrl("/opportunities", filters);
  return fetch(url, { headers: authHeaders() }).then(async (response) => {
    if (!response.ok) {
      throw new Error(await response.text());
    }
    return response.json() as Promise<Opportunity[]>;
  });
}

export async function getHealth(): Promise<HealthStatus> {
  return fetchJson<HealthStatus>("/health");
}

export async function getRiskSettings(): Promise<RiskSettings> {
  return fetchJson<RiskSettings>("/settings/risk");
}

export async function updateRiskSettings(settings: RiskSettings): Promise<RiskSettings> {
  return fetchJson<RiskSettings>("/settings/risk", {
    method: "PUT",
    body: JSON.stringify(settings)
  });
}

export async function getAlertMessageTemplate(): Promise<AlertMessageTemplateSettings> {
  return fetchJson<AlertMessageTemplateSettings>("/settings/alert-message-template");
}

export async function updateAlertMessageTemplate(
  settings: AlertMessageTemplateSettings
): Promise<AlertMessageTemplateSettings> {
  return fetchJson<AlertMessageTemplateSettings>("/settings/alert-message-template", {
    method: "PUT",
    body: JSON.stringify(settings)
  });
}

export async function getAstroCardSettings(): Promise<AstroCardSettings> {
  return fetchJson<AstroCardSettings>("/settings/astro-card");
}

export async function updateAstroCardSettings(settings: AstroCardSettings): Promise<AstroCardSettings> {
  return fetchJson<AstroCardSettings>("/settings/astro-card", {
    method: "PUT",
    body: JSON.stringify(settings)
  });
}

export async function getLivePilotSettings(): Promise<LivePilotSettings> {
  return fetchJson<LivePilotSettings>("/settings/live-pilot");
}

export async function getLivePilotPreview(): Promise<LivePilotPreview> {
  return fetchJson<LivePilotPreview>("/settings/live-pilot/preview");
}

export async function updateLivePilotSettings(settings: LivePilotSettings): Promise<LivePilotSettings> {
  return fetchJson<LivePilotSettings>("/settings/live-pilot", {
    method: "PUT",
    body: JSON.stringify(settings)
  });
}

export async function listAlertRules(): Promise<AlertRule[]> {
  return fetchJson<AlertRule[]>("/alerts/rules");
}

export async function createAlertRule(rule: AlertRule): Promise<AlertRule> {
  return fetchJson<AlertRule>("/alerts/rules", {
    method: "POST",
    body: JSON.stringify(rule)
  });
}

export async function updateAlertRule(id: string, rule: AlertRule): Promise<AlertRule> {
  return fetchJson<AlertRule>(`/alerts/rules/${id}`, {
    method: "PUT",
    body: JSON.stringify(rule)
  });
}

export async function deleteAlertRule(id: string): Promise<void> {
  await fetchJson(`/alerts/rules/${id}`, { method: "DELETE" });
}

export async function listAlertEvents(limit = 100): Promise<AlertEvent[]> {
  return fetchJson<AlertEvent[]>(`/alerts/events?limit=${limit}`);
}

export async function createTestAlertEvent(): Promise<AlertEvent> {
  return fetchJson<AlertEvent>("/alerts/test", {
    method: "POST",
    body: JSON.stringify({})
  });
}

export async function getServiceControlStatus(): Promise<ServiceControlStatus> {
  return fetchJson<ServiceControlStatus>("/admin/service-control");
}

export async function restartServiceControl(service: "backend" | "frontend"): Promise<ServiceRestartResult> {
  return fetchJson<ServiceRestartResult>(`/admin/service-control/${service}/restart`, {
    method: "POST",
    body: JSON.stringify({})
  });
}

export async function getAstroStatus(): Promise<AstroSdkStatus> {
  return fetchJson<AstroSdkStatus>("/astro/status");
}

export async function previewAstroPair(opportunityId: string): Promise<AstroPairPlan> {
  return fetchJson<AstroPairPlan>(`/astro/preview/${opportunityId}`);
}

export async function createAstroCard(
  opportunityId: string,
  request: AstroCardCreateRequest = {}
): Promise<AstroActionResult> {
  return fetchJson<AstroActionResult>(`/astro/opportunities/${opportunityId}/card`, {
    method: "POST",
    body: JSON.stringify(request)
  });
}

export async function listAstroPairs(): Promise<Record<string, unknown>[]> {
  return fetchJson<Record<string, unknown>[]>("/astro/pairs");
}

export function saveDashboardPassword(password: string): void {
  if (password) {
    window.localStorage.setItem("dashboard_password", password);
  } else {
    window.localStorage.removeItem("dashboard_password");
  }
}
