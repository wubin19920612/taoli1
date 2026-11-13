import { LoginDevices } from "../components/LoginDevices";
import { CloseOutlined, DeleteOutlined, EditOutlined, PlusOutlined, ReloadOutlined, SaveOutlined } from "@ant-design/icons";
import {
  Alert,
  Button,
  Checkbox,
  Form,
  Input,
  InputNumber,
  Segmented,
  Select,
  Space,
  Switch,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
  message
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useEffect, useRef, useState } from "react";

import {
  createAlertRule,
  deleteAlertRule,
  getAstroAutomationSettings,
  getAlertMessageTemplate,
  getAstroCardSettings,
  getAstroStatus,
  getRiskSettings,
  getPremiumIndexAlertStatus,
  getServiceControlStatus,
  listAlertRules,
  restartServiceControl,
  updateAlertMessageTemplate,
  updateAstroAutomationSettings,
  updateAstroCardSettings,
  updateAlertRule,
  updateRiskSettings
} from "../api/client";
import type {
  AlertMessageTemplateSettings,
  AlertRule,
  AstroAutomationSettings,
  AstroCardSettings,
  AstroSdkStatus,
  RiskSettings,
  ServiceControlStatus,
  SymbolAlias
} from "../api/types";
import { AstroCardVariantSelector } from "../components/AstroCardVariantSelector";
import { alertRuleFieldHelp, alertRuleGuide, alertSeverityOptions, alertTypeOptions } from "../constants/alertRules";
import { defaultHiddenRiskLabels, riskLabelOptions } from "../constants/riskLabels";
import { PhonePriceAlertsPanel } from "./PhonePriceAlertsPanel";
import { MarketIdentityPanel } from "../components/MarketIdentityPanel";
import { AlertHistoryPage } from "./AlertHistoryPage";

type AlertRuleFormValues = AlertRule & {
  min_volume_24h_k?: number;
};

type ServiceName = "frontend" | "backend";

const defaultRule: AlertRule = {
  name: "",
  enabled: true,
  types: ["SF", "FF", "SS"],
  suppress_sf_negative_funding: true,
  include_exchanges: [],
  exclude_exchanges: [],
  include_symbols: [],
  exclude_symbols: [],
  min_open_spread_pct: 0.5,
  favorable_funding_open_spread_pct: 0.9,
  min_fee_adjusted_open_pct: 0.25,
  min_volume_24h_usdt: 1000000,
  max_data_age_seconds: 600,
  excluded_risk_labels: defaultHiddenRiskLabels,
  consecutive_hits: 3,
  cooldown_seconds: 300,
  severity: "warning"
};

const defaultAlertMessageTemplate: AlertMessageTemplateSettings = {
  format: "compact",
  include_trigger_summary: true,
  include_rule_details: true,
  include_pair: true,
  include_spread: true,
  include_funding: true,
  include_volume: true,
  include_risk: true,
  include_observations: true,
  include_dashboard_link: true,
  suppress_when_card_conditions_fail: false,
  observation_limit: 5
};

const defaultRiskSettings: RiskSettings = {
  big_spread_alert_enabled: true,
  big_spread_alert_pct: 3,
  big_spread_alert_hits: 2,
  big_spread_alert_cooldown_seconds: 600,
  big_spread_alert_auto_create: true,
  min_volume_24h_usdt: 1_000_000,
  stale_after_seconds: 30,
  huge_spread_pct: 10,
  wide_spread_pct: 3,
  mark_index_deviation_pct: 1,
  multi_exchange_premium_alert_enabled: true,
  multi_exchange_premium_threshold_pct: 2,
  multi_exchange_premium_cooldown_seconds: 900,
  funding_against_pct: 0.01,
  signal_slippage_buffer_pct: 0.05,
  min_effective_open_pct: 0.05,
  max_open_spread_decay_pct: 60,
  signal_validation_notional_usdt: 1000,
  orderbook_depth_safety_multiple: 2,
  orderbook_depth_band_pct: 0.1,
  min_top_of_book_depth_usdt: 0,
  signal_strategy_notes: "",
  ticker_collision_symbols: ["AIUSDT", "UPUSDT", "LABUSDT"],
  excluded_symbols: [],
  ignored_exchanges: [],
  symbol_aliases: [
    {
      exchange: "gate",
      symbol: "EDGEXUSDT",
      canonical_symbol: "EDGEUSDT",
      market_type: null,
      dex: null,
      price_multiplier: 1
    }
  ]
};

const defaultAstroCardSettings: AstroCardSettings = {
  max_trade_usdt: 10,
  leverage: 1,
  min_notional: 10,
  max_notional: 10,
  open_enabled: false,
  card_variant: "both",
  close_position_buffer_pct: 0.1,
  unfavorable_funding_weight: 1,
  close_position_floor_pct: 0
};

function normalizeAstroCardSettings(values?: Partial<AstroCardSettings>): AstroCardSettings {
  return {
    ...defaultAstroCardSettings,
    ...(values ?? {})
  };
}

const alertTemplateOptions: Array<{
  name: Exclude<keyof AlertMessageTemplateSettings, "format" | "observation_limit">;
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
    label: "只报告可建卡告警",
    description: "最新信号失效会始终静默；开启后，Astro 支持类型或盘口深度不满足建卡条件时也只写历史"
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
    suppress_sf_negative_funding: rule.suppress_sf_negative_funding ?? true,
    favorable_funding_open_spread_pct: rule.favorable_funding_open_spread_pct === undefined
      ? 0.9
      : rule.favorable_funding_open_spread_pct,
    min_volume_24h_k: Math.round(rule.min_volume_24h_usdt / 1000)
  };
}

function ruleFromForm(values: AlertRuleFormValues, defaults: AlertRule): AlertRule {
  const { min_volume_24h_k: minVolumeK, favorable_funding_open_spread_pct: relaxedSpread, ...rule } = values;
  return {
    ...defaults,
    ...rule,
    favorable_funding_open_spread_pct: relaxedSpread ?? null,
    exclude_symbols: defaults.exclude_symbols,
    min_volume_24h_usdt: (minVolumeK ?? 0) * 1000
  };
}

const exchangeOptions = ["binance", "okx", "bybit", "gate", "bitget", "htx", "aster", "hyperliquid", "lighter", "arcus"].map((item) => ({
  label: item,
  value: item
}));
const marketTypeOptions = [
  { label: "现货", value: "spot" },
  { label: "合约", value: "future" }
];
const serviceNames: ServiceName[] = ["frontend", "backend"];
const serviceLabels: Record<ServiceName, string> = {
  frontend: "前端",
  backend: "后端"
};
const riskSelectOptions = riskLabelOptions.map((item) => ({
  label: `${item.label} (${item.value})`,
  value: item.value
}));
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

function normalizeAliasSymbol(value?: string | null): string {
  const normalized = (value ?? "").trim().toUpperCase().replace(/[_/]/g, "-");
  const withoutSwap = normalized.endsWith("-SWAP") ? normalized.slice(0, -5) : normalized;
  const compact = withoutSwap.replace(/-/g, "");
  return compact && !compact.endsWith("USDT") ? `${compact}USDT` : compact;
}

function normalizeSymbolAliases(values?: SymbolAlias[]): SymbolAlias[] {
  return (values ?? [])
    .map((item) => {
      const exchange = (item.exchange ?? "").trim().toLowerCase();
      return {
        exchange,
        symbol: normalizeAliasSymbol(item.symbol),
        canonical_symbol: normalizeAliasSymbol(item.canonical_symbol),
        market_type: item.market_type ?? null,
        dex: exchange === "hyperliquid" ? (item.dex ?? "").trim().toLowerCase() || null : null,
        price_multiplier:
          Number.isFinite(Number(item.price_multiplier)) && Number(item.price_multiplier) > 0
            ? Number(item.price_multiplier)
            : 1
      };
    })
    .filter((item) => item.exchange && item.symbol && item.canonical_symbol);
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
    ignored_exchanges: normalized.ignored_exchanges ?? [],
    symbol_aliases: normalizeSymbolAliases(normalized.symbol_aliases)
  };
}

function riskFromForm(values: RiskSettings): RiskSettings {
  const { min_volume_24h_k: minVolumeK, ...settings } = values;
  return {
    ...defaultRiskSettings,
    ...settings,
    min_volume_24h_usdt: (minVolumeK ?? 0) * 1000,
    symbol_aliases: normalizeSymbolAliases(settings.symbol_aliases)
  };
}

function normalizeAlertTemplate(values?: Partial<AlertMessageTemplateSettings>): AlertMessageTemplateSettings {
  return {
    ...defaultAlertMessageTemplate,
    ...(values ?? {})
  };
}

function buildAlertTemplatePreview(template: AlertMessageTemplateSettings): string {
  if (template.format === "compact") {
    const lines = [
      "【推荐】BTCUSDT FF binance→okx",
      "买 binance 合约 BTCUSDT → 卖 okx 合约 BTCUSDT",
      "盘口价差：开仓 0.800% / 平仓 0.500%；费后开仓估算 0.600%",
      "资金费率：当前 买 0.010%/8h / 卖 -0.020%/8h；下期预估 买 0.010%/8h / 卖 0.020%/8h",
      "24h成交额：买 10,000,000 USDT / 卖 12,000,000 USDT",
      "卡片：已创建"
    ];
    if (template.suppress_when_card_conditions_fail) {
      lines.push("只报告可建卡告警：不满足建卡条件时仅保留告警历史，不发送飞书。");
    }
    return lines.join("\n");
  }
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
      "价差对：BTCUSDT | binance 合约 -> okx 合约",
      "方向：买入 binance 合约 BTCUSDT，卖出 okx 合约 BTCUSDT"
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
    blocks.push("只报告可建卡告警：不满足建卡条件时仅保留告警历史，不发送飞书。");
  }
  return blocks.join("\n\n") || "至少保留一个字段，避免告警内容为空。";
}

const settingsTabs = ["rules", "risk", "astro", "access", "history"] as const;
type SettingsTab = typeof settingsTabs[number];
const activeTabStorageKey = "settings-active-tab";

function isHistoryUrl(): boolean {
  return typeof window !== "undefined"
    && new URLSearchParams(window.location.search).get("page") === "history";
}

function initialSettingsTab(): SettingsTab {
  if (isHistoryUrl()) return "history";
  try {
    const saved = window.localStorage.getItem(activeTabStorageKey);
    return settingsTabs.includes(saved as SettingsTab) ? saved as SettingsTab : "rules";
  } catch {
    return "rules";
  }
}

export function SettingsPage() {
  const [activeTab, setActiveTab] = useState<SettingsTab>(initialSettingsTab);
  const [premiumStatus, setPremiumStatus] = useState<Awaited<ReturnType<typeof getPremiumIndexAlertStatus>> | null>(null);
  const [riskForm] = Form.useForm<RiskSettings>();
  const [ruleForm] = Form.useForm<AlertRuleFormValues>();
  const [templateForm] = Form.useForm<AlertMessageTemplateSettings>();
  const [astroAutomationForm] = Form.useForm<AstroAutomationSettings>();
  const [astroCardForm] = Form.useForm<AstroCardSettings>();
  const [rules, setRules] = useState<AlertRule[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [ruleDefaults, setRuleDefaults] = useState<AlertRule>(defaultRule);
  const [editingRule, setEditingRule] = useState<AlertRule | null>(null);
  const [ruleSaving, setRuleSaving] = useState(false);
  const [quickSavingRuleId, setQuickSavingRuleId] = useState<string | null>(null);
  const ruleEditorRef = useRef<HTMLElement | null>(null);
  const [alertTemplatePreview, setAlertTemplatePreview] =
    useState<AlertMessageTemplateSettings>(defaultAlertMessageTemplate);
  const [serviceControl, setServiceControl] = useState<ServiceControlStatus | null>(null);
  const [serviceControlError, setServiceControlError] = useState("");
  const [restartingService, setRestartingService] = useState<ServiceName | null>(null);
  const [astroStatus, setAstroStatus] = useState<AstroSdkStatus | null>(null);
  const [astroStatusError, setAstroStatusError] = useState("");

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
      const [
        risk,
        nextRules,
        nextServiceControl,
        alertTemplate,
        astroAutomation,
        astroCard,
        nextAstroStatus,
        nextPremiumStatus
      ] = await Promise.all([
        getRiskSettings(),
        listAlertRules(),
        serviceControlRequest,
        getAlertMessageTemplate(),
        getAstroAutomationSettings(),
        getAstroCardSettings(),
        astroStatusRequest,
        getPremiumIndexAlertStatus().catch(() => null)
      ]);
      const nextRuleDefaults = ruleDefaultsForRisk(risk);
      const nextAlertTemplate = normalizeAlertTemplate(alertTemplate);
      riskForm.setFieldsValue(riskToForm(risk));
      ruleForm.setFieldsValue(ruleToForm(nextRuleDefaults));
      templateForm.setFieldsValue(nextAlertTemplate);
      astroAutomationForm.setFieldsValue(astroAutomation);
      astroCardForm.setFieldsValue(astroCard);
      setRuleDefaults(nextRuleDefaults);
      setAlertTemplatePreview(nextAlertTemplate);
      setRules(nextRules);
      setEditingRule(null);
      setServiceControl(nextServiceControl);
      setAstroStatus(nextAstroStatus);
      setPremiumStatus(nextPremiumStatus);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    ruleForm.setFieldsValue(ruleToForm(defaultRule));
    templateForm.setFieldsValue(defaultAlertMessageTemplate);
    astroAutomationForm.setFieldsValue({
      alert_auto_create: false,
      allow_same_name_variants: false
    });
    astroCardForm.setFieldsValue(defaultAstroCardSettings);
    void load();
  }, []);

  useEffect(() => {
    try {
      window.localStorage.setItem(activeTabStorageKey, activeTab);
    } catch {
      // Keep the current tab usable when browser storage is unavailable.
    }
  }, [activeTab]);

  useEffect(() => {
    const openHistoryFromUrl = () => {
      if (isHistoryUrl()) setActiveTab("history");
    };
    window.addEventListener("popstate", openHistoryFromUrl);
    window.addEventListener("taoli1:navigate", openHistoryFromUrl);
    return () => {
      window.removeEventListener("popstate", openHistoryFromUrl);
      window.removeEventListener("taoli1:navigate", openHistoryFromUrl);
    };
  }, []);

  const saveRisk = async () => {
    const values = await riskForm.validateFields();
    const saved = await updateRiskSettings(riskFromForm(values));
    const nextRuleDefaults = ruleDefaultsForRisk(saved);
    riskForm.setFieldsValue(riskToForm(saved));
    if (!editingRule) {
      ruleForm.setFieldsValue(ruleToForm(nextRuleDefaults));
    }
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
    const saved = await updateAstroCardSettings(normalizeAstroCardSettings(values));
    astroCardForm.setFieldsValue(saved);
    message.success("Astro 卡片默认参数已保存");
  };

  const saveAstroAutomation = async () => {
    const values = await astroAutomationForm.validateFields();
    const saved = await updateAstroAutomationSettings(values);
    astroAutomationForm.setFieldsValue(saved);
    message.success("Astro 自动化设置已保存");
  };

  const resetRuleEditor = () => {
    setEditingRule(null);
    ruleForm.setFieldsValue(ruleToForm(ruleDefaults));
  };

  const editRule = (rule: AlertRule) => {
    setEditingRule(rule);
    ruleForm.setFieldsValue(ruleToForm(rule));
    ruleEditorRef.current?.scrollIntoView?.({ behavior: "smooth", block: "start" });
  };

  const saveRule = async () => {
    setRuleSaving(true);
    try {
      const values = await ruleForm.validateFields();
      const nextRule = ruleFromForm(values, editingRule ?? ruleDefaults);
      if (editingRule?.id) {
        const saved = await updateAlertRule(editingRule.id, nextRule);
        setRules((current) => current.map((item) => (item.id === editingRule.id ? saved : item)));
        message.success("规则已更新");
      } else {
        const saved = await createAlertRule(nextRule);
        setRules((current) => [saved, ...current]);
        message.success("规则已新增");
      }
      resetRuleEditor();
    } catch (exc) {
      message.error(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setRuleSaving(false);
    }
  };

  const removeRule = async (rule: AlertRule) => {
    if (!rule.id) {
      return;
    }
    await deleteAlertRule(rule.id);
    setRules((current) => current.filter((item) => item.id !== rule.id));
    if (editingRule?.id === rule.id) {
      resetRuleEditor();
    }
  };

  const setSfNegativeFundingSuppression = async (rule: AlertRule, checked: boolean) => {
    if (!rule.id) {
      return;
    }
    setQuickSavingRuleId(rule.id);
    try {
      const saved = await updateAlertRule(rule.id, {
        ...rule,
        suppress_sf_negative_funding: checked
      });
      setRules((current) => current.map((item) => (item.id === rule.id ? saved : item)));
      if (editingRule?.id === rule.id) {
        setEditingRule(saved);
        ruleForm.setFieldsValue(ruleToForm(saved));
      }
      message.success(checked ? "SF 负资金费率通知已关闭" : "SF 负资金费率通知已允许");
    } catch (exc) {
      message.error(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setQuickSavingRuleId(null);
    }
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

  const astroRuntimeWarnings = [
    astroStatus?.dry_run_only ? "Astro dry-run 当前开启，保存配置后仍不会写入实盘卡片。" : "",
    astroStatus && !astroStatus.configured ? "Astro SDK 未配置，无法提交卡片。" : "",
    astroStatus && astroStatus.configured && !astroStatus.verify_tls
      ? "Astro TLS 证书校验已关闭，仅建议用于可信内网或临时排障。"
      : "",
    astroStatusError ? `Astro 状态读取失败：${astroStatusError}` : ""
  ].filter(Boolean);

  const columns: ColumnsType<AlertRule> = [
    { title: "规则", dataIndex: "name" },
    { title: "类型", dataIndex: "types", render: (types: string[]) => types.join(",") },
    { title: "开仓阈值", dataIndex: "min_open_spread_pct", render: (value: number) => `${value}%` },
    {
      title: "资金费率条件阈值",
      dataIndex: "favorable_funding_open_spread_pct",
      render: (value: number | null | undefined) => value === null ? "关闭" : `${value ?? 0.9}%`
    },
    { title: "综合阈值", dataIndex: "min_fee_adjusted_open_pct", render: (value: number) => `${value}%` },
    { title: "连续命中", dataIndex: "consecutive_hits" },
    { title: "冷却", dataIndex: "cooldown_seconds", render: (value: number) => `${value}s` },
    {
      title: "SF 负资金通知",
      width: 150,
      render: (_, row) => (
        <Switch
          size="small"
          checked={row.suppress_sf_negative_funding !== false}
          checkedChildren="不通知"
          unCheckedChildren="允许"
          loading={quickSavingRuleId === row.id}
          disabled={!row.id || quickSavingRuleId !== null}
          aria-label={`设置 ${row.name} 的 SF 负资金费率不通知`}
          onChange={(checked) => void setSfNegativeFundingSuppression(row, checked)}
        />
      )
    },
    {
      title: "操作",
      width: 104,
      render: (_, row) => (
        <Space size={4}>
          <Tooltip title="编辑规则">
            <Button
              icon={<EditOutlined />}
              type="text"
              aria-label={`编辑规则 ${row.name}`}
              onClick={() => editRule(row)}
            />
          </Tooltip>
          <Tooltip title="删除规则">
            <Button
              icon={<DeleteOutlined />}
              type="text"
              danger
              aria-label={`删除规则 ${row.name}`}
              onClick={() => void removeRule(row)}
            />
          </Tooltip>
        </Space>
      )
    }
  ];

  return (
    <div className="page settings-page">
      {error ? <Alert type="error" message={error} showIcon /> : null}
      <Tabs
        activeKey={activeTab}
        onChange={(key) => setActiveTab(key as SettingsTab)}
        items={[
          {
            key: "rules",
            label: "告警规则",
            forceRender: true,
            children: (
              <div className="settings-grid">
                <section className="panel panel-wide" ref={ruleEditorRef}>
                  <div className="settings-panel-header">
                    <Typography.Title level={4}>{editingRule ? "编辑告警规则" : "新增告警规则"}</Typography.Title>
                    <Space wrap>
                      <Button
                        type="primary"
                        htmlType="submit"
                        form="alert-rule-editor"
                        disabled={loading || ruleSaving}
                        icon={editingRule ? <SaveOutlined /> : <PlusOutlined />}
                        loading={ruleSaving}
                        aria-label={editingRule ? "保存修改" : "新增规则"}
                      >
                        {editingRule ? "保存修改" : "新增规则"}
                      </Button>
                      {editingRule ? (
                        <Button icon={<CloseOutlined />} onClick={resetRuleEditor} disabled={ruleSaving}>
                          取消编辑
                        </Button>
                      ) : null}
                    </Space>
                  </div>
                  {editingRule ? (
                    <Alert
                      className="rule-guide"
                      type="warning"
                      showIcon
                      message={`正在编辑：${editingRule.name}`}
                      description="保存后立即更新现有规则，不会创建重复规则。"
                    />
                  ) : null}
                  <Alert className="rule-guide" type="info" showIcon message="规则说明" description={alertRuleGuide} />
                  <Form
                    id="alert-rule-editor"
                    name="alert-rule-editor"
                    form={ruleForm}
                    layout="vertical"
                    disabled={loading || ruleSaving}
                    initialValues={ruleToForm(ruleDefaults)}
                    onFinish={saveRule}
                  >
                    <div className="form-grid">
                      <Form.Item label="告警规则名称" name="name" rules={[{ required: true }]} help={alertRuleFieldHelp.name}>
                        <Input />
                      </Form.Item>
                      <Form.Item label="启用" name="enabled" valuePropName="checked" help={alertRuleFieldHelp.enabled}>
                        <Switch />
                      </Form.Item>
                      <Form.Item label="套利类型" name="types" rules={[{ required: true }]} help={alertRuleFieldHelp.types}>
                        <Select mode="multiple" options={alertTypeOptions} />
                      </Form.Item>
                      <Form.Item
                        label="SF 负资金费率不通知"
                        name="suppress_sf_negative_funding"
                        valuePropName="checked"
                        help={alertRuleFieldHelp.suppress_sf_negative_funding}
                      >
                        <Switch checkedChildren="不通知" unCheckedChildren="允许通知" />
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
                        label="资金费率条件开仓阈值"
                        name="favorable_funding_open_spread_pct"
                        help={alertRuleFieldHelp.favorable_funding_open_spread_pct}
                      >
                        <InputNumber min={0} step={0.1} suffix="%" placeholder="留空关闭" className="wide-input" />
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
                    scroll={{ x: 980 }}
                  />
                </section>
                <section className="panel panel-wide">
                  <div className="settings-panel-header">
                    <Typography.Title level={4}>告警内容模板</Typography.Title>
                    <Button type="primary" htmlType="submit" form="alert-template-form" icon={<SaveOutlined />} disabled={loading}>
                      保存告警模板
                    </Button>
                  </div>
                  <Alert
                    className="rule-guide"
                    type="info"
                    showIcon
                    message="全局模板"
                    description="这里控制飞书告警和新告警历史里展示哪些内容。告警规则仍然只负责判断什么时候触发。"
                  />
                  <Form id="alert-template-form"
                    form={templateForm}
                    layout="vertical"
                    disabled={loading}
                    onFinish={saveAlertTemplate}
                    onValuesChange={(_, values) => setAlertTemplatePreview(normalizeAlertTemplate(values))}
                  >
                    <Form.Item name="format" label="消息格式">
                      <Segmented
                        options={[{ label: "精简", value: "compact" }, { label: "详细", value: "detailed" }]}
                        aria-label="告警消息格式"
                      />
                    </Form.Item>
                    <div className="template-grid">
                      <div className="template-options">
                        {alertTemplateOptions.filter((option) =>
                          option.name === "suppress_when_card_conditions_fail" || alertTemplatePreview.format === "detailed"
                        ).map((option) => (
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
                        {alertTemplatePreview.format === "detailed" ? (
                          <Form.Item
                            label="连续监测最多显示轮数"
                            name="observation_limit"
                            rules={[{ required: true }]}
                            className="template-limit"
                          >
                            <InputNumber min={1} max={20} className="wide-input" />
                          </Form.Item>
                        ) : null}
                      </div>
                      <div className="template-preview">
                        <Typography.Text strong>消息预览</Typography.Text>
                        <pre>{buildAlertTemplatePreview(alertTemplatePreview)}</pre>
                      </div>
                    </div>
                  </Form>
                </section>
                <PhonePriceAlertsPanel />
              </div>
            )
          },
          {
            key: "risk",
            label: "风险参数",
            forceRender: true,
            children: (
              <div className="settings-grid">
                <section className="panel">
                  <div className="settings-panel-header">
                    <Typography.Title level={4}>风险参数</Typography.Title>
                    <Button type="primary" htmlType="submit" form="risk-settings-form" icon={<SaveOutlined />} disabled={loading}>
                      保存风险参数
                    </Button>
                  </div>
                  <Form id="risk-settings-form" form={riskForm} layout="vertical" disabled={loading} onFinish={saveRisk}>
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
                    </div>
                    <div className="risk-help">
                      {riskLabelOptions.map((item) => (
                        <Typography.Text key={item.value} type="secondary">
                          {item.label} ({item.value})：{item.description}
                        </Typography.Text>
                      ))}
                    </div>
                    <section className="settings-section">
                      <div className="form-grid">
                        <div className="form-grid-heading">
                          <Typography.Title level={5}>大价差快速提醒</Typography.Title>
                          <Typography.Paragraph type="secondary">
                            按可成交开仓价差连续命中触发，不受普通规则的衰减检查和风险标签排除影响，风险仍会写入提醒。
                            保留类型、黑名单、成交额、行情时效和市场身份限制。冷却期内价差较上次发送值增加至少 50% 时可再次提醒。
                            自动建卡还需启用全局自动建卡并通过原有校验；创建的卡片始终关闭开仓，需手动开启。
                          </Typography.Paragraph>
                        </div>
                        <Form.Item label="启用大价差提醒" name="big_spread_alert_enabled" valuePropName="checked">
                          <Switch />
                        </Form.Item>
                        <Form.Item label="大价差开仓阈值" name="big_spread_alert_pct" rules={[{ required: true }, { type: "number", min: 0.1, max: 100 }]}>
                          <InputNumber min={0.1} max={100} step={0.1} suffix="%" className="wide-input" />
                        </Form.Item>
                        <Form.Item label="大价差连续命中次数" name="big_spread_alert_hits" rules={[{ required: true }, { type: "integer", min: 1, max: 20 }]}>
                          <InputNumber min={1} max={20} precision={0} className="wide-input" />
                        </Form.Item>
                        <Form.Item label="大价差提醒冷却秒数" name="big_spread_alert_cooldown_seconds" rules={[{ required: true }, { type: "integer", min: 0, max: 604800 }]}>
                          <InputNumber min={0} max={604800} precision={0} step={60} className="wide-input" />
                        </Form.Item>
                        <Form.Item label="大价差提醒自动建卡（不开仓）" name="big_spread_alert_auto_create" valuePropName="checked">
                          <Switch />
                        </Form.Item>
                      </div>
                    </section>
                    <section className="settings-section">
                      <div className="form-grid">
                        <div className="form-grid-heading">
                          <Typography.Title level={5}>多交易所实时溢价指数告警</Typography.Title>
                          <Typography.Paragraph type="secondary">
                            同一已核验标的至少两个不同交易所的实时溢价指数绝对值严格超过阈值时，通过飞书告警。
                            正负方向均计入，同一交易所的多个市场只计一个交易所。官方 Premium Index 优先，
                            缺失时回退标记价/指数价偏离并注明来源。全市场分批轮询存在扫描延迟，过期数据不参与告警；此告警不会自动建卡。
                          </Typography.Paragraph>
                          {premiumStatus?.running && (
                            <Typography.Paragraph type="secondary">
                              监控标的：{premiumStatus.eligible_symbols ?? 0}；上一轮完整扫描：
                              {premiumStatus.last_sweep_seconds == null ? "尚未完成" : `${premiumStatus.last_sweep_seconds} 秒`}；
                              最近一批查询失败：{premiumStatus.last_error_count ?? 0}。
                            </Typography.Paragraph>
                          )}
                        </div>
                        <Form.Item label="启用多交易所溢价告警" name="multi_exchange_premium_alert_enabled" valuePropName="checked">
                          <Switch />
                        </Form.Item>
                        <Form.Item label="实时溢价指数绝对值阈值" name="multi_exchange_premium_threshold_pct" rules={[{ required: true }, { type: "number", min: 0.000001 }]}>
                          <InputNumber min={0.000001} step={0.1} suffix="%" className="wide-input" />
                        </Form.Item>
                        <Form.Item label="同标的告警冷却秒数" name="multi_exchange_premium_cooldown_seconds" rules={[{ required: true }]}>
                          <InputNumber min={60} precision={0} step={60} className="wide-input" />
                        </Form.Item>
                      </div>
                    </section>
                    <section className="settings-section">
                      <div className="form-grid">
                        <div className="form-grid-heading">
                          <Typography.Title level={5}>信号策略</Typography.Title>
                          <Typography.Paragraph type="secondary">
                            信号策略用于判断告警机会是否真的适合创建 Astro 卡片。系统会在创建卡片前拉取两边交易所的多档 order book，
                            按盘口验证金额模拟买入侧 asks 和卖出侧 bids 的可成交 VWAP，避免只看瞬时最优价导致价差一买就消失。
                          </Typography.Paragraph>
                          <Typography.Paragraph type="secondary">
                            最小验证金额是创建卡片前的盘口试算金额，默认 1000 USDT；不会使用 Astro 卡片的总仓位金额或手动建卡金额。
                            深度按最优价附近的价格带累计，例如 0.1% 价格带内有多少挂单。
                            若价格带深度不足、成交后价差不够、或价差衰减太快，系统会跳过创建卡片。
                          </Typography.Paragraph>
                        </div>
                        <Form.Item label="信号滑点缓冲百分比 (Signal slippage buffer pct)" name="signal_slippage_buffer_pct" rules={[{ required: true }]}>
                          <InputNumber min={0} step={0.01} suffix="%" className="wide-input" />
                        </Form.Item>
                        <Form.Item
                          label="最低有效开仓收益率 (Minimum effective open pct)"
                          name="min_effective_open_pct"
                          rules={[{ required: true }]}
                          help="允许填负数；例如 -1 表示实际可成交有效收益低于 -1% 才拦截，填更低可基本关闭这项拦截。"
                        >
                          <InputNumber step={0.01} suffix="%" className="wide-input" />
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
                        <Form.Item label="开仓深度价格带 % (Order book depth band pct)" name="orderbook_depth_band_pct" rules={[{ required: true }]}>
                          <InputNumber min={0} step={0.01} suffix="%" className="wide-input" />
                        </Form.Item>
                        <Form.Item label="最小价格带深度 USDT (Minimum band depth USDT)" name="min_top_of_book_depth_usdt" rules={[{ required: true }]}>
                          <InputNumber min={0} step={10} className="wide-input" />
                        </Form.Item>
                        <Form.Item
                          label="信号策略备注 / 后续自定义规则"
                          name="signal_strategy_notes"
                          className="form-grid-wide"
                        >
                          <Input.TextArea rows={4} />
                        </Form.Item>
                      </div>
                    </section>
                    <section className="settings-section">
                      <Typography.Title level={5}>币名映射与过滤</Typography.Title>
                      <Form.Item label="同名风险标的 (SAME_TICKER_RISK)" name="ticker_collision_symbols">
                        <Select mode="tags" tokenSeparators={[",", " "]} />
                      </Form.Item>
                      <Form.Item label="黑名单标的" name="excluded_symbols">
                        <Select mode="tags" tokenSeparators={[",", " "]} placeholder="TRADOORUSDT" />
                      </Form.Item>
                      <Form.Item label="忽略交易所" name="ignored_exchanges">
                        <Select mode="multiple" allowClear options={exchangeOptions} />
                      </Form.Item>
                      <Form.List name="symbol_aliases">
                        {(fields, { add, remove }) => (
                          <div className="symbol-alias-list">
                            <Space className="symbol-alias-header" align="center" wrap>
                              <Typography.Title level={5}>全局币名映射</Typography.Title>
                              <Tag color="blue">全部页面生效</Tag>
                              <Button
                                type="dashed"
                                icon={<PlusOutlined />}
                                onClick={() =>
                                  add({
                                    exchange: "gate",
                                    symbol: "",
                                    canonical_symbol: "",
                                    market_type: null,
                                    dex: null,
                                    price_multiplier: 1
                                  })
                                }
                              >
                                添加映射
                              </Button>
                            </Space>
                            {fields.map((field) => (
                              <Space key={field.key} align="start" wrap className="symbol-alias-row">
                                <Form.Item
                                  label="交易所"
                                  name={[field.name, "exchange"]}
                                  rules={[{ required: true }]}
                                >
                                  <Select options={exchangeOptions} className="alias-exchange-input" />
                                </Form.Item>
                                <Form.Item
                                  label="原始名"
                                  name={[field.name, "symbol"]}
                                  rules={[{ required: true }]}
                                >
                                  <Input placeholder="NEX" className="alias-symbol-input" />
                                </Form.Item>
                                <Form.Item
                                  label="映射名"
                                  name={[field.name, "canonical_symbol"]}
                                  rules={[{ required: true }]}
                                >
                                  <Input placeholder="10000NEX" className="alias-symbol-input" />
                                </Form.Item>
                                <Form.Item label="类型" name={[field.name, "market_type"]}>
                                  <Select
                                    allowClear
                                    options={marketTypeOptions}
                                    placeholder="全部"
                                    className="alias-market-type-input"
                                  />
                                </Form.Item>
                                <Form.Item label="DEX" name={[field.name, "dex"]}>
                                  <Input placeholder="仅 Hyperliquid，如 io" className="alias-symbol-input" />
                                </Form.Item>
                                <Form.Item
                                  label="价格汇率"
                                  name={[field.name, "price_multiplier"]}
                                  rules={[{ required: true, type: "number", min: 0.000001 }]}
                                >
                                  <InputNumber min={0.000001} step={1} className="alias-symbol-input" />
                                </Form.Item>
                                <Button
                                  type="text"
                                  danger
                                  icon={<DeleteOutlined />}
                                  aria-label="删除映射"
                                  onClick={() => remove(field.name)}
                                />
                              </Space>
                            ))}
                          </div>
                        )}
                      </Form.List>
                    </section>
                  </Form>
                </section>
                <MarketIdentityPanel />
              </div>
            )
          },
          {
            key: "astro",
            label: "Astro",
            forceRender: true,
            children: (
              <div className="settings-grid">
                <section className="panel">
                  <div className="settings-panel-header">
                    <Typography.Title level={4}>Astro 自动化</Typography.Title>
                    <Button type="primary" htmlType="submit" form="astro-automation-form" icon={<SaveOutlined />} disabled={loading}>
                      保存 Astro 自动化
                    </Button>
                  </div>
                  <Alert
                    className="rule-guide"
                    type="info"
                    showIcon
                    message="控制告警是否自动创建 Astro 卡片"
                    description="开启后，命中告警会继续经过最新信号、订单簿、卡片参数和重复卡片校验；全部通过后才会写入 Astro。"
                  />
                  {astroRuntimeWarnings.length > 0 ? (
                    <Alert
                      className="rule-guide"
                      type="warning"
                      showIcon
                      message="运行状态提示"
                      description={astroRuntimeWarnings.join(" ")}
                    />
                  ) : null}
                  <Form id="astro-automation-form"
                    form={astroAutomationForm}
                    layout="vertical"
                    disabled={loading}
                    onFinish={saveAstroAutomation}
                  >
                    <Form.Item
                      label="告警自动创建 Astro 卡片"
                      name="alert_auto_create"
                      valuePropName="checked"
                      help="关闭时只会记录告警和通知，不会自动写入 Astro 卡片。"
                    >
                      <Switch checkedChildren="开启" unCheckedChildren="关闭" />
                    </Form.Item>
                    <Form.Item
                      label="允许同名不同类型或路线卡片"
                      name="allow_same_name_variants"
                      valuePropName="checked"
                      help="开启后，同名但类型或交易所路线不同的卡片会继续创建；名称、类型和路线完全相同时仍会跳过。"
                    >
                      <Switch checkedChildren="允许" unCheckedChildren="跳过" />
                    </Form.Item>
                  </Form>
                </section>
                <section className="panel">
                  <div className="settings-panel-header">
                    <Typography.Title level={4}>Astro 卡片默认参数</Typography.Title>
                    <Button type="primary" htmlType="submit" form="astro-card-defaults" icon={<SaveOutlined />} disabled={loading}>
                      保存 Astro 卡片默认参数
                    </Button>
                  </div>
                  <Form id="astro-card-defaults"
                    name="astro-card-defaults"
                    form={astroCardForm}
                    layout="vertical"
                    disabled={loading}
                    onFinish={saveAstroCardDefaults}
                  >
                    <div className="form-grid">
                      <Form.Item label="仓位金额 USDT" name="max_trade_usdt" rules={[{ required: true }]}>
                        <InputNumber min={0.01} step={1} className="wide-input" />
                      </Form.Item>
                      <Form.Item label="杠杆倍数" name="leverage" rules={[{ required: true }]}>
                        <InputNumber min={1} step={1} className="wide-input" />
                      </Form.Item>
                      <Form.Item label="最小名义金额 USDT" name="min_notional" rules={[{ required: true }]}>
                        <InputNumber min={0} step={1} className="wide-input" />
                      </Form.Item>
                      <Form.Item label="最大名义金额 USDT" name="max_notional" rules={[{ required: true }]}>
                        <InputNumber min={0.01} step={1} className="wide-input" />
                      </Form.Item>
                      <Form.Item label="创建后允许开仓" name="open_enabled" valuePropName="checked">
                        <Switch checkedChildren="开启" unCheckedChildren="关闭" />
                      </Form.Item>
                      <Form.Item label="平仓缓冲比例" name="close_position_buffer_pct" rules={[{ required: true }]}>
                        <InputNumber min={0} step={0.01} suffix="%" className="wide-input" />
                      </Form.Item>
                      <Form.Item label="不利资金费权重" name="unfavorable_funding_weight" rules={[{ required: true }]}>
                        <InputNumber min={0} step={0.1} className="wide-input" />
                      </Form.Item>
                      <Form.Item label="平仓下限比例" name="close_position_floor_pct" rules={[{ required: true }]}>
                        <InputNumber min={0} step={0.01} suffix="%" className="wide-input" />
                      </Form.Item>
                    </div>
                    <Form.Item
                      label="默认建卡路线"
                      name="card_variant"
                      help="Aster、Lighter 支持普通与 GC 路线，GC 路线使用 gc-aster、gc-lighter；Bitget 保留原交易所 ID，仅将支持 GC 的另一腿切换为 GC。具体可建路线以建卡预览为准。"
                    >
                      <AstroCardVariantSelector />
                    </Form.Item>
                  </Form>
                </section>
              </div>
            )
          },
          {
            key: "access",
            label: "访问与服务",
            forceRender: true,
            children: (
              <div className="settings-grid">
                <section className="panel">
                  <div className="settings-panel-header">
                    <Typography.Title level={4}>面板访问</Typography.Title>
                    <Button icon={<SaveOutlined />} onClick={() => void load()}>
                      应用
                    </Button>
                  </div>
                  <LoginDevices />
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
              </div>
            )
          },
          {
            key: "history",
            label: "告警历史",
            forceRender: true,
            children: (
              <div className="settings-grid">
                <AlertHistoryPage active={activeTab === "history"} />
              </div>
            )
          }
        ]}
      />
    </div>
  );
}
