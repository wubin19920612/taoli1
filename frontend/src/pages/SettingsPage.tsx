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
  excluded_risk_labels: ["HUGE_SPREAD_VERIFY", "STALE_DATA"],
  consecutive_hits: 3,
  cooldown_seconds: 300,
  severity: "warning"
};

const exchangeOptions = ["binance", "okx", "bybit", "gate", "bitget", "htx", "aster"].map((item) => ({
  label: item,
  value: item
}));

export function SettingsPage() {
  const [riskForm] = Form.useForm<RiskSettings>();
  const [ruleForm] = Form.useForm<AlertRule>();
  const [rules, setRules] = useState<AlertRule[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const [risk, nextRules] = await Promise.all([getRiskSettings(), listAlertRules()]);
      riskForm.setFieldsValue(risk);
      setRules(nextRules);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    ruleForm.setFieldsValue(defaultRule);
    void load();
  }, []);

  const saveRisk = async () => {
    const values = await riskForm.validateFields();
    const saved = await updateRiskSettings(values);
    riskForm.setFieldsValue(saved);
    message.success("已保存");
  };

  const createRule = async () => {
    const values = await ruleForm.validateFields();
    const saved = await createAlertRule({ ...defaultRule, ...values });
    setRules((current) => [saved, ...current]);
    ruleForm.setFieldsValue(defaultRule);
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
            <Form.Item label="最低24h成交额" name="min_volume_24h_usdt" rules={[{ required: true }]}>
              <InputNumber min={0} className="wide-input" />
            </Form.Item>
            <Form.Item label="数据过期秒数" name="stale_after_seconds" rules={[{ required: true }]}>
              <InputNumber min={5} className="wide-input" />
            </Form.Item>
            <Form.Item label="异常大价差" name="huge_spread_pct" rules={[{ required: true }]}>
              <InputNumber min={0} step={0.1} suffix="%" className="wide-input" />
            </Form.Item>
            <Form.Item label="价差宽度" name="wide_spread_pct" rules={[{ required: true }]}>
              <InputNumber min={0} step={0.1} suffix="%" className="wide-input" />
            </Form.Item>
            <Form.Item label="标记指数偏离" name="mark_index_deviation_pct" rules={[{ required: true }]}>
              <InputNumber min={0} step={0.1} suffix="%" className="wide-input" />
            </Form.Item>
            <Form.Item label="资金费率逆风" name="funding_against_pct" rules={[{ required: true }]}>
              <InputNumber min={0} step={0.01} suffix="%" className="wide-input" />
            </Form.Item>
          </div>
          <Form.Item label="同名风险标的" name="ticker_collision_symbols">
            <Select mode="tags" tokenSeparators={[",", " "]} />
          </Form.Item>
          <Button type="primary" htmlType="submit" icon={<SaveOutlined />}>
            保存风险参数
          </Button>
        </Form>
      </section>
      <section className="panel">
        <Typography.Title level={4}>新增告警规则</Typography.Title>
        <Form form={ruleForm} layout="vertical" initialValues={defaultRule} onFinish={createRule}>
          <div className="form-grid">
            <Form.Item label="规则名称" name="name" rules={[{ required: true }]}>
              <Input />
            </Form.Item>
            <Form.Item label="启用" name="enabled" valuePropName="checked">
              <Switch />
            </Form.Item>
            <Form.Item label="套利类型" name="types" rules={[{ required: true }]}>
              <Select
                mode="multiple"
                options={["SF", "FF", "SS"].map((item) => ({ label: item, value: item }))}
              />
            </Form.Item>
            <Form.Item label="包含交易所" name="include_exchanges">
              <Select mode="multiple" allowClear options={exchangeOptions} />
            </Form.Item>
            <Form.Item label="开仓阈值" name="min_open_spread_pct" rules={[{ required: true }]}>
              <InputNumber min={0} step={0.1} suffix="%" className="wide-input" />
            </Form.Item>
            <Form.Item label="净估算阈值" name="min_fee_adjusted_open_pct" rules={[{ required: true }]}>
              <InputNumber min={0} step={0.1} suffix="%" className="wide-input" />
            </Form.Item>
            <Form.Item label="最低成交额" name="min_volume_24h_usdt" rules={[{ required: true }]}>
              <InputNumber min={0} className="wide-input" />
            </Form.Item>
            <Form.Item label="连续命中" name="consecutive_hits" rules={[{ required: true }]}>
              <InputNumber min={1} className="wide-input" />
            </Form.Item>
            <Form.Item label="冷却秒数" name="cooldown_seconds" rules={[{ required: true }]}>
              <InputNumber min={0} className="wide-input" />
            </Form.Item>
            <Form.Item label="等级" name="severity" rules={[{ required: true }]}>
              <Select
                options={[
                  { label: "info", value: "info" },
                  { label: "warning", value: "warning" },
                  { label: "critical", value: "critical" }
                ]}
              />
            </Form.Item>
          </div>
          <Form.Item label="排除风险标签" name="excluded_risk_labels">
            <Select mode="tags" tokenSeparators={[",", " "]} />
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
