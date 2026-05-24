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

import { createAstroCard, getRiskSettings, previewAstroPair, updateRiskSettings } from "../api/client";
import type {
  AstroActionResult,
  AstroCardCreateRequest,
  AstroFieldAssumption,
  AstroPairPlan,
  ExchangePollState,
  Opportunity,
  OpportunityFilters,
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
          <Statistic title="Exchange errors" value={Object.keys(errors).length} />
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
          message={Object.entries(errors)
            .slice(0, 4)
            .map(([key, value]) => `${key}: ${value}`)
            .join(" | ")}
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
      />
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
