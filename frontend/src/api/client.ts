import type {
  AccountConnection,
  AccountConnectionOverview,
  AccountConnectionTestResult,
  AccountConnectionUpdate,
  AccountConnectionWrite,
  AccountPositionIdentity,
  AccountPositionSnapshot,
  AlertEvent,
  AlertMessageTemplateSettings,
  AlertRule,
  AnnouncementExchangeOption,
  AnnouncementFilters,
  AnnouncementSettings,
  AstroAutomationSettings,
  AstroActionResult,
  AstroCardCreateRequest,
  AstroInstrumentRouteRequest,
  AstroCardSettings,
  AstroPairStatus,
  AstroPairPlan,
  AstroPreaddPreview,
  AstroPreaddRunResult,
  AstroPreaddSettings,
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
  FloatingWatchSettings,
  FatFingerBacktestRequest,
  FatFingerBacktestResult,
  GateTwapJobStatus,
  GateTwapMarketSnapshot,
  GateTwapPlan,
  GateTwapRequest,
  GateTwapRunRequest,
  HealthStatus,
  HyperliquidDexMarket,
  HyperliquidTradeStatusResult,
  HyperliquidTradeStatusWatch,
  ExchangeAnnouncement,
  IndexComponentChange,
  IndexComponentAutoWatchStatus,
  IndexComponentChangeFilters,
  IndexComponentSnapshot,
  IndexComponentSnapshotFilters,
  IndexComponentWatchItem,
  InstrumentLookupResult,
  OpportunityHistoryStats,
  OpportunityHistoryStatsQuery,
  OpportunityRadarPreview,
  OpportunityRadarSettings,
  PairSpreadDiagnosticResult,
  PairSpreadFundingHistoryResult,
  PairSpreadQueryResult,
  PairSpreadPreset,
  PairSpreadFundingRecordRequest,
  PairSpreadFundingRecordStatus,
  LivePilotPreview,
  MarketType,
  LivePilotSettings,
  MarketFilters,
  MarketSnapshot,
  Opportunity,
  OpportunityFilters,
  OilNewsFilters,
  OilNewsItem,
  OilNewsRefreshResult,
  OilNewsSettings,
  PhonePriceAlertDiagnostics,
  PhonePriceAlertEvent,
  PhonePriceAlertRule,
  PremiumIndexCurrentSnapshot,
  PremiumIndexQueryResult,
  NegativeBasisAlertEvent,
  NegativeBasisAnalysisResult,
  NegativeBasisAutoCandidate,
  NegativeBasisAutoScanSettings,
  NegativeBasisMonitorStatus,
  NegativeBasisSignalSample,
  NegativeBasisWatchItem,
  RiskSettings,
  SecondLevelIndexComponentSample,
  SecondLevelMarketSample,
  SecondLevelSamplingConfig,
  SecondLevelSamplingStatus,
  ServiceControlStatus,
  ServiceRestartResult,
  SymbolSpreadQueryResult,
  TradeAvailabilityResult,
  TradeAvailabilityWatch,
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

const PROXY_ERROR_MESSAGES: Partial<Record<number, string>> = {
  502: "后端服务暂时不可用，可能正在重启，请稍后重试。",
  503: "服务正在启动或暂时不可用，请稍后重试。",
  504: "后端服务响应超时，请稍后重试。"
};

function looksLikeHtmlError(text: string): boolean {
  return /<(?:!doctype\s+)?html(?:\s|>)|<head(?:\s|>)|<body(?:\s|>)/i.test(text);
}

function extractErrorMessage(text: string, status: number): string {
  const normalized = text.trim();
  if (!normalized) {
    return PROXY_ERROR_MESSAGES[status] ?? `请求失败（HTTP ${status}）`;
  }
  try {
    const parsed = JSON.parse(normalized) as { detail?: unknown };
    if (parsed && typeof parsed.detail === "string" && parsed.detail.trim()) {
      return parsed.detail;
    }
  } catch {
    // Non-JSON responses are handled below.
  }
  const isStandardProxyText = /^(?:502\s+)?bad gateway$/i.test(normalized)
    || /^(?:503\s+)?service unavailable$/i.test(normalized)
    || /^(?:504\s+)?gateway time-?out$/i.test(normalized);
  if (looksLikeHtmlError(normalized) || isStandardProxyText) {
    return PROXY_ERROR_MESSAGES[status]
      ?? `服务返回了无法显示的错误页面（HTTP ${status}），请稍后重试。`;
  }
  return normalized;
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
      throw new Error(extractErrorMessage(await response.text(), response.status));
    }
    return response.json() as Promise<Opportunity[]>;
  });
}

export function listMarkets(filters: MarketFilters = {}): Promise<MarketSnapshot[]> {
  const url = buildUrl("/markets", filters);
  return fetch(url, { headers: authHeaders() }).then(async (response) => {
    if (!response.ok) {
      throw new Error(extractErrorMessage(await response.text(), response.status));
    }
    return response.json() as Promise<MarketSnapshot[]>;
  });
}

export function lookupInstrument(symbol: string, hyperliquidDex?: string): Promise<InstrumentLookupResult> {
  const params = new URLSearchParams();
  if (hyperliquidDex) params.set("dex", hyperliquidDex);
  const query = params.size ? `?${params.toString()}` : "";
  return fetchJson<InstrumentLookupResult>(`/instruments/${encodeURIComponent(symbol)}${query}`);
}

export async function getHyperliquidTradeStatus(
  symbol: string,
  dex?: string,
  rawSymbol?: string
): Promise<HyperliquidTradeStatusResult> {
  const params = new URLSearchParams();
  if (dex) params.set("dex", dex);
  if (rawSymbol) params.set("raw_symbol", rawSymbol);
  const query = params.size ? `?${params.toString()}` : "";
  const value = await fetchJson<unknown>(
    `/hyperliquid/trade-status/${encodeURIComponent(symbol)}${query}`
  );
  if (!value || typeof value !== "object" || !Array.isArray((value as HyperliquidTradeStatusResult).markets)) {
    throw new Error("Hyperliquid 交易状态响应格式无效");
  }
  return value as HyperliquidTradeStatusResult;
}

export async function listHyperliquidTradeStatusWatches(): Promise<HyperliquidTradeStatusWatch[]> {
  const value = await fetchJson<unknown>("/hyperliquid/trade-status/watches/list");
  if (!Array.isArray(value)) throw new Error("Hyperliquid 恢复监控响应格式无效");
  return value as HyperliquidTradeStatusWatch[];
}

export function createHyperliquidTradeStatusWatch(payload: {
  symbol: string;
  dex: string;
  raw_symbol: string;
  monitor_buy?: boolean;
  monitor_sell?: boolean;
}): Promise<HyperliquidTradeStatusWatch> {
  return fetchJson<HyperliquidTradeStatusWatch>("/hyperliquid/trade-status/watches", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export function deleteHyperliquidTradeStatusWatch(watchId: string): Promise<{ status: string }> {
  return fetchJson<{ status: string }>(
    `/hyperliquid/trade-status/watches/${encodeURIComponent(watchId)}`,
    { method: "DELETE" }
  );
}

export async function getTradeAvailability(symbol: string): Promise<TradeAvailabilityResult> {
  const value = await fetchJson<unknown>(`/trade-status/${encodeURIComponent(symbol)}`);
  if (!value || typeof value !== "object" || !Array.isArray((value as TradeAvailabilityResult).markets)) {
    throw new Error("全交易所交易可用性响应格式无效");
  }
  return value as TradeAvailabilityResult;
}

export async function listTradeAvailabilityWatches(): Promise<TradeAvailabilityWatch[]> {
  const value = await fetchJson<unknown>("/trade-status/watches/list");
  if (!Array.isArray(value)) throw new Error("交易可用性恢复监控响应格式无效");
  return value as TradeAvailabilityWatch[];
}

export function createTradeAvailabilityWatch(payload: {
  symbol: string;
  exchange: string;
  market_type: MarketType;
  raw_symbol: string;
  dex?: string | null;
  monitor_buy?: boolean;
  monitor_sell?: boolean;
}): Promise<TradeAvailabilityWatch> {
  return fetchJson<TradeAvailabilityWatch>("/trade-status/watches", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export function deleteTradeAvailabilityWatch(watchId: string): Promise<{ status: string }> {
  return fetchJson<{ status: string }>(
    `/trade-status/watches/${encodeURIComponent(watchId)}`,
    { method: "DELETE" }
  );
}

export async function getFloatingWatchSettings(): Promise<FloatingWatchSettings> {
  const value = await fetchJson<unknown>("/settings/floating-watch");
  if (!value || typeof value !== "object") {
    return { symbols: [], pair_ids: [], hidden_positions: [] };
  }
  const candidate = value as Partial<FloatingWatchSettings>;
  return {
    symbols: Array.isArray(candidate.symbols)
      ? candidate.symbols.filter((item): item is string => typeof item === "string")
      : [],
    pair_ids: Array.isArray(candidate.pair_ids)
      ? candidate.pair_ids.filter((item): item is string => typeof item === "string")
      : [],
    hidden_positions: Array.isArray(candidate.hidden_positions)
      ? candidate.hidden_positions.filter(
        (item): item is AccountPositionIdentity => Boolean(
          item
          && typeof item === "object"
          && typeof (item as AccountPositionIdentity).id === "string"
        )
      )
      : []
  };
}

export async function mutateFloatingWatchItem(
  action: "add" | "remove",
  itemType: "symbol" | "pair",
  value: string
): Promise<FloatingWatchSettings> {
  return fetchJson<FloatingWatchSettings>("/settings/floating-watch/items", {
    method: "POST",
    body: JSON.stringify({ action, item_type: itemType, value })
  });
}

export async function listAccountPositions(): Promise<AccountPositionSnapshot> {
  const value = await fetchJson<unknown>("/account-positions");
  if (
    !value
    || typeof value !== "object"
    || !Array.isArray((value as AccountPositionSnapshot).positions)
    || !Array.isArray((value as AccountPositionSnapshot).accounts)
  ) {
    throw new Error("账户持仓响应格式无效");
  }
  return value as AccountPositionSnapshot;
}

export function listAccountConnections(): Promise<AccountConnectionOverview> {
  return fetchJson<AccountConnectionOverview>("/account-connections");
}

export function createAccountConnection(
  payload: AccountConnectionWrite
): Promise<AccountConnection> {
  return fetchJson<AccountConnection>("/account-connections", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export function updateAccountConnection(
  connectionId: string,
  payload: AccountConnectionUpdate
): Promise<AccountConnection> {
  return fetchJson<AccountConnection>(`/account-connections/${encodeURIComponent(connectionId)}`, {
    method: "PUT",
    body: JSON.stringify(payload)
  });
}

export async function deleteAccountConnection(connectionId: string): Promise<void> {
  const response = await fetch(buildUrl(`/account-connections/${encodeURIComponent(connectionId)}`), {
    method: "DELETE",
    headers: authHeaders()
  });
  if (!response.ok) {
    throw new Error(extractErrorMessage(await response.text(), response.status));
  }
}

export function testSavedAccountConnection(
  connectionId: string
): Promise<AccountConnectionTestResult> {
  return fetchJson<AccountConnectionTestResult>(
    `/account-connections/${encodeURIComponent(connectionId)}/test`,
    { method: "POST" }
  );
}

export function testDraftAccountConnection(
  payload: AccountConnectionWrite
): Promise<AccountConnectionTestResult> {
  return fetchJson<AccountConnectionTestResult>("/account-connections/test", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export async function mutateFloatingWatchPosition(
  action: "add" | "remove",
  position: AccountPositionIdentity
): Promise<FloatingWatchSettings> {
  return fetchJson<FloatingWatchSettings>("/settings/floating-watch/positions", {
    method: "POST",
    body: JSON.stringify({ action, position })
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
      throw new Error(extractErrorMessage(await response.text(), response.status));
    }
    return response.json() as Promise<ExchangeAnnouncement[]>;
  });
}

export async function listAnnouncementExchanges(): Promise<AnnouncementExchangeOption[]> {
  return fetchJson<AnnouncementExchangeOption[]>("/announcements/exchanges");
}

export async function getOilNewsSettings(): Promise<OilNewsSettings> {
  return fetchJson<OilNewsSettings>("/settings/oil-news");
}

export async function updateOilNewsSettings(settings: OilNewsSettings): Promise<OilNewsSettings> {
  return fetchJson<OilNewsSettings>("/settings/oil-news", {
    method: "PUT",
    body: JSON.stringify(settings)
  });
}

export async function listOilNews(filters: OilNewsFilters = {}): Promise<OilNewsItem[]> {
  const url = buildUrl("/oil-news", { limit: 200, ...filters });
  const response = await fetch(url, { headers: authHeaders() });
  if (!response.ok) {
    throw new Error(extractErrorMessage(await response.text(), response.status));
  }
  return response.json() as Promise<OilNewsItem[]>;
}

export async function refreshOilNews(): Promise<OilNewsRefreshResult> {
  return fetchJson<OilNewsRefreshResult>("/oil-news/refresh", { method: "POST" });
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

export async function getAstroPreaddExchanges(): Promise<string[]> {
  return fetchJson<string[]>("/astro/preadd/exchanges");
}

export async function getAstroPreaddSettings(): Promise<AstroPreaddSettings> {
  return fetchJson<AstroPreaddSettings>("/astro/preadd/settings");
}

export async function updateAstroPreaddSettings(settings: AstroPreaddSettings): Promise<AstroPreaddSettings> {
  return fetchJson<AstroPreaddSettings>("/astro/preadd/settings", {
    method: "PUT", body: JSON.stringify(settings)
  });
}

export async function getAstroPreaddPreview(): Promise<AstroPreaddPreview> {
  return fetchJson<AstroPreaddPreview>("/astro/preadd/preview");
}

export async function runAstroPreadd(candidateIds?: string[]): Promise<AstroPreaddRunResult> {
  return fetchJson<AstroPreaddRunResult>("/astro/preadd/run", {
    method: "POST", body: JSON.stringify({ candidate_ids: candidateIds ?? null })
  });
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
      throw new Error(extractErrorMessage(await response.text(), response.status));
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
      throw new Error(extractErrorMessage(await response.text(), response.status));
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
      throw new Error(extractErrorMessage(await response.text(), response.status));
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
      throw new Error(extractErrorMessage(await response.text(), response.status));
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
      throw new Error(extractErrorMessage(await response.text(), response.status));
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
      throw new Error(extractErrorMessage(await response.text(), response.status));
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
      throw new Error(extractErrorMessage(await response.text(), response.status));
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
      throw new Error(extractErrorMessage(await response.text(), response.status));
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
      throw new Error(extractErrorMessage(await response.text(), response.status));
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
      throw new Error(extractErrorMessage(await response.text(), response.status));
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
      throw new Error(extractErrorMessage(await response.text(), response.status));
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
      throw new Error(extractErrorMessage(await response.text(), response.status));
    }
    return response.json() as Promise<IndexComponentSnapshot[]>;
  });
}

export async function listIndexComponentWatchlist(): Promise<IndexComponentWatchItem[]> {
  return fetchJson<IndexComponentWatchItem[]>("/index-components/watchlist");
}

export async function getIndexComponentAutoWatch(): Promise<IndexComponentAutoWatchStatus> {
  return fetchJson<IndexComponentAutoWatchStatus>("/index-components/auto-watch");
}

export async function updateIndexComponentAutoWatch(enabled: boolean): Promise<IndexComponentAutoWatchStatus> {
  return fetchJson<IndexComponentAutoWatchStatus>("/index-components/auto-watch", {
    method: "PUT", body: JSON.stringify({ enabled })
  });
}

export async function syncIndexComponentAutoWatch(): Promise<IndexComponentAutoWatchStatus> {
  return fetchJson<IndexComponentAutoWatchStatus>("/index-components/auto-watch/sync", { method: "POST" });
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
      throw new Error(extractErrorMessage(await response.text(), response.status));
    }
    return response.json() as Promise<OpportunityHistoryStats>;
  });
}

export async function queryPairSpread(query: {
  leg1_exchange: string;
  leg1_symbol: string;
  leg1_market_type?: MarketType;
  leg1_dex?: string;
  leg2_exchange: string;
  leg2_symbol: string;
  leg2_market_type?: MarketType;
  leg2_dex?: string;
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

export async function listPairSpreadPresets(): Promise<PairSpreadPreset[]> {
  const value = await fetchJson<unknown>("/pair-spread/presets");
  return Array.isArray(value) ? (value as PairSpreadPreset[]) : [];
}

export async function mergePairSpreadPresets(
  presets: PairSpreadPreset[]
): Promise<PairSpreadPreset[]> {
  return fetchJson<PairSpreadPreset[]>("/pair-spread/presets/merge", {
    method: "POST",
    body: JSON.stringify({ presets })
  });
}

export async function upsertPairSpreadPreset(
  preset: PairSpreadPreset
): Promise<PairSpreadPreset> {
  return fetchJson<PairSpreadPreset>(`/pair-spread/presets/${encodeURIComponent(preset.id)}`, {
    method: "PUT",
    body: JSON.stringify(preset)
  });
}

export async function deletePairSpreadPreset(id: string): Promise<void> {
  await fetchJson(`/pair-spread/presets/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export async function listHyperliquidMarkets(): Promise<HyperliquidDexMarket[]> {
  const value = await fetchJson<unknown>("/pair-spread/hyperliquid-markets");
  return Array.isArray(value) ? (value as HyperliquidDexMarket[]) : [];
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
  leg1_dex?: string;
  leg2_exchange: string;
  leg2_symbol: string;
  leg2_market_type?: MarketType;
  leg2_dex?: string;
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
  leg1_dex?: string;
  leg2_exchange: string;
  leg2_symbol: string;
  leg2_market_type?: MarketType;
  leg2_dex?: string;
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
  leg1_dex?: string;
  leg2_exchange: string;
  leg2_symbol: string;
  leg2_market_type?: MarketType;
  leg2_dex?: string;
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
  dex?: string;
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
  dex?: string;
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

export async function runFatFingerBacktest(
  request: FatFingerBacktestRequest
): Promise<FatFingerBacktestResult> {
  return fetchJson<FatFingerBacktestResult>("/second-level-sampling/fat-finger-backtest", {
    method: "POST",
    body: JSON.stringify(request)
  });
}

export async function listNegativeBasisExchanges(): Promise<{ spot: string[]; future: string[] }> {
  return fetchJson<{ spot: string[]; future: string[] }>("/negative-basis-monitor/exchanges");
}

export async function getNegativeBasisMonitorStatus(): Promise<NegativeBasisMonitorStatus> {
  return fetchJson<NegativeBasisMonitorStatus>("/negative-basis-monitor/status");
}

export async function refreshNegativeBasisAutoScan(): Promise<NegativeBasisAutoCandidate[]> {
  return fetchJson<NegativeBasisAutoCandidate[]>("/negative-basis-monitor/auto-scan", {
    method: "POST",
    body: JSON.stringify({})
  });
}

export async function getNegativeBasisAutoScanSettings(): Promise<NegativeBasisAutoScanSettings> {
  return fetchJson<NegativeBasisAutoScanSettings>("/negative-basis-monitor/auto-scan/settings");
}

export async function updateNegativeBasisAutoScanSettings(
  settings: NegativeBasisAutoScanSettings
): Promise<NegativeBasisAutoScanSettings> {
  return fetchJson<NegativeBasisAutoScanSettings>("/negative-basis-monitor/auto-scan/settings", {
    method: "PUT",
    body: JSON.stringify(settings)
  });
}

export async function blockNegativeBasisSymbol(symbol: string): Promise<NegativeBasisAutoScanSettings> {
  const url = buildUrl("/negative-basis-monitor/auto-scan/block-symbol", { symbol });
  return fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...authHeaders()
    },
    body: JSON.stringify({})
  }).then(async (response) => {
    if (!response.ok) {
      const text = await response.text();
      throw new Error(extractErrorMessage(text, response.status));
    }
    return response.json() as Promise<NegativeBasisAutoScanSettings>;
  });
}

export async function unblockNegativeBasisSymbol(symbol: string): Promise<NegativeBasisAutoScanSettings> {
  const url = buildUrl("/negative-basis-monitor/auto-scan/block-symbol", { symbol });
  return fetch(url, {
    method: "DELETE",
    headers: authHeaders()
  }).then(async (response) => {
    if (!response.ok) {
      const text = await response.text();
      throw new Error(extractErrorMessage(text, response.status));
    }
    return response.json() as Promise<NegativeBasisAutoScanSettings>;
  });
}

export async function blockNegativeBasisExchange(exchange: string): Promise<NegativeBasisAutoScanSettings> {
  const url = buildUrl("/negative-basis-monitor/auto-scan/block-exchange", { exchange });
  return fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...authHeaders()
    },
    body: JSON.stringify({})
  }).then(async (response) => {
    if (!response.ok) {
      const text = await response.text();
      throw new Error(extractErrorMessage(text, response.status));
    }
    return response.json() as Promise<NegativeBasisAutoScanSettings>;
  });
}

export async function unblockNegativeBasisExchange(exchange: string): Promise<NegativeBasisAutoScanSettings> {
  const url = buildUrl("/negative-basis-monitor/auto-scan/block-exchange", { exchange });
  return fetch(url, {
    method: "DELETE",
    headers: authHeaders()
  }).then(async (response) => {
    if (!response.ok) {
      const text = await response.text();
      throw new Error(extractErrorMessage(text, response.status));
    }
    return response.json() as Promise<NegativeBasisAutoScanSettings>;
  });
}

export async function blockNegativeBasisExchangeSymbol(
  exchange: string,
  symbol: string
): Promise<NegativeBasisAutoScanSettings> {
  const url = buildUrl("/negative-basis-monitor/auto-scan/block-exchange-symbol", { exchange, symbol });
  return fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...authHeaders()
    },
    body: JSON.stringify({})
  }).then(async (response) => {
    if (!response.ok) {
      const text = await response.text();
      throw new Error(extractErrorMessage(text, response.status));
    }
    return response.json() as Promise<NegativeBasisAutoScanSettings>;
  });
}

export async function unblockNegativeBasisExchangeSymbol(
  exchange: string,
  symbol: string
): Promise<NegativeBasisAutoScanSettings> {
  const url = buildUrl("/negative-basis-monitor/auto-scan/block-exchange-symbol", { exchange, symbol });
  return fetch(url, {
    method: "DELETE",
    headers: authHeaders()
  }).then(async (response) => {
    if (!response.ok) {
      const text = await response.text();
      throw new Error(extractErrorMessage(text, response.status));
    }
    return response.json() as Promise<NegativeBasisAutoScanSettings>;
  });
}

export async function listNegativeBasisWatchlist(): Promise<NegativeBasisWatchItem[]> {
  return fetchJson<NegativeBasisWatchItem[]>("/negative-basis-monitor/watchlist");
}

export async function upsertNegativeBasisWatchItem(
  item: NegativeBasisWatchItem
): Promise<NegativeBasisWatchItem> {
  return fetchJson<NegativeBasisWatchItem>("/negative-basis-monitor/watchlist", {
    method: "POST",
    body: JSON.stringify(item)
  });
}

export async function deleteNegativeBasisWatchItem(itemId: string): Promise<{ ok: boolean }> {
  return fetchJson<{ ok: boolean }>(`/negative-basis-monitor/watchlist/${itemId}`, {
    method: "DELETE"
  });
}

export async function collectNegativeBasisWatchItem(
  itemId: string
): Promise<NegativeBasisAnalysisResult> {
  return fetchJson<NegativeBasisAnalysisResult>(`/negative-basis-monitor/watchlist/${itemId}/collect`, {
    method: "POST",
    body: JSON.stringify({})
  });
}

export async function listNegativeBasisSamples(query: {
  watch_id?: string;
  symbol?: string;
  minutes?: number;
  limit?: number;
} = {}): Promise<NegativeBasisSignalSample[]> {
  const url = buildUrl("/negative-basis-monitor/samples", query);
  return fetch(url, { headers: authHeaders() }).then(async (response) => {
    if (!response.ok) {
      const text = await response.text();
      throw new Error(extractErrorMessage(text, response.status));
    }
    return response.json() as Promise<NegativeBasisSignalSample[]>;
  });
}

export async function listNegativeBasisEvents(query: {
  watch_id?: string;
  symbol?: string;
  minutes?: number;
  limit?: number;
} = {}): Promise<NegativeBasisAlertEvent[]> {
  const url = buildUrl("/negative-basis-monitor/events", query);
  return fetch(url, { headers: authHeaders() }).then(async (response) => {
    if (!response.ok) {
      const text = await response.text();
      throw new Error(extractErrorMessage(text, response.status));
    }
    return response.json() as Promise<NegativeBasisAlertEvent[]>;
  });
}

export async function queryNegativeBasis(query: {
  symbol?: string;
  spot_exchange?: string;
  future_exchange?: string;
  spot_symbol?: string;
  future_symbol?: string;
  future_multiplier?: number;
  hours?: number;
  watch_threshold_pct?: number;
  building_threshold_pct?: number;
  confirmed_threshold_pct?: number;
  strong_threshold_pct?: number;
  extreme_threshold_pct?: number;
  watch_consecutive_hits?: number;
  building_consecutive_hits?: number;
  confirmed_consecutive_hits?: number;
  strong_consecutive_hits?: number;
  extreme_consecutive_hits?: number;
  spot_volume_growth_threshold?: number;
  oi_confirmed_growth_pct?: number;
  oi_strong_growth_pct?: number;
  min_spot_hourly_volume_usdt?: number;
}): Promise<NegativeBasisAnalysisResult> {
  const url = buildUrl("/negative-basis-monitor/query", query);
  return fetch(url, { headers: authHeaders() }).then(async (response) => {
    if (!response.ok) {
      const text = await response.text();
      throw new Error(extractErrorMessage(text, response.status));
    }
    return response.json() as Promise<NegativeBasisAnalysisResult>;
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

export async function previewInstrumentAstroPair(
  route: AstroInstrumentRouteRequest
): Promise<AstroPairPlan> {
  return fetchJson<AstroPairPlan>("/astro/instrument/preview", {
    method: "POST",
    body: JSON.stringify(route)
  });
}

export async function createInstrumentAstroCard(
  route: AstroInstrumentRouteRequest,
  expectedOpenSpreadPct: number,
  card: AstroCardCreateRequest = {}
): Promise<AstroActionResult> {
  return fetchJson<AstroActionResult>("/astro/instrument/card", {
    method: "POST",
    body: JSON.stringify({
      route,
      card,
      expected_open_spread_pct: expectedOpenSpreadPct
    })
  });
}

export async function listAstroPairs(): Promise<AstroPairStatus[]> {
  const value = await fetchJson<unknown>("/astro/pairs");
  if (!Array.isArray(value)) throw new Error("Astro 卡片列表格式无效");
  return value.filter(
    (item): item is AstroPairStatus => typeof item === "object" && item !== null && !Array.isArray(item)
  );
}

export function saveDashboardPassword(password: string): void {
  if (password) {
    window.localStorage.setItem("dashboard_password", password);
  } else {
    window.localStorage.removeItem("dashboard_password");
  }
}
