import { DeleteOutlined, SaveOutlined } from "@ant-design/icons";
import {
  Alert,
  Button,
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
  getRiskSettings,
  listAlertRules,
  saveDashboardPassword,
  updateRiskSettings
} from "../api/client";
import type { AlertRule, RiskSettings } from "../api/types";
import { alertRuleFieldHelp, alertRuleGuide, alertSeverityOptions, alertTypeOptions } from "../constants/alertRules";
import { defaultHiddenRiskLabels, riskLabelOptions } from "../constants/riskLabels";

type AlertRuleFormValues = AlertRule & {
  min_volume_24h_k?: number;
};

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
    min_volume_24h_usdt: (minVolumeK ?? 0) * 1000
  };
}

const exchangeOptions = ["binance", "okx", "bybit", "gate", "bitget", "htx", "aster", "hyperliquid"].map((item) => ({
  label: item,
  value: item
}));
const riskSelectOptions = riskLabelOptions.map((item) => ({
  label: `${item.label} (${item.value})`,
  value: item.value
}));

function riskToForm(settings: RiskSettings): RiskSettings {
  return {
    ...settings,
    min_volume_24h_k: Math.round(settings.min_volume_24h_usdt / 1000),
    excluded_symbols: settings.excluded_symbols ?? [],
    ignored_exchanges: settings.ignored_exchanges ?? []
  };
}

function riskFromForm(values: RiskSettings): RiskSettings {
  const { min_volume_24h_k: minVolumeK, ...settings } = values;
  return {
    ...settings,
    min_volume_24h_usdt: (minVolumeK ?? 0) * 1000
  };
}

export function SettingsPage() {
  const [riskForm] = Form.useForm<RiskSettings>();
  const [ruleForm] = Form.useForm<AlertRuleFormValues>();
  const [rules, setRules] = useState<AlertRule[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [ruleDefaults, setRuleDefaults] = useState<AlertRule>(defaultRule);

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const [risk, nextRules] = await Promise.all([getRiskSettings(), listAlertRules()]);
      const nextRuleDefaults = ruleDefaultsForRisk(risk);
      riskForm.setFieldsValue(riskToForm(risk));
      ruleForm.setFieldsValue(ruleToForm(nextRuleDefaults));
      setRuleDefaults(nextRuleDefaults);
      setRules(nextRules);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    ruleForm.setFieldsValue(ruleToForm(defaultRule));
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

  const columns: ColumnsType<AlertRule> = [
    { title: "规则", dataIndex: "name" },
    { title: "类型", dataIndex: "types", render: (types: string[]) => types.join(",") },
    { title: "开仓阈值", dataIndex: "min_open_spread_pct", render: (value: number) => `${value}%` },
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
            <Form.Item
              label="排除标的"
              name="exclude_symbols"
              help={alertRuleFieldHelp.exclude_symbols}
            >
              <Select mode="tags" tokenSeparators={[",", " "]} placeholder="TRADOORUSDT" />
            </Form.Item>
            <Form.Item
              label="开仓阈值"
              name="min_open_spread_pct"
              rules={[{ required: true }]}
              help={alertRuleFieldHelp.min_open_spread_pct}
            >
              <InputNumber min={0} step={0.1} suffix="%" className="wide-input" />
            </Form.Item>
            <Form.Item
              label="净估算阈值"
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
