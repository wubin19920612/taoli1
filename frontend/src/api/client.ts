import type {
  AlertEvent,
  AlertRule,
  HealthStatus,
  Opportunity,
  OpportunityFilters,
  RiskSettings
} from "./types";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api";

function buildUrl(path: string, params?: object) {
  const url = new URL(`${API_BASE}${path}`, window.location.origin);
  Object.entries((params ?? {}) as Record<string, string | number | boolean | undefined>).forEach(([key, value]) => {
    if (value !== undefined && value !== "") {
      url.searchParams.set(key, String(value));
    }
  });
  return url.toString();
}

function authHeaders(): HeadersInit {
  const password = window.localStorage.getItem("dashboard_password") ?? "";
  return password ? { "X-Dashboard-Password": password } : {};
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
    throw new Error(text || `Request failed: ${response.status}`);
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

export function saveDashboardPassword(password: string): void {
  if (password) {
    window.localStorage.setItem("dashboard_password", password);
  } else {
    window.localStorage.removeItem("dashboard_password");
  }
}
