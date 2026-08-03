import type {
  AlertEvent,
  AlertMessageTemplateSettings,
  AlertRule,
  AnnouncementExchangeOption,
  AnnouncementFilters,
  AnnouncementSettings,
  AstroAutomationSettings,
  AstroActionResult,
  AstroCardCreateRequest,
  AstroCardSettings,
  AstroPairPlan,
  AstroSdkStatus,
  FundingArbitragePreview,
  FundingArbitrageSettings,
  FundingResearchCandidate,
  FundingResearchCandidateSnapshot,
  FundingResearchLegacyBacktestQuery,
  FundingResearchLegacyBacktestSummary,
  FundingResearchPaperTrade,
  FundingResearchPaperTradeSummary,
  FundingResearchRunResult,
  GateTwapJobStatus,
  GateTwapMarketSnapshot,
  GateTwapPlan,
  GateTwapRequest,
  GateTwapRunRequest,
  HealthStatus,
  ExchangeAnnouncement,
  IndexComponentChange,
  IndexComponentChangeFilters,
  IndexComponentSnapshot,
  IndexComponentSnapshotFilters,
  IndexComponentWatchItem,
  OpportunityHistoryStats,
  OpportunityHistoryStatsQuery,
  OpportunityRadarPreview,
  OpportunityRadarSettings,
  PairSpreadDiagnosticResult,
  PairSpreadFundingHistoryResult,
  PairSpreadQueryResult,
  PairSpreadFundingRecordRequest,
  PairSpreadFundingRecordStatus,
  LivePilotPreview,
  MarketType,
  LivePilotSettings,
  MarketFilters,
  MarketSnapshot,
  Opportunity,
  OpportunityFilters,
  PhonePriceAlertDiagnostics,
  PhonePriceAlertEvent,
  PhonePriceAlertRule,
  PremiumIndexCurrentSnapshot,
  PremiumIndexQueryResult,
  MinuteSignalScanResult,
  MinuteSignalSettings,
  MinuteSignalUniverseScanResult,
  RiskSettings,
  SecondLevelIndexComponentSample,
  SecondLevelMarketSample,
  SecondLevelSamplingConfig,
  SecondLevelSamplingStatus,
  ServiceControlStatus,
  ServiceRestartResult,
  SymbolSpreadQueryResult,
  TradfiPerpMonitorPreview
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

export function listMarkets(filters: MarketFilters = {}): Promise<MarketSnapshot[]> {
  const url = buildUrl("/markets", filters);
  return fetch(url, { headers: authHeaders() }).then(async (response) => {
    if (!response.ok) {
      throw new Error(await response.text());
    }
    return response.json() as Promise<MarketSnapshot[]>;
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

export async function getAstroAutomationSettings(): Promise<AstroAutomationSettings> {
  return fetchJson<AstroAutomationSettings>("/settings/astro-automation");
}

export async function updateAstroAutomationSettings(
  settings: AstroAutomationSettings
): Promise<AstroAutomationSettings> {
  return fetchJson<AstroAutomationSettings>("/settings/astro-automation", {
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

export async function getMinuteSignalSettings(): Promise<MinuteSignalSettings> {
  return fetchJson<MinuteSignalSettings>("/settings/minute-signals");
}

export async function updateMinuteSignalSettings(settings: MinuteSignalSettings): Promise<MinuteSignalSettings> {
  return fetchJson<MinuteSignalSettings>("/settings/minute-signals", {
    method: "PUT",
    body: JSON.stringify(settings)
  });
}

export async function getAnnouncementSettings(): Promise<AnnouncementSettings> {
  return fetchJson<AnnouncementSettings>("/settings/announcements");
}

export async function updateAnnouncementSettings(settings: AnnouncementSettings): Promise<AnnouncementSettings> {
  return fetchJson<AnnouncementSettings>("/settings/announcements", {
    method: "PUT",
    body: JSON.stringify(settings)
  });
}

export async function listAnnouncements(filters: AnnouncementFilters = {}): Promise<ExchangeAnnouncement[]> {
  const url = buildUrl("/announcements", { limit: 100, ...filters });
  return fetch(url, { headers: authHeaders() }).then(async (response) => {
    if (!response.ok) {
      throw new Error(await response.text());
    }
    return response.json() as Promise<ExchangeAnnouncement[]>;
  });
}

export async function listAnnouncementExchanges(): Promise<AnnouncementExchangeOption[]> {
  return fetchJson<AnnouncementExchangeOption[]>("/announcements/exchanges");
}

export async function getFundingArbitrageSettings(): Promise<FundingArbitrageSettings> {
  return fetchJson<FundingArbitrageSettings>("/funding-arbitrage/settings");
}

export async function updateFundingArbitrageSettings(
  settings: FundingArbitrageSettings
): Promise<FundingArbitrageSettings> {
  return fetchJson<FundingArbitrageSettings>("/funding-arbitrage/settings", {
    method: "PUT",
    body: JSON.stringify(settings)
  });
}

export async function getFundingArbitragePreview(): Promise<FundingArbitragePreview> {
  return fetchJson<FundingArbitragePreview>("/funding-arbitrage/preview");
}

export async function getOpportunityRadarSettings(): Promise<OpportunityRadarSettings> {
  return fetchJson<OpportunityRadarSettings>("/opportunity-radar/settings");
}

export async function updateOpportunityRadarSettings(
  settings: OpportunityRadarSettings
): Promise<OpportunityRadarSettings> {
  return fetchJson<OpportunityRadarSettings>("/opportunity-radar/settings", {
    method: "PUT",
    body: JSON.stringify(settings)
  });
}

export async function getOpportunityRadarPreview(): Promise<OpportunityRadarPreview> {
  return fetchJson<OpportunityRadarPreview>("/opportunity-radar/preview");
}

export async function testOpportunityRadarNotification(): Promise<{ status: string }> {
  return fetchJson<{ status: string }>("/opportunity-radar/test-notification", {
    method: "POST",
    body: JSON.stringify({})
  });
}

export async function getTradfiPerpMonitorPreview(params: {
  live?: boolean;
  min_volume_24h_k?: number;
  max_mark_index_deviation_pct?: number;
  max_rows?: number;
} = {}): Promise<TradfiPerpMonitorPreview> {
  const url = buildUrl("/tradfi-perp-monitor/preview", params);
  return fetch(url, { headers: authHeaders() }).then(async (response) => {
    if (!response.ok) {
      throw new Error(await response.text());
    }
    return response.json() as Promise<TradfiPerpMonitorPreview>;
  });
}

export async function refreshTradfiPerpMonitorPreview(params: {
  min_volume_24h_k?: number;
  max_mark_index_deviation_pct?: number;
  max_rows?: number;
} = {}): Promise<TradfiPerpMonitorPreview> {
  const url = buildUrl("/tradfi-perp-monitor/refresh", params);
  return fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...authHeaders()
    },
    body: JSON.stringify({})
  }).then(async (response) => {
    if (!response.ok) {
      throw new Error(await response.text());
    }
    return response.json() as Promise<TradfiPerpMonitorPreview>;
  });
}

export async function runFundingResearch(params: {
  manage_paper_trades?: boolean;
  snapshot_retention_hours?: number;
} = {}): Promise<FundingResearchRunResult> {
  const url = buildUrl("/funding-research/run", params);
  return fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...authHeaders()
    },
    body: JSON.stringify({})
  }).then(async (response) => {
    if (!response.ok) {
      throw new Error(await response.text());
    }
    return response.json() as Promise<FundingResearchRunResult>;
  });
}

export async function listFundingResearchCandidates(params: {
  symbol?: string;
  opportunity_type?: string;
  limit?: number;
} = {}): Promise<FundingResearchCandidate[]> {
  const url = buildUrl("/funding-research/candidates", params);
  return fetch(url, { headers: authHeaders() }).then(async (response) => {
    if (!response.ok) {
      throw new Error(await response.text());
    }
    return response.json() as Promise<FundingResearchCandidate[]>;
  });
}

export async function listFundingResearchCandidateSnapshots(params: {
  symbol?: string;
  long_exchange?: string;
  short_exchange?: string;
  limit?: number;
} = {}): Promise<FundingResearchCandidateSnapshot[]> {
  const url = buildUrl("/funding-research/candidate-snapshots", params);
  return fetch(url, { headers: authHeaders() }).then(async (response) => {
    if (!response.ok) {
      throw new Error(await response.text());
    }
    return response.json() as Promise<FundingResearchCandidateSnapshot[]>;
  });
}

export async function listFundingResearchPaperTrades(params: {
  status?: string;
  opportunity_type?: string;
  limit?: number;
} = {}): Promise<FundingResearchPaperTrade[]> {
  const url = buildUrl("/funding-research/paper-trades", params);
  return fetch(url, { headers: authHeaders() }).then(async (response) => {
    if (!response.ok) {
      throw new Error(await response.text());
    }
    return response.json() as Promise<FundingResearchPaperTrade[]>;
  });
}

export async function openFundingResearchPaperTrade(
  candidateId: string
): Promise<FundingResearchPaperTrade> {
  return fetchJson<FundingResearchPaperTrade>(`/funding-research/paper-trades/open/${candidateId}`, {
    method: "POST",
    body: JSON.stringify({})
  });
}

export async function closeFundingResearchPaperTrade(
  tradeId: string,
  exitReason = "manual"
): Promise<FundingResearchPaperTrade> {
  const url = buildUrl(`/funding-research/paper-trades/${tradeId}/close`, {
    exit_reason: exitReason
  });
  return fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...authHeaders()
    },
    body: JSON.stringify({})
  }).then(async (response) => {
    if (!response.ok) {
      throw new Error(await response.text());
    }
    return response.json() as Promise<FundingResearchPaperTrade>;
  });
}

export async function getFundingResearchPaperTradeSummary(
  limit = 1000,
  opportunityType?: string
): Promise<FundingResearchPaperTradeSummary> {
  const url = buildUrl("/funding-research/paper-trades/summary", {
    limit,
    opportunity_type: opportunityType
  });
  return fetch(url, { headers: authHeaders() }).then(async (response) => {
    if (!response.ok) {
      throw new Error(await response.text());
    }
    return response.json() as Promise<FundingResearchPaperTradeSummary>;
  });
}

export async function getFundingResearchLegacyBacktest(
  query: FundingResearchLegacyBacktestQuery = {}
): Promise<FundingResearchLegacyBacktestSummary> {
  const url = buildUrl("/funding-research/legacy-backtest", query);
  return fetch(url, { headers: authHeaders() }).then(async (response) => {
    if (!response.ok) {
      throw new Error(await response.text());
    }
    return response.json() as Promise<FundingResearchLegacyBacktestSummary>;
  });
}

export async function getGateTwapMarket(params: {
  contract?: string;
  settle?: string;
} = {}): Promise<GateTwapMarketSnapshot> {
  const url = buildUrl("/gate-twap/market", params);
  return fetch(url, { headers: authHeaders() }).then(async (response) => {
    if (!response.ok) {
      throw new Error(await response.text());
    }
    return response.json() as Promise<GateTwapMarketSnapshot>;
  });
}

export async function previewGateTwap(request: GateTwapRequest): Promise<GateTwapPlan> {
  return fetchJson<GateTwapPlan>("/gate-twap/preview", {
    method: "POST",
    body: JSON.stringify(request)
  });
}

export async function startGateTwapJob(request: GateTwapRunRequest): Promise<GateTwapJobStatus> {
  return fetchJson<GateTwapJobStatus>("/gate-twap/jobs", {
    method: "POST",
    body: JSON.stringify(request)
  });
}

export async function listGateTwapJobs(): Promise<GateTwapJobStatus[]> {
  return fetchJson<GateTwapJobStatus[]>("/gate-twap/jobs");
}

export async function getGateTwapJob(jobId: string): Promise<GateTwapJobStatus> {
  return fetchJson<GateTwapJobStatus>(`/gate-twap/jobs/${jobId}`);
}

export async function cancelGateTwapJob(jobId: string): Promise<GateTwapJobStatus> {
  return fetchJson<GateTwapJobStatus>(`/gate-twap/jobs/${jobId}`, {
    method: "DELETE"
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

export async function listPhonePriceAlertRules(): Promise<PhonePriceAlertRule[]> {
  return fetchJson<PhonePriceAlertRule[]>("/phone-alerts/rules");
}

export async function createPhonePriceAlertRule(
  rule: PhonePriceAlertRule
): Promise<PhonePriceAlertRule> {
  return fetchJson<PhonePriceAlertRule>("/phone-alerts/rules", {
    method: "POST",
    body: JSON.stringify(rule)
  });
}

export async function updatePhonePriceAlertRule(
  id: string,
  rule: PhonePriceAlertRule
): Promise<PhonePriceAlertRule> {
  return fetchJson<PhonePriceAlertRule>(`/phone-alerts/rules/${id}`, {
    method: "PUT",
    body: JSON.stringify(rule)
  });
}

export async function deletePhonePriceAlertRule(id: string): Promise<void> {
  await fetchJson(`/phone-alerts/rules/${id}`, { method: "DELETE" });
}

export async function listPhonePriceAlertEvents(limit = 100): Promise<PhonePriceAlertEvent[]> {
  return fetchJson<PhonePriceAlertEvent[]>(`/phone-alerts/events?limit=${limit}`);
}

export async function getPhonePriceAlertDiagnostics(): Promise<PhonePriceAlertDiagnostics> {
  return fetchJson<PhonePriceAlertDiagnostics>("/phone-alerts/diagnostics");
}

export async function listIndexComponentChanges(
  filters: IndexComponentChangeFilters = {}
): Promise<IndexComponentChange[]> {
  const url = buildUrl("/index-components/changes", { limit: 100, ...filters });
  return fetch(url, { headers: authHeaders() }).then(async (response) => {
    if (!response.ok) {
      throw new Error(await response.text());
    }
    return response.json() as Promise<IndexComponentChange[]>;
  });
}

export async function listIndexComponentSnapshots(
  filters: IndexComponentSnapshotFilters = {}
): Promise<IndexComponentSnapshot[]> {
  const url = buildUrl("/index-components/snapshots", { limit: 500, ...filters });
  return fetch(url, { headers: authHeaders() }).then(async (response) => {
    if (!response.ok) {
      throw new Error(await response.text());
    }
    return response.json() as Promise<IndexComponentSnapshot[]>;
  });
}

export async function listIndexComponentWatchlist(): Promise<IndexComponentWatchItem[]> {
  return fetchJson<IndexComponentWatchItem[]>("/index-components/watchlist");
}

export async function createIndexComponentWatchItem(
  item: Pick<IndexComponentWatchItem, "symbol" | "note">
): Promise<IndexComponentWatchItem> {
  return fetchJson<IndexComponentWatchItem>("/index-components/watchlist", {
    method: "POST",
    body: JSON.stringify(item)
  });
}

export async function deleteIndexComponentWatchItem(id: string): Promise<void> {
  await fetchJson(`/index-components/watchlist/${id}`, { method: "DELETE" });
}

export async function getOpportunityHistoryStats(
  query: OpportunityHistoryStatsQuery
): Promise<OpportunityHistoryStats> {
  const url = buildUrl("/history/opportunities/stats", query);
  return fetch(url, { headers: authHeaders() }).then(async (response) => {
    if (!response.ok) {
      throw new Error(await response.text());
    }
    return response.json() as Promise<OpportunityHistoryStats>;
  });
}

export async function queryPairSpread(query: {
  leg1_exchange: string;
  leg1_symbol: string;
  leg1_market_type?: MarketType;
  leg2_exchange: string;
  leg2_symbol: string;
  leg2_market_type?: MarketType;
  hours?: number;
  interval_minutes?: number;
  interval_seconds?: number;
  leg2_multiplier?: number;
  end_at?: string;
  include_current?: boolean;
}): Promise<PairSpreadQueryResult> {
  const url = buildUrl("/pair-spread/query", query);
  return fetch(url, { headers: authHeaders() }).then(async (response) => {
    if (!response.ok) {
      const text = await response.text();
      throw new Error(extractErrorMessage(text, response.status));
    }
    return response.json() as Promise<PairSpreadQueryResult>;
  });
}

export async function querySymbolExchangeSpreads(query: {
  symbol: string;
  market_type?: MarketType;
  base_exchange?: string;
  exchanges?: string[];
  hours?: number;
  interval_seconds?: number;
  end_at?: string;
  include_current?: boolean;
}): Promise<SymbolSpreadQueryResult> {
  const url = buildUrl("/pair-spread/symbol-query", query);
  return fetch(url, { headers: authHeaders() }).then(async (response) => {
    if (!response.ok) {
      const text = await response.text();
      throw new Error(extractErrorMessage(text, response.status));
    }
    return response.json() as Promise<SymbolSpreadQueryResult>;
  });
}

export async function queryPairSpreadDiagnostics(query: {
  leg1_exchange: string;
  leg1_symbol: string;
  leg1_market_type?: MarketType;
  leg2_exchange: string;
  leg2_symbol: string;
  leg2_market_type?: MarketType;
  hours?: number;
  threshold_pct?: number;
  interval_seconds?: number;
  leg2_multiplier?: number;
  end_at?: string;
}): Promise<PairSpreadDiagnosticResult> {
  const url = buildUrl("/pair-spread/diagnostics", query);
  return fetch(url, { headers: authHeaders() }).then(async (response) => {
    if (!response.ok) {
      const text = await response.text();
      throw new Error(extractErrorMessage(text, response.status));
    }
    return response.json() as Promise<PairSpreadDiagnosticResult>;
  });
}

export async function queryPairSpreadFundingHistory(query: {
  leg1_exchange: string;
  leg1_symbol: string;
  leg1_market_type?: MarketType;
  leg2_exchange: string;
  leg2_symbol: string;
  leg2_market_type?: MarketType;
  hours?: number;
  leg2_multiplier?: number;
  start_at?: string;
  end_at?: string;
}): Promise<PairSpreadFundingHistoryResult> {
  const url = buildUrl("/pair-spread/funding-history", query);
  return fetch(url, { headers: authHeaders() }).then(async (response) => {
    if (!response.ok) {
      const text = await response.text();
      throw new Error(extractErrorMessage(text, response.status));
    }
    return response.json() as Promise<PairSpreadFundingHistoryResult>;
  });
}

export async function getPairSpreadFundingRecordStatus(query: {
  leg1_exchange: string;
  leg1_symbol: string;
  leg1_market_type?: MarketType;
  leg2_exchange: string;
  leg2_symbol: string;
  leg2_market_type?: MarketType;
  hours?: number;
  leg2_multiplier?: number;
  end_at?: string;
}): Promise<PairSpreadFundingRecordStatus> {
  const url = buildUrl("/pair-spread/funding-records/status", query);
  return fetch(url, { headers: authHeaders() }).then(async (response) => {
    if (!response.ok) {
      const text = await response.text();
      throw new Error(extractErrorMessage(text, response.status));
    }
    return response.json() as Promise<PairSpreadFundingRecordStatus>;
  });
}

export async function startPairSpreadFundingRecord(
  request: PairSpreadFundingRecordRequest,
  hours = 72
): Promise<PairSpreadFundingRecordStatus> {
  return fetchJson<PairSpreadFundingRecordStatus>(
    `/pair-spread/funding-records/watch?hours=${hours}`,
    {
      method: "POST",
      body: JSON.stringify(request)
    }
  );
}

export async function stopPairSpreadFundingRecord(
  request: PairSpreadFundingRecordRequest,
  hours = 72
): Promise<PairSpreadFundingRecordStatus> {
  return fetchJson<PairSpreadFundingRecordStatus>(
    `/pair-spread/funding-records/watch?hours=${hours}`,
    {
      method: "DELETE",
      body: JSON.stringify(request)
    }
  );
}

export async function queryPremiumIndex(query: {
  exchange: string;
  symbol: string;
  hours?: number;
  interval_minutes?: number;
}): Promise<PremiumIndexQueryResult> {
  const url = buildUrl("/premium-index/query", query);
  return fetch(url, { headers: authHeaders() }).then(async (response) => {
    if (!response.ok) {
      const text = await response.text();
      throw new Error(extractErrorMessage(text, response.status));
    }
    return response.json() as Promise<PremiumIndexQueryResult>;
  });
}

export async function getCurrentPremiumIndex(query: {
  exchange: string;
  symbol: string;
}): Promise<PremiumIndexCurrentSnapshot> {
  const url = buildUrl("/premium-index/current", query);
  return fetch(url, { headers: authHeaders() }).then(async (response) => {
    if (!response.ok) {
      const text = await response.text();
      throw new Error(extractErrorMessage(text, response.status));
    }
    return response.json() as Promise<PremiumIndexCurrentSnapshot>;
  });
}

export async function scanMinuteSignals(query: {
  symbol?: string;
  alpha_symbol?: string;
  hours?: number;
} = {}): Promise<MinuteSignalScanResult> {
  const url = buildUrl("/minute-signals/scan", query);
  return fetch(url, { headers: authHeaders() }).then(async (response) => {
    if (!response.ok) {
      const text = await response.text();
      throw new Error(extractErrorMessage(text, response.status));
    }
    return response.json() as Promise<MinuteSignalScanResult>;
  });
}

export async function scanMinuteSignalUniverse(query: {
  hours?: number;
  max_symbols?: number;
  min_volume_24h_usdt?: number;
  alert_cooldown_minutes?: number;
  max_entry_basis_bps?: number;
  require_negative_premium_when_spot_above?: boolean;
  max_premium_when_spot_above_bps?: number;
} = {}): Promise<MinuteSignalUniverseScanResult> {
  const url = buildUrl("/minute-signals/scan-all", query);
  return fetch(url, { headers: authHeaders() }).then(async (response) => {
    if (!response.ok) {
      const text = await response.text();
      throw new Error(extractErrorMessage(text, response.status));
    }
    return response.json() as Promise<MinuteSignalUniverseScanResult>;
  });
}

export async function listSecondLevelSamplingExchanges(): Promise<string[]> {
  return fetchJson<string[]>("/second-level-sampling/exchanges");
}

export async function getSecondLevelSamplingConfig(): Promise<SecondLevelSamplingConfig> {
  return fetchJson<SecondLevelSamplingConfig>("/second-level-sampling/config");
}

export async function updateSecondLevelSamplingConfig(
  config: SecondLevelSamplingConfig
): Promise<SecondLevelSamplingConfig> {
  return fetchJson<SecondLevelSamplingConfig>("/second-level-sampling/config", {
    method: "PUT",
    body: JSON.stringify(config)
  });
}

export async function startSecondLevelSampling(): Promise<SecondLevelSamplingStatus> {
  return fetchJson<SecondLevelSamplingStatus>("/second-level-sampling/start", {
    method: "POST",
    body: JSON.stringify({})
  });
}

export async function stopSecondLevelSampling(): Promise<SecondLevelSamplingStatus> {
  return fetchJson<SecondLevelSamplingStatus>("/second-level-sampling/stop", {
    method: "POST",
    body: JSON.stringify({})
  });
}

export async function getSecondLevelSamplingStatus(): Promise<SecondLevelSamplingStatus> {
  return fetchJson<SecondLevelSamplingStatus>("/second-level-sampling/status");
}

export async function listSecondLevelSamples(query: {
  exchange?: string;
  symbol?: string;
  minutes?: number;
  limit?: number;
} = {}): Promise<SecondLevelMarketSample[]> {
  const url = buildUrl("/second-level-sampling/samples", query);
  return fetch(url, { headers: authHeaders() }).then(async (response) => {
    if (!response.ok) {
      const text = await response.text();
      throw new Error(extractErrorMessage(text, response.status));
    }
    return response.json() as Promise<SecondLevelMarketSample[]>;
  });
}

export async function listSecondLevelIndexComponentSamples(query: {
  target_exchange?: string;
  symbol?: string;
  component_source?: string;
  minutes?: number;
  limit?: number;
} = {}): Promise<SecondLevelIndexComponentSample[]> {
  const url = buildUrl("/second-level-sampling/component-samples", query);
  return fetch(url, { headers: authHeaders() }).then(async (response) => {
    if (!response.ok) {
      const text = await response.text();
      throw new Error(extractErrorMessage(text, response.status));
    }
    return response.json() as Promise<SecondLevelIndexComponentSample[]>;
  });
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
