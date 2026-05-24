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
  getRiskSettings,
  getServiceControlStatus,
  listAlertRules,
  restartServiceControl,
  saveDashboardPassword,
  updateAlertMessageTemplate,
  updateAstroCardSettings,
  updateRiskSettings
} from "../api/client";
import type {
  AlertMessageTemplateSettings,
  AlertRule,
  AstroCardSettings,
  RiskSettings,
  ServiceControlStatus
} from "../api/types";
import { alertRuleFieldHelp, alertRuleGuide, alertSeverityOptions, alertTypeOptions } from "../constants/alertRules";
import { defaultHiddenRiskLabels, riskLabelOptions } from "../constants/riskLabels";

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

const alertTemplateOptions: Array<{
  name: Exclude<keyof AlertMessageTemplateSettings, "observation_limit">;
  label: string;
  description: string;
}> = [
  { name: "include_trigger_summary", label: "触发摘要", description: "规则名、等级和触发提示" },
  { name: "include_rule_details", label: "规则参数", description: "阈值、交易所、标的和冷却参数" },
  { name: "include_pair", label: "价差对", description: "标的、买卖方向和两侧交易所" },
  { name: "include_spread", label: "价差信息", description: "开仓、平仓、净估算和综合开仓" },
  { name: "include_funding", label: "资金费率", description: "当前、预测资金费率、日化净差和结算周期" },
  { name: "include_volume", label: "成交额", description: "买入侧和卖出侧 24h 成交额" },
  { name: "include_risk", label: "风险标签", description: "过滤命中的风险标签" },
  { name: "include_observations", label: "连续监测", description: "最近几轮命中的价差和日化资金费差" },
  { name: "include_dashboard_link", label: "Dashboard 链接", description: "消息末尾追加面板地址" }
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
      "资金费率差（日化）：当前 -0.09% / 预测 0.03%",
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
      `【连续监测】\n1. 01:59:44 | 价差 0.720% | 净估算 0.520% | 资金差（日化） 0.03% | 综合 0.550%\n最多显示 ${template.observation_limit} 轮`
    );
  }
  if (template.include_dashboard_link) {
    blocks.push("Dashboard: https://your-domain.example");
  }
  return blocks.join("\n\n") || "至少保留一个字段，避免告警内容为空。";
}

export function SettingsPage() {
  const [riskForm] = Form.useForm<RiskSettings>();
  const [ruleForm] = Form.useForm<AlertRuleFormValues>();
  const [templateForm] = Form.useForm<AlertMessageTemplateSettings>();
  const [astroCardForm] = Form.useForm<AstroCardSettings>();
  const [rules, setRules] = useState<AlertRule[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [ruleDefaults, setRuleDefaults] = useState<AlertRule>(defaultRule);
  const [alertTemplatePreview, setAlertTemplatePreview] =
    useState<AlertMessageTemplateSettings>(defaultAlertMessageTemplate);
  const [serviceControl, setServiceControl] = useState<ServiceControlStatus | null>(null);
  const [serviceControlError, setServiceControlError] = useState("");
  const [restartingService, setRestartingService] = useState<ServiceName | null>(null);

  const load = async () => {
    setLoading(true);
    setError("");
    setServiceControlError("");
    try {
      const serviceControlRequest = getServiceControlStatus().catch((exc) => {
        setServiceControlError(exc instanceof Error ? exc.message : String(exc));
        return null;
      });
      const [risk, nextRules, nextServiceControl, alertTemplate, astroCard] = await Promise.all([
        getRiskSettings(),
        listAlertRules(),
        serviceControlRequest,
        getAlertMessageTemplate(),
        getAstroCardSettings()
      ]);
      const nextRuleDefaults = ruleDefaultsForRisk(risk);
      const nextAlertTemplate = normalizeAlertTemplate(alertTemplate);
      riskForm.setFieldsValue(riskToForm(risk));
      ruleForm.setFieldsValue(ruleToForm(nextRuleDefaults));
      templateForm.setFieldsValue(nextAlertTemplate);
      astroCardForm.setFieldsValue(astroCard);
      setRuleDefaults(nextRuleDefaults);
      setAlertTemplatePreview(nextAlertTemplate);
      setRules(nextRules);
      setServiceControl(nextServiceControl);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    ruleForm.setFieldsValue(ruleToForm(defaultRule));
    templateForm.setFieldsValue(defaultAlertMessageTemplate);
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
              <Typography.Title level={5}>Signal strategy</Typography.Title>
            </div>
            <Form.Item label="Signal slippage buffer pct" name="signal_slippage_buffer_pct" rules={[{ required: true }]}>
              <InputNumber min={0} step={0.01} suffix="%" className="wide-input" />
            </Form.Item>
            <Form.Item label="Minimum effective open pct" name="min_effective_open_pct" rules={[{ required: true }]}>
              <InputNumber min={0} step={0.01} suffix="%" className="wide-input" />
            </Form.Item>
            <Form.Item label="Max open spread decay pct" name="max_open_spread_decay_pct" rules={[{ required: true }]}>
              <InputNumber min={0} max={100} step={1} suffix="%" className="wide-input" />
            </Form.Item>
            <Form.Item label="Minimum validation notional USDT" name="signal_validation_notional_usdt" rules={[{ required: true }]}>
              <InputNumber min={0} step={1} className="wide-input" />
            </Form.Item>
            <Form.Item label="Depth safety multiple" name="orderbook_depth_safety_multiple" rules={[{ required: true }]}>
              <InputNumber min={0} step={0.5} className="wide-input" />
            </Form.Item>
            <Form.Item label="Minimum top-of-book depth USDT" name="min_top_of_book_depth_usdt" rules={[{ required: true }]}>
              <InputNumber min={0} step={10} className="wide-input" />
            </Form.Item>
            <Form.Item label="Signal strategy notes" name="signal_strategy_notes" className="form-grid-wide">
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
    </div>
  );
}
