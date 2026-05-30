import {
  Alert,
  Button,
  Checkbox,
  Col,
  Descriptions,
  Form,
  InputNumber,
  Modal,
  Row,
  Segmented,
  Space,
  Statistic,
  Table,
  Tag,
  Typography,
  message
} from "antd";
import { useEffect, useMemo, useState } from "react";
import dayjs from "dayjs";
import utc from "dayjs/plugin/utc";
import { EyeOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";

import {
  createAstroCard,
  getOpportunityHistoryStats,
  getRiskSettings,
  previewAstroPair,
  updateRiskSettings
} from "../api/client";
import type {
  AstroActionResult,
  AstroCardCreateRequest,
  AstroFieldAssumption,
  AstroPairPlan,
  ExchangePollState,
  Opportunity,
  OpportunityFilters,
  OpportunityHistoryPoint,
  OpportunityHistoryStats,
  OpportunitySpreadStats,
  OpportunityType,
  RiskSettings
} from "../api/types";
import { OpportunityTable } from "../components/OpportunityTable";
import { TopFilters } from "../components/TopFilters";
import { defaultHiddenRiskLabels } from "../constants/riskLabels";
import { useRadarStore } from "../state/useRadarStore";

dayjs.extend(utc);

function normalizeSymbol(value: string): string {
  return value.toUpperCase().replace(/[-_]/g, "");
}

function normalizeSymbols(values: string[] | undefined): string[] {
  return Array.from(
    new Set((values ?? []).map((item) => normalizeSymbol(item)).filter((item) => item.length > 0))
  );
}

function jsonBlock(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

const astroAssumptionColumns: ColumnsType<AstroFieldAssumption> = [
  {
    title: "Field",
    dataIndex: "field",
    width: 136,
    render: (value: string, row) => (
      <Space direction="vertical" size={2}>
        <Typography.Text strong>{value}</Typography.Text>
        <Tag color={row.needs_verification ? "orange" : "green"}>
          {row.needs_verification ? "Needs verification" : "Confirmed"}
        </Tag>
      </Space>
    )
  },
  {
    title: "Source",
    dataIndex: "source",
    width: 180
  },
  {
    title: "Assumed value",
    dataIndex: "assumed_value",
    width: 160,
    render: (value: string) => <Typography.Text code>{value}</Typography.Text>
  },
  {
    title: "Note",
    dataIndex: "note"
  }
];

type ExchangeStateRow = ExchangePollState & {
  exchange: string;
};

type AstroSizingFormValues = Required<Omit<AstroCardCreateRequest, "save_as_default">> & {
  save_as_default: boolean;
};

const dashboardFilterStorageKey = "taoli1.dashboard.filters.v1";
const opportunityTypes: OpportunityType[] = ["SF", "FF", "SS"];
const spreadHistoryRanges = [
  { label: "24h", value: 24 },
  { label: "72h", value: 72 },
  { label: "7天", value: 168 },
  { label: "30天", value: 720 }
];

function isOpportunityType(value: unknown): value is OpportunityType {
  return typeof value === "string" && opportunityTypes.includes(value as OpportunityType);
}

function normalizeOpportunityTypes(value: unknown): OpportunityType[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return Array.from(new Set(value.filter(isOpportunityType)));
}

function defaultDashboardFilters(): OpportunityFilters {
  return {
    include_risky: false,
    hidden_risk_labels: defaultHiddenRiskLabels,
    exclude_types: []
  };
}

function loadPersistedDashboardFilters(): Partial<OpportunityFilters> {
  if (typeof window === "undefined") {
    return {};
  }
  try {
    const raw = window.localStorage.getItem(dashboardFilterStorageKey);
    if (!raw) {
      return {};
    }
    const parsed = JSON.parse(raw) as { exclude_types?: unknown };
    const excludeTypes = normalizeOpportunityTypes(parsed.exclude_types);
    return excludeTypes.length > 0 ? { exclude_types: excludeTypes } : {};
  } catch {
    return {};
  }
}

function initialDashboardFilters(): OpportunityFilters {
  return {
    ...defaultDashboardFilters(),
    ...loadPersistedDashboardFilters()
  };
}

function savePersistedDashboardFilters(filters: OpportunityFilters): void {
  if (typeof window === "undefined") {
    return;
  }
  const excludeTypes = normalizeOpportunityTypes(filters.exclude_types);
  try {
    if (excludeTypes.length > 0) {
      window.localStorage.setItem(
        dashboardFilterStorageKey,
        JSON.stringify({ exclude_types: excludeTypes })
      );
    } else {
      window.localStorage.removeItem(dashboardFilterStorageKey);
    }
  } catch {
    // Ignore storage failures; filters still work for the current page lifetime.
  }
}

function formatExchangeTimestamp(value: string | null | undefined): string {
  return value ? dayjs.utc(value).format("MM-DD HH:mm:ss [UTC]") : "-";
}

function formatPct(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value) ? `${value.toFixed(3)}%` : "-";
}

function formatSigned(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "-";
  }
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}`;
}

function spreadStatus(stats: OpportunitySpreadStats): { color: string; label: string } {
  if (typeof stats.current !== "number") {
    return { color: "default", label: "暂无当前值" };
  }
  if (typeof stats.p95 === "number" && stats.current >= stats.p95) {
    return { color: "green", label: "高于p95" };
  }
  if (typeof stats.p05 === "number" && stats.current <= stats.p05) {
    return { color: "orange", label: "低于p05" };
  }
  if (typeof stats.mean === "number" && stats.current >= stats.mean) {
    return { color: "blue", label: "高于均值" };
  }
  return { color: "default", label: "均值附近" };
}

function valuePath(
  points: OpportunityHistoryPoint[],
  field: keyof Pick<
    OpportunityHistoryPoint,
    "open_spread_pct" | "close_spread_pct" | "fee_adjusted_open_pct"
  >,
  xAt: (index: number) => number,
  yAt: (value: number) => number
): string {
  return points
    .map((point, index) => `${index === 0 ? "M" : "L"} ${xAt(index).toFixed(1)} ${yAt(point[field]).toFixed(1)}`)
    .join(" ");
}

function SpreadHistoryChart({ stats }: { stats: OpportunityHistoryStats }) {
  const points = stats.points;
  const width = 920;
  const height = 300;
  const padding = { top: 18, right: 64, bottom: 34, left: 52 };
  const chartWidth = width - padding.left - padding.right;
  const chartHeight = height - padding.top - padding.bottom;
  if (points.length === 0) {
    return (
      <div className="spread-history-empty" data-testid="spread-history-chart">
        暂无价差历史样本
      </div>
    );
  }
  const values = points.flatMap((point) => [
    point.open_spread_pct,
    point.close_spread_pct,
    point.fee_adjusted_open_pct
  ]);
  [
    stats.open_spread_pct.mean,
    stats.open_spread_pct.p05,
    stats.open_spread_pct.p95,
    stats.open_spread_pct.current
  ].forEach((value) => {
    if (typeof value === "number" && Number.isFinite(value)) {
      values.push(value);
    }
  });
  const rawMin = Math.min(...values);
  const rawMax = Math.max(...values);
  const span = rawMax - rawMin || 1;
  const min = rawMin - span * 0.12;
  const max = rawMax + span * 0.12;
  const xAt = (index: number) =>
    padding.left + (points.length === 1 ? chartWidth / 2 : (chartWidth * index) / (points.length - 1));
  const yAt = (value: number) => padding.top + ((max - value) / (max - min)) * chartHeight;
  const referenceLines = [
    { label: "p95上边界", value: stats.open_spread_pct.p95, color: "#b45309" },
    { label: "历史均值", value: stats.open_spread_pct.mean, color: "#0f766e" },
    { label: "p05下边界", value: stats.open_spread_pct.p05, color: "#64748b" }
  ].filter((line): line is { label: string; value: number; color: string } => typeof line.value === "number");
  const first = points[0];
  const last = points[points.length - 1];

  return (
    <div className="spread-history-chart-wrap">
      <svg
        className="spread-history-chart"
        data-testid="spread-history-chart"
        role="img"
        aria-label="价差历史统计图"
        viewBox={`0 0 ${width} ${height}`}
      >
        <rect x={padding.left} y={padding.top} width={chartWidth} height={chartHeight} rx="4" />
        {[0, 0.25, 0.5, 0.75, 1].map((tick) => {
          const y = padding.top + chartHeight * tick;
          const value = max - (max - min) * tick;
          return (
            <g key={tick}>
              <line className="spread-grid-line" x1={padding.left} y1={y} x2={padding.left + chartWidth} y2={y} />
              <text className="spread-axis-label" x={padding.left - 8} y={y + 4} textAnchor="end">
                {formatPct(value)}
              </text>
            </g>
          );
        })}
        {referenceLines.map((line) => {
          const y = yAt(line.value);
          return (
            <g key={line.label}>
              <line
                className="spread-reference-line"
                x1={padding.left}
                y1={y}
                x2={padding.left + chartWidth}
                y2={y}
                stroke={line.color}
              />
              <text className="spread-reference-label" x={padding.left + chartWidth + 8} y={y + 4}>
                {line.label}
              </text>
            </g>
          );
        })}
        <path
          className="spread-line spread-line-open"
          d={valuePath(points, "open_spread_pct", xAt, yAt)}
        />
        <path
          className="spread-line spread-line-close"
          d={valuePath(points, "close_spread_pct", xAt, yAt)}
        />
        <path
          className="spread-line spread-line-fee"
          d={valuePath(points, "fee_adjusted_open_pct", xAt, yAt)}
        />
        <circle
          className="spread-current-point"
          cx={xAt(points.length - 1)}
          cy={yAt(last.open_spread_pct)}
          r="4.8"
        />
        <text className="spread-axis-label" x={padding.left} y={height - 10}>
          {dayjs.utc(first.observed_at).format("MM-DD HH:mm")}
        </text>
        <text className="spread-axis-label" x={padding.left + chartWidth} y={height - 10} textAnchor="end">
          {dayjs.utc(last.observed_at).format("MM-DD HH:mm")}
        </text>
      </svg>
      <div className="spread-chart-legend">
        <span className="legend-open">开仓差</span>
        <span className="legend-close">平仓差</span>
        <span className="legend-fee">扣费开仓差</span>
      </div>
    </div>
  );
}

function StatTile({
  label,
  value,
  extra
}: {
  label: string;
  value: string | number;
  extra?: string;
}) {
  return (
    <div className="spread-stat-tile">
      <Typography.Text type="secondary">{label}</Typography.Text>
      <Typography.Title level={4}>{value}</Typography.Title>
      {extra ? <Typography.Text type="secondary">{extra}</Typography.Text> : null}
    </div>
  );
}

function SpreadStatsDescriptions({ stats }: { stats: OpportunityHistoryStats }) {
  return (
    <Descriptions bordered size="small" column={4} className="spread-history-descriptions">
      <Descriptions.Item label="开仓均值">{formatPct(stats.open_spread_pct.mean)}</Descriptions.Item>
      <Descriptions.Item label="开仓中位">{formatPct(stats.open_spread_pct.median)}</Descriptions.Item>
      <Descriptions.Item label="开仓p05">{formatPct(stats.open_spread_pct.p05)}</Descriptions.Item>
      <Descriptions.Item label="开仓p95">{formatPct(stats.open_spread_pct.p95)}</Descriptions.Item>
      <Descriptions.Item label="平仓当前">{formatPct(stats.close_spread_pct.current)}</Descriptions.Item>
      <Descriptions.Item label="平仓均值">{formatPct(stats.close_spread_pct.mean)}</Descriptions.Item>
      <Descriptions.Item label="扣费当前">{formatPct(stats.fee_adjusted_open_pct.current)}</Descriptions.Item>
      <Descriptions.Item label="扣费均值">{formatPct(stats.fee_adjusted_open_pct.mean)}</Descriptions.Item>
      <Descriptions.Item label="下周期资金均值">
        {formatPct(stats.net_funding_next_pct.mean)}
      </Descriptions.Item>
      <Descriptions.Item label="下周期资金当前">
        {formatPct(stats.net_funding_next_pct.current)}
      </Descriptions.Item>
      <Descriptions.Item label="开仓z-score">
        {formatSigned(stats.open_spread_pct.z_score)}
      </Descriptions.Item>
      <Descriptions.Item label="决策状态">
        <Tag color={spreadStatus(stats.open_spread_pct).color}>
          {spreadStatus(stats.open_spread_pct).label}
        </Tag>
      </Descriptions.Item>
    </Descriptions>
  );
}

function exchangeStatusColor(status: ExchangePollState["status"] | null | undefined): string | undefined {
  switch (status) {
    case "healthy":
      return "green";
    case "degraded":
      return "gold";
    case "cooling_down":
      return "red";
    default:
      return undefined;
  }
}

function exchangeStatusLabel(status: ExchangePollState["status"] | null | undefined): string {
  return status ?? "unknown";
}

function formatFailureCount(value: number | null | undefined): string | number {
  return typeof value === "number" ? value : "n/a";
}

function formatInFlight(value: boolean | null | undefined) {
  if (typeof value !== "boolean") {
    return <Tag>unknown</Tag>;
  }
  return <Tag color={value ? "blue" : "default"}>{value ? "yes" : "no"}</Tag>;
}

function pairNumber(pair: Record<string, unknown> | null | undefined, key: string): number | undefined {
  const value = pair?.[key];
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : undefined;
  }
  return undefined;
}

function pairString(pair: Record<string, unknown> | null | undefined, key: string): string {
  const value = pair?.[key];
  if (value === null || value === undefined) {
    return "-";
  }
  return String(value);
}

function sizingFromPlan(plan: AstroPairPlan): AstroSizingFormValues {
  return {
    max_trade_usdt: pairNumber(plan.pair, "maxTradeUSDT") ?? 0,
    leverage: pairNumber(plan.pair, "leverage") ?? 1,
    min_notional: pairNumber(plan.pair, "minNotional") ?? 0,
    max_notional: pairNumber(plan.pair, "maxNotional") ?? 0,
    save_as_default: false
  };
}

const exchangeStateColumns: ColumnsType<ExchangeStateRow> = [
  {
    title: "Exchange",
    dataIndex: "exchange",
    width: 120,
    render: (value: string) => <Typography.Text strong>{value}</Typography.Text>
  },
  {
    title: "Status",
    dataIndex: "status",
    width: 120,
    render: (value: ExchangePollState["status"] | null | undefined) => (
      <Tag color={exchangeStatusColor(value)}>{exchangeStatusLabel(value)}</Tag>
    )
  },
  {
    title: "Last success UTC",
    dataIndex: "last_success_at",
    width: 132,
    render: (value: string | null) => formatExchangeTimestamp(value)
  },
  {
    title: "Last error UTC",
    dataIndex: "last_error_at",
    width: 132,
    render: (value: string | null) => formatExchangeTimestamp(value)
  },
  {
    title: "Failures",
    dataIndex: "consecutive_failures",
    width: 84,
    align: "right",
    render: (value: number | null | undefined) => formatFailureCount(value)
  },
  {
    title: "Cooldown UTC",
    dataIndex: "cooldown_until",
    width: 132,
    render: (value: string | null) => formatExchangeTimestamp(value)
  },
  {
    title: "Next due UTC",
    dataIndex: "next_due_at",
    width: 132,
    render: (value: string | null) => formatExchangeTimestamp(value)
  },
  {
    title: "In flight",
    dataIndex: "in_flight",
    width: 92,
    render: (value: boolean | null | undefined) => formatInFlight(value)
  }
];

export function DashboardPage() {
  const [astroSizingForm] = Form.useForm<AstroSizingFormValues>();
  const [filters, setFilters] = useState<OpportunityFilters>(() => initialDashboardFilters());
  const [riskSettings, setRiskSettings] = useState<RiskSettings | null>(null);
  const [savingSymbol, setSavingSymbol] = useState<string | null>(null);
  const [settingsLoaded, setSettingsLoaded] = useState(false);
  const [astroPreviewOpportunity, setAstroPreviewOpportunity] = useState<Opportunity | null>(null);
  const [astroPreviewPlan, setAstroPreviewPlan] = useState<AstroPairPlan | null>(null);
  const [astroPreviewLoading, setAstroPreviewLoading] = useState(false);
  const [astroPreviewError, setAstroPreviewError] = useState<string | null>(null);
  const [astroSubmitLoading, setAstroSubmitLoading] = useState(false);
  const [astroSubmitResult, setAstroSubmitResult] = useState<AstroActionResult | null>(null);
  const [astroSubmitError, setAstroSubmitError] = useState<string | null>(null);
  const [historyOpportunity, setHistoryOpportunity] = useState<Opportunity | null>(null);
  const [historyStats, setHistoryStats] = useState<OpportunityHistoryStats | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [historyHours, setHistoryHours] = useState(168);
  const { opportunities, health, loading, error, refresh } = useRadarStore(filters, settingsLoaded);
  const errors = health?.exchange_errors ?? {};
  const exchangeStates = useMemo(
    () =>
      Object.entries(health?.exchange_states ?? {}).map(([exchange, state]) => ({
        exchange,
        ...state
      })),
    [health]
  );
  const blockedSymbols = riskSettings ? normalizeSymbols(riskSettings.excluded_symbols) : [];
  const astroPreviewSymbol = useMemo(
    () => (astroPreviewOpportunity ? normalizeSymbol(astroPreviewOpportunity.symbol) : null),
    [astroPreviewOpportunity]
  );

  const changeFilters = (nextFilters: OpportunityFilters) => {
    setFilters(nextFilters);
    savePersistedDashboardFilters(nextFilters);
  };

  useEffect(() => {
    let cancelled = false;
    void getRiskSettings()
      .then((settings) => {
        if (cancelled) {
          return;
        }
        const normalizedSettings = {
          ...settings,
          excluded_symbols: normalizeSymbols(settings.excluded_symbols)
        };
        setRiskSettings(normalizedSettings);
        if (typeof settings.min_volume_24h_usdt === "number") {
          setFilters((current) => ({
            ...current,
            min_volume_24h_k: Math.round(settings.min_volume_24h_usdt / 1000)
          }));
        }
        setSettingsLoaded(true);
      })
      .catch(() => {
        if (!cancelled) {
          setSettingsLoaded(true);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const toggleBlockedSymbol = async (symbol: string, block: boolean) => {
    if (!riskSettings) {
      return;
    }
    const normalizedSymbol = normalizeSymbol(symbol);
    if (!normalizedSymbol) {
      return;
    }
    const currentExcluded = normalizeSymbols(riskSettings.excluded_symbols);
    const nextExcluded = block
      ? normalizeSymbols([...currentExcluded, normalizedSymbol])
      : currentExcluded.filter((item) => item !== normalizedSymbol);
    setSavingSymbol(normalizedSymbol);
    try {
      const saved = await updateRiskSettings({
        ...riskSettings,
        excluded_symbols: nextExcluded
      });
      const normalizedSaved = {
        ...saved,
        excluded_symbols: normalizeSymbols(saved.excluded_symbols)
      };
      setRiskSettings(normalizedSaved);
      message.success(block ? `Blocked ${normalizedSymbol}` : `Unblocked ${normalizedSymbol}`);
      await refresh();
    } catch (exc) {
      message.error(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setSavingSymbol(null);
    }
  };

  const closeAstroPreview = () => {
    setAstroPreviewOpportunity(null);
    setAstroPreviewPlan(null);
    setAstroPreviewError(null);
    setAstroPreviewLoading(false);
    setAstroSubmitLoading(false);
    setAstroSubmitResult(null);
    setAstroSubmitError(null);
    astroSizingForm.resetFields();
  };

  const openAstroPreview = async (opportunity: Opportunity) => {
    setAstroPreviewOpportunity(opportunity);
    setAstroPreviewPlan(null);
    setAstroPreviewError(null);
    setAstroSubmitResult(null);
    setAstroSubmitError(null);
    setAstroPreviewLoading(true);
    try {
      const plan = await previewAstroPair(opportunity.id);
      setAstroPreviewPlan(plan);
      if (plan.pair) {
        astroSizingForm.setFieldsValue(sizingFromPlan(plan));
      } else {
        astroSizingForm.resetFields();
      }
    } catch (exc) {
      setAstroPreviewError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setAstroPreviewLoading(false);
    }
  };

  const submitAstroCard = async () => {
    if (!astroPreviewOpportunity || !astroPreviewPlan?.can_submit) {
      return;
    }
    setAstroSubmitLoading(true);
    setAstroSubmitResult(null);
    setAstroSubmitError(null);
    try {
      const sizing = await astroSizingForm.validateFields();
      const result = await createAstroCard(astroPreviewOpportunity.id, sizing);
      setAstroSubmitResult(result);
      if (result.status === "created" || result.status === "updated") {
        message.success(result.message);
      } else if (result.status === "failed") {
        message.error(result.message);
      } else {
        message.warning(result.message);
      }
    } catch (exc) {
      const text = exc instanceof Error ? exc.message : String(exc);
      setAstroSubmitError(text);
      message.error(text);
    } finally {
      setAstroSubmitLoading(false);
    }
  };

  const openSpreadHistory = async (opportunity: Opportunity, hours = historyHours) => {
    setHistoryOpportunity(opportunity);
    setHistoryStats(null);
    setHistoryError(null);
    setHistoryLoading(true);
    try {
      const stats = await getOpportunityHistoryStats({
        symbol: opportunity.symbol,
        opportunity_id: opportunity.id,
        type: opportunity.type,
        hours,
        point_limit: 360
      });
      setHistoryStats(stats);
    } catch (exc) {
      setHistoryError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setHistoryLoading(false);
    }
  };

  const closeSpreadHistory = () => {
    setHistoryOpportunity(null);
    setHistoryStats(null);
    setHistoryError(null);
    setHistoryLoading(false);
  };

  const changeSpreadHistoryRange = (value: string | number) => {
    const nextHours = Number(value);
    setHistoryHours(nextHours);
    if (historyOpportunity) {
      void openSpreadHistory(historyOpportunity, nextHours);
    }
  };

  const astroSubmitDisabled =
    astroPreviewLoading ||
    astroSubmitLoading ||
    !astroPreviewPlan ||
    !astroPreviewPlan.can_submit;

  return (
    <div className="page">
      <TopFilters filters={filters} loading={loading} onChange={changeFilters} onRefresh={refresh} />
      {blockedSymbols.length > 0 ? (
        <div className="blocked-strip">
          <Typography.Text className="blocked-strip-title">Blocked symbols</Typography.Text>
          <Space size={8} wrap className="blocked-strip-list">
            {blockedSymbols.map((symbol) => (
              <Button
                key={symbol}
                size="small"
                type="text"
                icon={<EyeOutlined />}
                aria-label={`\u53d6\u6d88\u5c4f\u853d ${symbol}`}
                loading={savingSymbol === symbol}
                disabled={savingSymbol !== null && savingSymbol !== symbol}
                onClick={() => void toggleBlockedSymbol(symbol, false)}
              >
                取消屏蔽 {symbol}
              </Button>
            ))}
          </Space>
        </div>
      ) : null}
      <Row gutter={[12, 12]} className="metric-row">
        <Col xs={12} md={6}>
          <Statistic title="Opportunities" value={opportunities.length} />
        </Col>
        <Col xs={12} md={6}>
          <Statistic title="Markets" value={health?.markets ?? 0} />
        </Col>
        <Col xs={12} md={6}>
          <Statistic title="交易所链路异常" value={Object.keys(errors).length} />
        </Col>
        <Col xs={12} md={6}>
          <Statistic
            title="Top open spread"
            value={opportunities[0]?.open_spread_pct ?? 0}
            precision={3}
            suffix="%"
          />
        </Col>
      </Row>
      {error ? <Alert className="page-alert" type="error" message={error} showIcon /> : null}
      {Object.keys(errors).length > 0 ? (
        <Alert
          className="page-alert"
          type="warning"
          message="交易所外部 API 链路异常"
          description={
            <div className="exchange-error-list">
              {Object.entries(errors)
                .slice(0, 6)
                .map(([key, value]) => (
                  <div key={key} className="exchange-error-item">
                    <Typography.Text strong>{key}</Typography.Text>
                    <Typography.Text>{`: ${value}`}</Typography.Text>
                  </div>
                ))}
              {Object.keys(errors).length > 6 ? (
                <Typography.Text type="secondary">
                  还有 {Object.keys(errors).length - 6} 条交易所链路异常，请查看健康检查详情。
                </Typography.Text>
              ) : null}
            </div>
          }
          showIcon
        />
      ) : null}
      {exchangeStates.length > 0 ? (
        <div className="metric-row">
          <Space direction="vertical" size={10} style={{ width: "100%" }}>
            <Typography.Title level={5} style={{ margin: 0 }}>
              Exchange states
            </Typography.Title>
            <Table
              size="small"
              pagination={false}
              rowKey="exchange"
              columns={exchangeStateColumns}
              dataSource={exchangeStates}
              scroll={{ x: 960 }}
            />
          </Space>
        </div>
      ) : null}
      <OpportunityTable
        opportunities={opportunities}
        loading={loading}
        blockedSymbols={blockedSymbols}
        actionLoadingSymbol={savingSymbol}
        previewLoadingSymbol={astroPreviewSymbol}
        onToggleSymbol={(symbol, block) => void toggleBlockedSymbol(symbol, block)}
        onPreviewAstro={(opportunity) => void openAstroPreview(opportunity)}
        onOpenHistory={(opportunity) => void openSpreadHistory(opportunity)}
      />
      <Modal
        open={historyOpportunity !== null}
        title="价差历史统计"
        width={1080}
        onCancel={closeSpreadHistory}
        footer={[
          <Button key="close" onClick={closeSpreadHistory}>
            关闭
          </Button>
        ]}
        destroyOnHidden
      >
        {historyOpportunity ? (
          <Space direction="vertical" size={12} className="spread-history-panel">
            <div className="spread-history-head">
              <Space direction="vertical" size={2}>
                <Space size={8} wrap>
                  <Typography.Title level={4}>{historyOpportunity.symbol}</Typography.Title>
                  <Tag>{historyOpportunity.type}</Tag>
                  <Tag color="blue">
                    {historyOpportunity.buy_exchange}/{historyOpportunity.sell_exchange}
                  </Tag>
                </Space>
                <Typography.Text type="secondary">
                  {historyOpportunity.id}
                </Typography.Text>
              </Space>
              <Segmented
                value={historyHours}
                options={spreadHistoryRanges}
                onChange={changeSpreadHistoryRange}
              />
            </div>
            {historyLoading ? <Alert type="info" showIcon message="加载价差历史..." /> : null}
            {historyError ? <Alert type="error" showIcon message={historyError} /> : null}
            {historyStats ? (
              <>
                <div className="spread-history-stats">
                  <StatTile label="样本数" value={historyStats.count} />
                  <StatTile
                    label="当前开仓差"
                    value={formatPct(historyStats.open_spread_pct.current)}
                    extra={`z ${formatSigned(historyStats.open_spread_pct.z_score)}`}
                  />
                  <StatTile
                    label="p95上边界"
                    value={formatPct(historyStats.open_spread_pct.p95)}
                    extra={`p05 ${formatPct(historyStats.open_spread_pct.p05)}`}
                  />
                  <StatTile
                    label="下周期资金差"
                    value={formatPct(historyStats.net_funding_next_pct.current)}
                    extra={`均值 ${formatPct(historyStats.net_funding_next_pct.mean)}`}
                  />
                  <StatTile
                    label="历史区间"
                    value={historyStats.first_seen_at ? dayjs.utc(historyStats.first_seen_at).format("MM-DD HH:mm") : "-"}
                    extra={historyStats.last_seen_at ? dayjs.utc(historyStats.last_seen_at).format("MM-DD HH:mm") : "-"}
                  />
                </div>
                <SpreadHistoryChart stats={historyStats} />
                <SpreadStatsDescriptions stats={historyStats} />
              </>
            ) : null}
          </Space>
        ) : null}
      </Modal>
      <Modal
        open={astroPreviewOpportunity !== null}
        title="Astro dry-run"
        width={1040}
        onCancel={closeAstroPreview}
        footer={[
          <Button key="close" onClick={closeAstroPreview}>
            关闭
          </Button>,
          <Button
            key="submit"
            type="primary"
            disabled={astroSubmitDisabled}
            loading={astroSubmitLoading}
            onClick={() => void submitAstroCard()}
          >
            创建/更新暂停卡片
          </Button>
        ]}
        destroyOnHidden
      >
        {astroPreviewOpportunity ? (
          <Space direction="vertical" size={12} className="astro-preview-panel">
            <Descriptions bordered size="small" column={2}>
              <Descriptions.Item label="Symbol">{astroPreviewOpportunity.symbol}</Descriptions.Item>
              <Descriptions.Item label="Opportunity ID">{astroPreviewOpportunity.id}</Descriptions.Item>
              <Descriptions.Item label="Mode">
                <Tag color="blue">{astroPreviewPlan?.mode ?? "dry_run"}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="Can submit">
                <Tag color={astroPreviewPlan?.can_submit ? "green" : "red"}>
                  {astroPreviewPlan?.can_submit ? "yes" : "no"}
                </Tag>
              </Descriptions.Item>
            </Descriptions>
            {astroPreviewLoading ? <Alert type="info" showIcon message="Loading Astro preview..." /> : null}
            {astroPreviewError ? <Alert type="error" showIcon message={astroPreviewError} /> : null}
            {astroSubmitError ? <Alert type="error" showIcon message={astroSubmitError} /> : null}
            {astroSubmitResult ? (
              <Alert
                type={
                  astroSubmitResult.status === "created" || astroSubmitResult.status === "updated"
                    ? "success"
                    : astroSubmitResult.status === "failed"
                      ? "error"
                      : "warning"
                }
                showIcon
                message={astroSubmitResult.message}
              />
            ) : null}
            {astroPreviewPlan ? (
              <>
                {astroPreviewPlan.warnings.length > 0 ? (
                  <Alert
                    type="warning"
                    showIcon
                    message={astroPreviewPlan.warnings.join(" | ")}
                  />
                ) : null}
                {astroPreviewPlan.blockers.length > 0 ? (
                  <Alert
                    type="error"
                    showIcon
                    message="Blockers"
                    description={astroPreviewPlan.blockers.join(" | ")}
                  />
                ) : null}
                <div className="astro-preview-section">
                  <Typography.Title level={5}>Generated card defaults</Typography.Title>
                  <Descriptions bordered size="small" column={2}>
                    <Descriptions.Item label="Generated openPosition">
                      <Typography.Text code>{pairString(astroPreviewPlan.pair, "openPosition")}</Typography.Text>
                    </Descriptions.Item>
                    <Descriptions.Item label="Generated closePosition">
                      <Typography.Text code>{pairString(astroPreviewPlan.pair, "closePosition")}</Typography.Text>
                    </Descriptions.Item>
                  </Descriptions>
                  {astroPreviewPlan.pair ? (
                    <Form form={astroSizingForm} layout="vertical" className="astro-sizing-form">
                      <div className="form-grid">
                        <Form.Item label="Position value USDT" name="max_trade_usdt" rules={[{ required: true }]}>
                          <InputNumber min={0.01} step={1} className="wide-input" />
                        </Form.Item>
                        <Form.Item label="Leverage" name="leverage" rules={[{ required: true }]}>
                          <InputNumber min={1} step={1} className="wide-input" />
                        </Form.Item>
                        <Form.Item label="Minimum notional USDT" name="min_notional" rules={[{ required: true }]}>
                          <InputNumber min={0} step={1} className="wide-input" />
                        </Form.Item>
                        <Form.Item label="Maximum notional USDT" name="max_notional" rules={[{ required: true }]}>
                          <InputNumber min={0.01} step={1} className="wide-input" />
                        </Form.Item>
                      </div>
                      <Form.Item name="save_as_default" valuePropName="checked">
                        <Checkbox>Save sizing as global default</Checkbox>
                      </Form.Item>
                    </Form>
                  ) : null}
                </div>
                <div className="astro-preview-section">
                  <Typography.Title level={5}>Field assumptions</Typography.Title>
                  <Table
                    size="small"
                    pagination={false}
                    rowKey="field"
                    columns={astroAssumptionColumns}
                    dataSource={astroPreviewPlan.assumptions}
                    scroll={{ x: 760 }}
                  />
                </div>
                <div className="astro-preview-section">
                  <Typography.Title level={5}>Pair payload</Typography.Title>
                  <pre className="astro-preview-json">{jsonBlock(astroPreviewPlan.pair)}</pre>
                </div>
                <div className="astro-preview-section">
                  <Typography.Title level={5}>SDK payload</Typography.Title>
                  <pre className="astro-preview-json">{jsonBlock(astroPreviewPlan.sdk_payload)}</pre>
                </div>
              </>
            ) : null}
          </Space>
        ) : null}
      </Modal>
    </div>
  );
}
