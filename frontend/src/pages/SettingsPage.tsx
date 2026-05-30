import { DeleteOutlined, ReloadOutlined, SaveOutlined } from "@ant-design/icons";
import {
  Alert,
  Button,
  Checkbox,
  Form,
  Input,
  InputNumber,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
  message
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useEffect, useState } from "react";

import {
  createAlertRule,
  deleteAlertRule,
  getAlertMessageTemplate,
  getAstroCardSettings,
  getAstroStatus,
  getLivePilotPreview,
  getLivePilotSettings,
  getRiskSettings,
  getServiceControlStatus,
  listAlertRules,
  restartServiceControl,
  saveDashboardPassword,
  updateAlertMessageTemplate,
  updateAstroCardSettings,
  updateLivePilotSettings,
  updateRiskSettings
} from "../api/client";
import type {
  AlertMessageTemplateSettings,
  AlertRule,
  AstroCardSettings,
  AstroSdkStatus,
  LivePilotPreview,
  LivePilotPreviewItem,
  LivePilotSettings,
  RiskSettings,
  ServiceControlStatus
} from "../api/types";
import { alertRuleFieldHelp, alertRuleGuide, alertSeverityOptions, alertTypeOptions } from "../constants/alertRules";
import { defaultHiddenRiskLabels, riskLabelOptions } from "../constants/riskLabels";
import { PhonePriceAlertsPanel } from "./PhonePriceAlertsPanel";

type AlertRuleFormValues = AlertRule & {
  min_volume_24h_k?: number;
};

type ServiceName = "frontend" | "backend";

const defaultRule: AlertRule = {
  name: "",
  enabled: true,
  types: ["SF", "FF", "SS"],
  include_exchanges: [],
  exclude_exchanges: [],
  include_symbols: [],
  exclude_symbols: [],
  min_open_spread_pct: 0.5,
  min_fee_adjusted_open_pct: 0.25,
  min_volume_24h_usdt: 1000000,
  max_data_age_seconds: 600,
  excluded_risk_labels: defaultHiddenRiskLabels,
  consecutive_hits: 3,
  cooldown_seconds: 300,
  severity: "warning"
};

const defaultAlertMessageTemplate: AlertMessageTemplateSettings = {
  include_trigger_summary: true,
  include_rule_details: true,
  include_pair: true,
  include_spread: true,
  include_funding: true,
  include_volume: true,
  include_risk: true,
  include_observations: true,
  include_dashboard_link: true,
  suppress_when_card_conditions_fail: true,
  observation_limit: 5
};

const defaultRiskSettings: RiskSettings = {
  min_volume_24h_usdt: 1_000_000,
  stale_after_seconds: 30,
  huge_spread_pct: 10,
  wide_spread_pct: 3,
  mark_index_deviation_pct: 1,
  funding_against_pct: 0.01,
  signal_slippage_buffer_pct: 0.05,
  min_effective_open_pct: 0.05,
  max_open_spread_decay_pct: 60,
  signal_validation_notional_usdt: 1000,
  orderbook_depth_safety_multiple: 2,
  min_top_of_book_depth_usdt: 0,
  signal_strategy_notes: "",
  ticker_collision_symbols: ["AIUSDT", "UPUSDT", "LABUSDT"],
  excluded_symbols: [],
  ignored_exchanges: []
};

const defaultLivePilotSettings: LivePilotSettings = {
  enabled: false,
  max_symbols: 10,
  notional_per_symbol_usdt: 100,
  min_next_funding_edge_pct: -0.05,
  prefer_hyperliquid: true,
  exclude_ss: true,
  create_cards_enabled: true
};

const alertTemplateOptions: Array<{
  name: Exclude<keyof AlertMessageTemplateSettings, "observation_limit">;
  label: string;
  description: string;
}> = [
  { name: "include_trigger_summary", label: "触发摘要", description: "规则名、等级和触发提示" },
  { name: "include_rule_details", label: "规则参数", description: "阈值、交易所、标的和冷却参数" },
  { name: "include_pair", label: "价差对", description: "标的、买卖方向和两侧交易所" },
  { name: "include_spread", label: "价差信息", description: "开仓、平仓、净估算和综合开仓" },
  { name: "include_funding", label: "资金费率", description: "当前、预测资金费率、周期净差和结算周期" },
  { name: "include_volume", label: "成交额", description: "买入侧和卖出侧 24h 成交额" },
  { name: "include_risk", label: "风险标签", description: "过滤命中的风险标签" },
  { name: "include_observations", label: "连续监测", description: "最近几轮命中的价差和周期资金费差" },
  { name: "include_dashboard_link", label: "Dashboard 链接", description: "消息末尾追加面板地址" },
  {
    name: "suppress_when_card_conditions_fail",
    label: "建卡失败不通知",
    description: "最新信号或盘口深度不满足建卡条件时，只写告警历史，不发飞书"
  }
];

function ruleDefaultsForRisk(settings: RiskSettings): AlertRule {
  return {
    ...defaultRule,
    min_volume_24h_usdt: settings.min_volume_24h_usdt
  };
}

function ruleToForm(rule: AlertRule): AlertRuleFormValues {
  return {
    ...rule,
    min_volume_24h_k: Math.round(rule.min_volume_24h_usdt / 1000)
  };
}

function ruleFromForm(values: AlertRuleFormValues, defaults: AlertRule): AlertRule {
  const { min_volume_24h_k: minVolumeK, ...rule } = values;
  return {
    ...defaults,
    ...rule,
    exclude_symbols: [],
    min_volume_24h_usdt: (minVolumeK ?? 0) * 1000
  };
}

const exchangeOptions = ["binance", "okx", "bybit", "gate", "bitget", "htx", "aster", "hyperliquid"].map((item) => ({
  label: item,
  value: item
}));
const serviceNames: ServiceName[] = ["frontend", "backend"];
const serviceLabels: Record<ServiceName, string> = {
  frontend: "前端",
  backend: "后端"
};
const riskSelectOptions = riskLabelOptions.map((item) => ({
  label: `${item.label} (${item.value})`,
  value: item.value
}));
const opportunityTypeLabels: Record<string, string> = {
  SF: "SF 现货-合约",
  FF: "FF 合约-合约",
  SS: "SS 现货-现货"
};

function formatPct(value: number): string {
  return `${value.toFixed(3)}%`;
}

function formatCompactUsdt(value?: number | null): string {
  if (value === null || value === undefined) {
    return "-";
  }
  if (value >= 1_000_000) {
    return `${(value / 1_000_000).toFixed(2)}M`;
  }
  if (value >= 1_000) {
    return `${(value / 1_000).toFixed(1)}K`;
  }
  return `${value.toFixed(0)}`;
}

function serviceCanRestart(status: ServiceControlStatus | null, service: ServiceName): boolean {
  if (!status?.enabled) {
    return false;
  }
  const detail = (status.details ?? []).find((item) => item.name === service);
  if (detail) {
    return detail.available;
  }
  return (status.services ?? []).includes(service);
}

function riskToForm(settings: RiskSettings): RiskSettings {
  const normalized = {
    ...defaultRiskSettings,
    ...settings
  };
  return {
    ...normalized,
    min_volume_24h_k: Math.round(normalized.min_volume_24h_usdt / 1000),
    excluded_symbols: normalized.excluded_symbols ?? [],
    ignored_exchanges: normalized.ignored_exchanges ?? []
  };
}

function riskFromForm(values: RiskSettings): RiskSettings {
  const { min_volume_24h_k: minVolumeK, ...settings } = values;
  return {
    ...defaultRiskSettings,
    ...settings,
    min_volume_24h_usdt: (minVolumeK ?? 0) * 1000
  };
}

function normalizeAlertTemplate(values?: Partial<AlertMessageTemplateSettings>): AlertMessageTemplateSettings {
  return {
    ...defaultAlertMessageTemplate,
    ...(values ?? {})
  };
}

function normalizeLivePilot(values?: Partial<LivePilotSettings>): LivePilotSettings {
  return {
    ...defaultLivePilotSettings,
    ...(values ?? {})
  };
}

function buildAlertTemplatePreview(template: AlertMessageTemplateSettings): string {
  const blocks: string[] = [];
  if (template.include_trigger_summary) {
    blocks.push("【告警触发】\n规则：FF 价差\n等级：warning（普通告警）");
  }
  if (template.include_rule_details) {
    blocks.push("【规则参数】\n套利类型：FF\n开仓阈值：>= 0.500%\n综合开仓阈值：>= 0.250%");
  }
  const snapshotLines: string[] = [];
  if (template.include_pair) {
    snapshotLines.push(
      "标的：BTCUSDT / FF",
      "价差对：BTCUSDT | binance future -> okx future",
      "方向：买入 binance future BTCUSDT，卖出 okx future BTCUSDT"
    );
  }
  if (template.include_spread) {
    snapshotLines.push("价差：开仓 0.800% / 平仓 0.500%", "净估算：0.600%", "综合开仓：0.610%");
  }
  if (template.include_funding) {
    snapshotLines.push(
      "资金费率差（周期）：当前 -0.03% / 预测 0.01%",
      "下一次结算：08:00 / 08:00",
      "结算周期：8h / 8h"
    );
  }
  if (template.include_volume) {
    snapshotLines.push("成交额：买入侧 10000K USDT / 卖出侧 12000K USDT");
  }
  if (template.include_risk) {
    snapshotLines.push("风险：FUNDING_AGAINST");
  }
  if (snapshotLines.length > 0) {
    blocks.push(`【行情快照】\n${snapshotLines.join("\n")}`);
  }
  if (template.include_observations) {
    blocks.push(
      `【连续监测】\n1. 01:59:44 | 价差 0.720% | 净估算 0.520% | 资金差（周期） 0.01% | 综合 0.530%\n最多显示 ${template.observation_limit} 轮`
    );
  }
  if (template.include_dashboard_link) {
    blocks.push("Dashboard: https://your-domain.example");
  }
  if (template.suppress_when_card_conditions_fail) {
    blocks.push("建卡条件过滤：不满足时仅保留告警历史，不发送飞书。");
  }
  return blocks.join("\n\n") || "至少保留一个字段，避免告警内容为空。";
}

export function SettingsPage() {
  const [riskForm] = Form.useForm<RiskSettings>();
  const [ruleForm] = Form.useForm<AlertRuleFormValues>();
  const [templateForm] = Form.useForm<AlertMessageTemplateSettings>();
  const [astroCardForm] = Form.useForm<AstroCardSettings>();
  const [livePilotForm] = Form.useForm<LivePilotSettings>();
  const [rules, setRules] = useState<AlertRule[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [ruleDefaults, setRuleDefaults] = useState<AlertRule>(defaultRule);
  const [alertTemplatePreview, setAlertTemplatePreview] =
    useState<AlertMessageTemplateSettings>(defaultAlertMessageTemplate);
  const [serviceControl, setServiceControl] = useState<ServiceControlStatus | null>(null);
  const [serviceControlError, setServiceControlError] = useState("");
  const [restartingService, setRestartingService] = useState<ServiceName | null>(null);
  const [astroStatus, setAstroStatus] = useState<AstroSdkStatus | null>(null);
  const [astroStatusError, setAstroStatusError] = useState("");
  const [livePilotPreview, setLivePilotPreview] =
    useState<LivePilotSettings>(defaultLivePilotSettings);
  const [livePilotSelection, setLivePilotSelection] = useState<LivePilotPreview | null>(null);

  const load = async () => {
    setLoading(true);
    setError("");
    setServiceControlError("");
    setAstroStatusError("");
    try {
      const serviceControlRequest = getServiceControlStatus().catch((exc) => {
        setServiceControlError(exc instanceof Error ? exc.message : String(exc));
        return null;
      });
      const astroStatusRequest = getAstroStatus().catch((exc) => {
        setAstroStatusError(exc instanceof Error ? exc.message : String(exc));
        return null;
      });
      const [risk, nextRules, nextServiceControl, alertTemplate, astroCard, livePilot, pilotSelection, nextAstroStatus] = await Promise.all([
        getRiskSettings(),
        listAlertRules(),
        serviceControlRequest,
        getAlertMessageTemplate(),
        getAstroCardSettings(),
        getLivePilotSettings(),
        getLivePilotPreview(),
        astroStatusRequest
      ]);
      const nextRuleDefaults = ruleDefaultsForRisk(risk);
      const nextAlertTemplate = normalizeAlertTemplate(alertTemplate);
      const nextLivePilot = normalizeLivePilot(livePilot);
      riskForm.setFieldsValue(riskToForm(risk));
      ruleForm.setFieldsValue(ruleToForm(nextRuleDefaults));
      templateForm.setFieldsValue(nextAlertTemplate);
      astroCardForm.setFieldsValue(astroCard);
      livePilotForm.setFieldsValue(nextLivePilot);
      setRuleDefaults(nextRuleDefaults);
      setAlertTemplatePreview(nextAlertTemplate);
      setLivePilotPreview(nextLivePilot);
      setLivePilotSelection(pilotSelection);
      setRules(nextRules);
      setServiceControl(nextServiceControl);
      setAstroStatus(nextAstroStatus);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    ruleForm.setFieldsValue(ruleToForm(defaultRule));
    templateForm.setFieldsValue(defaultAlertMessageTemplate);
    livePilotForm.setFieldsValue(defaultLivePilotSettings);
    void load();
  }, []);

  const saveRisk = async () => {
    const values = await riskForm.validateFields();
    const saved = await updateRiskSettings(riskFromForm(values));
    const nextRuleDefaults = ruleDefaultsForRisk(saved);
    riskForm.setFieldsValue(riskToForm(saved));
    ruleForm.setFieldsValue(ruleToForm(nextRuleDefaults));
    setRuleDefaults(nextRuleDefaults);
    message.success("已保存");
  };

  const saveAlertTemplate = async () => {
    const values = normalizeAlertTemplate(await templateForm.validateFields());
    const saved = normalizeAlertTemplate(await updateAlertMessageTemplate(values));
    templateForm.setFieldsValue(saved);
    setAlertTemplatePreview(saved);
    message.success("告警模板已保存");
  };

  const saveAstroCardDefaults = async () => {
    const values = await astroCardForm.validateFields();
    const saved = await updateAstroCardSettings(values);
    astroCardForm.setFieldsValue(saved);
    message.success("Astro card defaults saved");
  };

  const saveLivePilot = async () => {
    const values = normalizeLivePilot(await livePilotForm.validateFields());
    const saved = normalizeLivePilot(await updateLivePilotSettings(values));
    livePilotForm.setFieldsValue(saved);
    setLivePilotPreview(saved);
    setLivePilotSelection(await getLivePilotPreview());
    message.success("实盘灰度已保存");
  };

  const createRule = async () => {
    const values = await ruleForm.validateFields();
    const saved = await createAlertRule(ruleFromForm(values, ruleDefaults));
    setRules((current) => [saved, ...current]);
    ruleForm.setFieldsValue(ruleToForm(ruleDefaults));
    message.success("已新增");
  };

  const removeRule = async (rule: AlertRule) => {
    if (!rule.id) {
      return;
    }
    await deleteAlertRule(rule.id);
    setRules((current) => current.filter((item) => item.id !== rule.id));
  };

  const restartService = async (service: ServiceName) => {
    setRestartingService(service);
    try {
      const result = await restartServiceControl(service);
      message.success(result.message ?? `${serviceLabels[service]}重启已提交`);
    } catch (exc) {
      message.error(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setRestartingService(null);
    }
  };

  const livePilotBudget = livePilotPreview.max_symbols * livePilotPreview.notional_per_symbol_usdt;
  const livePilotRuntimeWarnings = [
    astroStatus?.dry_run_only ? "Astro dry-run 当前开启，保存配置后仍不会写入实盘卡片。" : "",
    astroStatus && !astroStatus.configured ? "Astro SDK 未配置，无法提交卡片。" : "",
    astroStatusError ? `Astro 状态读取失败：${astroStatusError}` : ""
  ].filter(Boolean);

  const columns: ColumnsType<AlertRule> = [
    { title: "规则", dataIndex: "name" },
    { title: "类型", dataIndex: "types", render: (types: string[]) => types.join(",") },
    { title: "开仓阈值", dataIndex: "min_open_spread_pct", render: (value: number) => `${value}%` },
    { title: "综合阈值", dataIndex: "min_fee_adjusted_open_pct", render: (value: number) => `${value}%` },
    { title: "连续命中", dataIndex: "consecutive_hits" },
    { title: "冷却", dataIndex: "cooldown_seconds", render: (value: number) => `${value}s` },
    {
      title: "",
      width: 72,
      render: (_, row) => (
        <Button icon={<DeleteOutlined />} type="text" danger onClick={() => void removeRule(row)} />
      )
    }
  ];
  const livePilotColumns: ColumnsType<LivePilotPreviewItem> = [
    {
      title: "标的",
      dataIndex: "symbol",
      width: 132,
      render: (value: string, row) => (
        <Space size={4} wrap>
          <Typography.Text strong>{value}</Typography.Text>
          {row.uses_hyperliquid ? <Tag color="cyan">Hyper</Tag> : null}
        </Space>
      )
    },
    {
      title: "类型",
      dataIndex: "type",
      width: 116,
      render: (value: string) => <Tag>{opportunityTypeLabels[value] ?? value}</Tag>
    },
    { title: "路线", dataIndex: "route", ellipsis: true },
    { title: "综合", dataIndex: "combined_open_edge_pct", width: 88, render: formatPct },
    { title: "价差净值", dataIndex: "fee_adjusted_open_pct", width: 96, render: formatPct },
    { title: "下周期资金", dataIndex: "next_funding_edge_pct", width: 108, render: formatPct },
    { title: "资金", dataIndex: "notional_usdt", width: 88, render: (value: number) => `${value}U` },
    { title: "24h量", dataIndex: "volume_24h_usdt", width: 92, render: formatCompactUsdt },
    {
      title: "风险",
      dataIndex: "risk_labels",
      width: 160,
      render: (labels: string[]) => labels.length > 0 ? labels.join(", ") : "-"
    }
  ];

  return (
    <div className="page settings-grid">
      {error ? <Alert type="error" message={error} showIcon /> : null}
      <section className="panel">
        <Typography.Title level={4}>面板访问</Typography.Title>
        <Space.Compact className="password-row">
          <Input.Password
            placeholder="Dashboard Password"
            onChange={(event) => saveDashboardPassword(event.target.value)}
          />
          <Button icon={<SaveOutlined />} onClick={() => void load()}>
            应用
          </Button>
        </Space.Compact>
        <div className="service-control">
          <Typography.Title level={5}>服务控制</Typography.Title>
          <Alert
            type={serviceControl?.enabled ? "info" : "warning"}
            showIcon
            message={serviceControl?.enabled ? "当前环境允许重启前端和后端服务" : "服务控制未启用"}
            description={
              serviceControlError ||
              serviceControl?.message ||
              "仅建议在本地测试环境或受控网络中开启。"
            }
          />
          <Space wrap className="service-control-actions">
            {serviceNames.map((service) => (
              <Button
                key={service}
                danger
                icon={<ReloadOutlined />}
                aria-label={`重启${serviceLabels[service]}`}
                onClick={() => void restartService(service)}
                disabled={!serviceCanRestart(serviceControl, service)}
                loading={restartingService === service}
              >
                重启{serviceLabels[service]}
              </Button>
            ))}
          </Space>
          <div className="service-control-list">
            {(serviceControl?.details ?? []).map((item) => (
              <Typography.Text key={item.name} type="secondary">
                {serviceLabels[item.name as ServiceName] ?? item.name}：
                {item.available ? item.container_name ?? item.container_id ?? item.state ?? "可用" : "不可用"}
              </Typography.Text>
            ))}
          </div>
        </div>
      </section>
      <section className="panel">
        <Typography.Title level={4}>风险参数</Typography.Title>
        <Form form={riskForm} layout="vertical" disabled={loading} onFinish={saveRisk}>
          <div className="form-grid">
            <Form.Item label="低成交额阈值 (LOW_VOLUME)" name="min_volume_24h_k" rules={[{ required: true }]}>
              <InputNumber min={0} step={100} suffix="K" className="wide-input" />
            </Form.Item>
            <Form.Item label="数据过期秒数 (STALE_DATA)" name="stale_after_seconds" rules={[{ required: true }]}>
              <InputNumber min={5} className="wide-input" />
            </Form.Item>
            <Form.Item label="异常大价差阈值 (HUGE_SPREAD_VERIFY)" name="huge_spread_pct" rules={[{ required: true }]}>
              <InputNumber min={0} step={0.1} suffix="%" className="wide-input" />
            </Form.Item>
            <Form.Item label="WIDE_SPREAD 开平价差宽度" name="wide_spread_pct" rules={[{ required: true }]}>
              <InputNumber min={0} step={0.1} suffix="%" className="wide-input" />
            </Form.Item>
            <Form.Item label="标记/指数偏离阈值 (MARK_INDEX_DEVIATION)" name="mark_index_deviation_pct" rules={[{ required: true }]}>
              <InputNumber min={0} step={0.1} suffix="%" className="wide-input" />
            </Form.Item>
            <Form.Item label="资金费率逆风阈值 (FUNDING_AGAINST)" name="funding_against_pct" rules={[{ required: true }]}>
              <InputNumber min={0} step={0.01} suffix="%" className="wide-input" />
            </Form.Item>
            <div className="form-grid-heading">
              <Typography.Title level={5}>Signal strategy（信号策略）</Typography.Title>
              <Typography.Paragraph type="secondary">
                信号策略用于判断告警机会是否真的适合创建 Astro 卡片。系统会在创建卡片前拉取两边交易所的多档 order book，
                按计划仓位金额模拟买入侧 asks 和卖出侧 bids 的可成交 VWAP，避免只看瞬时最优价导致价差一买就消失。
              </Typography.Paragraph>
              <Typography.Paragraph type="secondary">
                最小验证金额是盘口深度校验的底线，默认 1000 USDT；实际校验金额会取卡片仓位价值、手动填写仓位价值、
                最小验证金额三者中的较大值。若盘口不足、成交后价差不够、或价差衰减太快，系统会跳过创建卡片。
              </Typography.Paragraph>
            </div>
            <Form.Item label="信号滑点缓冲百分比 (Signal slippage buffer pct)" name="signal_slippage_buffer_pct" rules={[{ required: true }]}>
              <InputNumber min={0} step={0.01} suffix="%" className="wide-input" />
            </Form.Item>
            <Form.Item label="最低有效开仓收益率 (Minimum effective open pct)" name="min_effective_open_pct" rules={[{ required: true }]}>
              <InputNumber min={0} step={0.01} suffix="%" className="wide-input" />
            </Form.Item>
            <Form.Item label="最大开仓价差衰减百分比 (Max open spread decay pct)" name="max_open_spread_decay_pct" rules={[{ required: true }]}>
              <InputNumber min={0} max={100} step={1} suffix="%" className="wide-input" />
            </Form.Item>
            <Form.Item label="最小盘口验证金额 USDT (Minimum validation notional USDT)" name="signal_validation_notional_usdt" rules={[{ required: true }]}>
              <InputNumber min={0} step={1} className="wide-input" />
            </Form.Item>
            <Form.Item label="盘口深度安全倍数 (Depth safety multiple)" name="orderbook_depth_safety_multiple" rules={[{ required: true }]}>
              <InputNumber min={0} step={0.5} className="wide-input" />
            </Form.Item>
            <Form.Item label="最小顶档盘口深度 USDT (Minimum top-of-book depth USDT)" name="min_top_of_book_depth_usdt" rules={[{ required: true }]}>
              <InputNumber min={0} step={10} className="wide-input" />
            </Form.Item>
            <Form.Item
              label="信号策略备注 / 后续自定义规则 (Signal strategy notes)"
              name="signal_strategy_notes"
              className="form-grid-wide"
            >
              <Input.TextArea rows={4} />
            </Form.Item>
          </div>
          <div className="risk-help">
            {riskLabelOptions.map((item) => (
              <Typography.Text key={item.value} type="secondary">
                {item.label} ({item.value})：{item.description}
              </Typography.Text>
            ))}
          </div>
          <Form.Item label="同名风险标的 (SAME_TICKER_RISK)" name="ticker_collision_symbols">
            <Select mode="tags" tokenSeparators={[",", " "]} />
          </Form.Item>
          <Form.Item label="黑名单标的" name="excluded_symbols">
            <Select mode="tags" tokenSeparators={[",", " "]} placeholder="TRADOORUSDT" />
          </Form.Item>
          <Form.Item label="忽略交易所" name="ignored_exchanges">
            <Select mode="multiple" allowClear options={exchangeOptions} />
          </Form.Item>
          <Button type="primary" htmlType="submit" icon={<SaveOutlined />}>
            保存风险参数
          </Button>
        </Form>
      </section>
      <section className="panel">
        <Typography.Title level={4}>Astro card defaults</Typography.Title>
        <Form form={astroCardForm} layout="vertical" disabled={loading} onFinish={saveAstroCardDefaults}>
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
            <Form.Item label="Close buffer pct" name="close_position_buffer_pct" rules={[{ required: true }]}>
              <InputNumber min={0} step={0.01} suffix="%" className="wide-input" />
            </Form.Item>
            <Form.Item label="Unfavorable funding weight" name="unfavorable_funding_weight" rules={[{ required: true }]}>
              <InputNumber min={0} step={0.1} className="wide-input" />
            </Form.Item>
            <Form.Item label="Close floor pct" name="close_position_floor_pct" rules={[{ required: true }]}>
              <InputNumber min={0} step={0.01} suffix="%" className="wide-input" />
            </Form.Item>
          </div>
          <Button type="primary" htmlType="submit" icon={<SaveOutlined />}>
            Save Astro card defaults
          </Button>
        </Form>
      </section>
      <section className="panel panel-wide">
        <Typography.Title level={4}>实盘灰度</Typography.Title>
        <Alert
          className="rule-guide"
          type={livePilotPreview.enabled ? "warning" : "info"}
          showIcon
          message={livePilotPreview.enabled ? "Live Pilot 已配置为启用" : "Live Pilot 未启用"}
          description={
            livePilotPreview.enabled
              ? "告警循环会先从实时机会中选最多 10 个标的，同标的只保留一个路线；默认优先 Hyper，跳过 SS、强负资金和风险候选，然后按综合开仓收益排序。"
              : "开启后用于小资金实盘灰度，不影响手动 Astro 建卡的安全默认。"
          }
        />
        {livePilotRuntimeWarnings.length > 0 ? (
          <Alert
            className="rule-guide"
            type="warning"
            showIcon
            message="运行状态提示"
            description={livePilotRuntimeWarnings.join(" ")}
          />
        ) : null}
        <Form
          form={livePilotForm}
          layout="vertical"
          disabled={loading}
          onFinish={saveLivePilot}
          onValuesChange={(_, values) => setLivePilotPreview(normalizeLivePilot(values))}
        >
          <div className="live-pilot-metrics">
            <div>
              <Typography.Text type="secondary">最多标的</Typography.Text>
              <Typography.Title level={5}>{livePilotPreview.max_symbols}</Typography.Title>
            </div>
            <div>
              <Typography.Text type="secondary">每标的资金</Typography.Text>
              <Typography.Title level={5}>{livePilotPreview.notional_per_symbol_usdt} USDT</Typography.Title>
            </div>
            <div>
              <Typography.Text type="secondary">总预算</Typography.Text>
              <Typography.Title level={5}>{livePilotBudget} USDT</Typography.Title>
            </div>
            <div>
              <Typography.Text type="secondary">资金过滤</Typography.Text>
              <Typography.Title level={5}>{`>= ${livePilotPreview.min_next_funding_edge_pct}%`}</Typography.Title>
            </div>
          </div>
          <div className="form-grid">
            <Form.Item label="启用实盘灰度" name="enabled" valuePropName="checked">
              <Switch />
            </Form.Item>
            <Form.Item label="卡片默认开启" name="create_cards_enabled" valuePropName="checked">
              <Switch />
            </Form.Item>
            <Form.Item
              label="屏蔽 SS（现货-现货）"
              name="exclude_ss"
              valuePropName="checked"
              help="默认开启；开启后 SS 不进入实盘灰度选标和自动建卡。"
            >
              <Switch />
            </Form.Item>
            <Form.Item label="最多标的数" name="max_symbols" rules={[{ required: true }]}>
              <InputNumber min={1} max={100} step={1} className="wide-input" />
            </Form.Item>
            <Form.Item label="每标的资金 USDT" name="notional_per_symbol_usdt" rules={[{ required: true }]}>
              <InputNumber min={0.01} step={1} className="wide-input" />
            </Form.Item>
            <Form.Item
              label="强负资金跳过阈值"
              name="min_next_funding_edge_pct"
              rules={[{ required: true }]}
              help="下一资金周期净资金差低于该值的机会不会进入本次灰度。"
            >
              <InputNumber step={0.01} suffix="%" className="wide-input" />
            </Form.Item>
            <Form.Item label="Hyper 优先" name="prefer_hyperliquid" valuePropName="checked">
              <Switch />
            </Form.Item>
          </div>
          <div className="rule-note">
            实盘灰度只影响告警自动创建 Astro 卡片：同一标的多条告警时先按套利类型和风控过滤，再选 Hyper 路线与综合开仓收益；启用后盘口验证金额使用每标的资金。
          </div>
          <div className="live-pilot-preview">
            <div className="live-pilot-preview-head">
              <Space size={8} wrap>
                <Typography.Text strong>
                  当前候选 {livePilotSelection?.selected_symbols ?? 0}/{livePilotSelection?.eligible_symbols ?? 0}
                </Typography.Text>
                <Tag color="blue">强负资金跳过 {livePilotSelection?.skipped_negative_funding ?? 0}</Tag>
                <Tag color="purple">类型跳过 {livePilotSelection?.skipped_type ?? 0}</Tag>
                <Tag color="orange">风险跳过 {livePilotSelection?.skipped_risk ?? 0}</Tag>
                <Tag color="green">预算 {livePilotSelection?.budget_usdt ?? 0} USDT</Tag>
              </Space>
              <Typography.Text type="secondary">
                实时机会 {livePilotSelection?.total_opportunities ?? 0}
              </Typography.Text>
            </div>
            <Table
              columns={livePilotColumns}
              dataSource={livePilotSelection?.items ?? []}
              rowKey="opportunity_id"
              pagination={false}
              size="small"
              tableLayout="fixed"
            />
          </div>
          <Button type="primary" htmlType="submit" icon={<SaveOutlined />}>
            保存实盘灰度
          </Button>
        </Form>
      </section>
      <section className="panel panel-wide">
        <Typography.Title level={4}>告警内容模板</Typography.Title>
        <Alert
          className="rule-guide"
          type="info"
          showIcon
          message="全局模板"
          description="这里控制飞书告警和新告警历史里展示哪些内容。告警规则仍然只负责判断什么时候触发。"
        />
        <Form
          form={templateForm}
          layout="vertical"
          disabled={loading}
          onFinish={saveAlertTemplate}
          onValuesChange={(_, values) => setAlertTemplatePreview(normalizeAlertTemplate(values))}
        >
          <div className="template-grid">
            <div className="template-options">
              {alertTemplateOptions.map((option) => (
                <Form.Item
                  key={option.name}
                  name={option.name}
                  valuePropName="checked"
                  className="template-option"
                >
                  <Checkbox aria-label={option.label}>
                    <span className="template-option-label">{option.label}</span>
                    <span className="template-option-desc">{option.description}</span>
                  </Checkbox>
                </Form.Item>
              ))}
              <Form.Item
                label="连续监测最多显示轮数"
                name="observation_limit"
                rules={[{ required: true }]}
                className="template-limit"
              >
                <InputNumber min={1} max={20} className="wide-input" />
              </Form.Item>
            </div>
            <div className="template-preview">
              <Typography.Text strong>消息预览</Typography.Text>
              <pre>{buildAlertTemplatePreview(alertTemplatePreview)}</pre>
            </div>
          </div>
          <Button type="primary" htmlType="submit" icon={<SaveOutlined />}>
            保存告警模板
          </Button>
        </Form>
      </section>
      <section className="panel">
        <Typography.Title level={4}>新增告警规则</Typography.Title>
        <Alert className="rule-guide" type="info" showIcon message="规则说明" description={alertRuleGuide} />
        <Form
          form={ruleForm}
          layout="vertical"
          disabled={loading}
          initialValues={ruleToForm(ruleDefaults)}
          onFinish={createRule}
        >
          <div className="form-grid">
            <Form.Item label="规则名称" name="name" rules={[{ required: true }]} help={alertRuleFieldHelp.name}>
              <Input />
            </Form.Item>
            <Form.Item label="启用" name="enabled" valuePropName="checked" help={alertRuleFieldHelp.enabled}>
              <Switch />
            </Form.Item>
            <Form.Item label="套利类型" name="types" rules={[{ required: true }]} help={alertRuleFieldHelp.types}>
              <Select mode="multiple" options={alertTypeOptions} />
            </Form.Item>
            <Form.Item
              label="包含交易所"
              name="include_exchanges"
              help={alertRuleFieldHelp.include_exchanges}
            >
              <Select mode="multiple" allowClear options={exchangeOptions} />
            </Form.Item>
            <Form.Item
              label="排除交易所"
              name="exclude_exchanges"
              help={alertRuleFieldHelp.exclude_exchanges}
            >
              <Select mode="multiple" allowClear options={exchangeOptions} />
            </Form.Item>
            <Form.Item
              label="包含标的"
              name="include_symbols"
              help={alertRuleFieldHelp.include_symbols}
            >
              <Select mode="tags" tokenSeparators={[",", " "]} placeholder="BTCUSDT, ETHUSDT" />
            </Form.Item>
            <div className="rule-note">{alertRuleFieldHelp.exclude_symbols}</div>
            <Form.Item
              label="开仓阈值"
              name="min_open_spread_pct"
              rules={[{ required: true }]}
              help={alertRuleFieldHelp.min_open_spread_pct}
            >
              <InputNumber min={0} step={0.1} suffix="%" className="wide-input" />
            </Form.Item>
            <Form.Item
              label="综合开仓阈值"
              name="min_fee_adjusted_open_pct"
              rules={[{ required: true }]}
              help={alertRuleFieldHelp.min_fee_adjusted_open_pct}
            >
              <InputNumber min={0} step={0.1} suffix="%" className="wide-input" />
            </Form.Item>
            <Form.Item
              label="最低成交额 (K)"
              name="min_volume_24h_k"
              rules={[{ required: true }]}
              help={alertRuleFieldHelp.min_volume_24h_usdt}
            >
              <InputNumber min={0} step={100} suffix="K" className="wide-input" />
            </Form.Item>
            <Form.Item
              label="连续命中"
              name="consecutive_hits"
              rules={[{ required: true }]}
              help={alertRuleFieldHelp.consecutive_hits}
            >
              <InputNumber min={1} className="wide-input" />
            </Form.Item>
            <Form.Item
              label="冷却秒数"
              name="cooldown_seconds"
              rules={[{ required: true }]}
              help={alertRuleFieldHelp.cooldown_seconds}
            >
              <InputNumber min={0} className="wide-input" />
            </Form.Item>
            <Form.Item label="等级" name="severity" rules={[{ required: true }]} help={alertRuleFieldHelp.severity}>
              <Select options={alertSeverityOptions} />
            </Form.Item>
          </div>
          <Form.Item label="排除风险标签" name="excluded_risk_labels">
            <Select mode="multiple" options={riskSelectOptions} />
          </Form.Item>
          <Button type="primary" htmlType="submit">
            新增规则
          </Button>
        </Form>
      </section>
      <section className="panel panel-wide">
        <Typography.Title level={4}>告警规则</Typography.Title>
        <Table
          columns={columns}
          dataSource={rules}
          loading={loading}
          rowKey={(row) => row.id ?? row.name}
          pagination={false}
          size="middle"
        />
      </section>
      <PhonePriceAlertsPanel />
    </div>
  );
}
