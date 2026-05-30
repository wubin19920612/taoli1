import { DeleteOutlined, PhoneOutlined } from "@ant-design/icons";
import { Alert, Button, Form, Input, InputNumber, Select, Space, Switch, Table, Tag, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useEffect, useState } from "react";

import {
  createPhonePriceAlertRule,
  deletePhonePriceAlertRule,
  getPhonePriceAlertDiagnostics,
  listPhonePriceAlertRules
} from "../api/client";
import type { PhonePriceAlertDiagnostic, PhonePriceAlertDiagnostics, PhonePriceAlertRule } from "../api/types";

const exchangeOptions = ["binance", "okx", "bybit", "gate", "bitget", "htx", "aster", "hyperliquid"].map((item) => ({
  label: item,
  value: item
}));

const defaultPhoneRule: PhonePriceAlertRule = {
  name: "",
  enabled: true,
  symbol: "",
  exchange: undefined,
  market_type: "future",
  price_field: "mark_price",
  condition: "above",
  target_price: 1,
  cooldown_seconds: 300
};

function normalizeRule(values: PhonePriceAlertRule): PhonePriceAlertRule {
  return {
    ...defaultPhoneRule,
    ...values,
    symbol: values.symbol.trim().toUpperCase().replace(/[-_]/g, ""),
    exchange: values.exchange || undefined
  };
}

function formatPrice(value?: number | null): string {
  if (value === null || value === undefined) {
    return "-";
  }
  return Number.isInteger(value) ? value.toFixed(0) : value.toPrecision(8).replace(/0+$/, "").replace(/\.$/, "");
}

function diagnosticTag(diagnostic?: PhonePriceAlertDiagnostic) {
  if (!diagnostic) {
    return <Tag>待诊断</Tag>;
  }
  if (!diagnostic.market_found) {
    return <Tag color="orange">未找到行情</Tag>;
  }
  if (diagnostic.triggered) {
    return <Tag color="red">已达到</Tag>;
  }
  return <Tag color="blue">监控中</Tag>;
}

export function PhonePriceAlertsPanel() {
  const [form] = Form.useForm<PhonePriceAlertRule>();
  const [rules, setRules] = useState<PhonePriceAlertRule[]>([]);
  const [diagnostics, setDiagnostics] = useState<PhonePriceAlertDiagnostics | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const [nextRules, nextDiagnostics] = await Promise.all([
        listPhonePriceAlertRules(),
        getPhonePriceAlertDiagnostics()
      ]);
      setRules(nextRules);
      setDiagnostics(nextDiagnostics);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    form.setFieldsValue(defaultPhoneRule);
    void load();
  }, []);

  const createRule = async () => {
    const saved = await createPhonePriceAlertRule(normalizeRule(await form.validateFields()));
    setRules((current) => [saved, ...current]);
    form.setFieldsValue(defaultPhoneRule);
    setDiagnostics(await getPhonePriceAlertDiagnostics());
    message.success("电话价格告警已新增");
  };

  const removeRule = async (rule: PhonePriceAlertRule) => {
    if (!rule.id) {
      return;
    }
    await deletePhonePriceAlertRule(rule.id);
    setRules((current) => current.filter((item) => item.id !== rule.id));
    setDiagnostics(await getPhonePriceAlertDiagnostics());
  };

  const diagnosticByRuleId = new Map((diagnostics?.items ?? []).map((item) => [item.rule_id, item]));
  const columns: ColumnsType<PhonePriceAlertRule> = [
    { title: "规则", dataIndex: "name" },
    { title: "标的", dataIndex: "symbol", width: 120 },
    { title: "交易所", dataIndex: "exchange", width: 120, render: (value?: string | null) => value || "任意" },
    { title: "市场", dataIndex: "market_type", width: 90, render: (value: string) => <Tag>{value}</Tag> },
    { title: "价格源", dataIndex: "price_field", width: 120 },
    {
      title: "当前",
      width: 170,
      render: (_, row) => {
        const diagnostic = row.id ? diagnosticByRuleId.get(row.id) : undefined;
        return (
          <Space size={4} direction="vertical">
            {diagnosticTag(diagnostic)}
            <Typography.Text type="secondary">
              {diagnostic?.resolved_price_field ?? diagnostic?.price_field ?? row.price_field}:{" "}
              {formatPrice(diagnostic?.observed_price)}
            </Typography.Text>
          </Space>
        );
      }
    },
    {
      title: "触发",
      width: 150,
      render: (_, row) => `${row.condition === "above" ? ">=" : "<="} ${row.target_price}`
    },
    {
      title: "状态说明",
      width: 280,
      render: (_, row) => {
        const diagnostic = row.id ? diagnosticByRuleId.get(row.id) : undefined;
        return diagnostic?.reason ?? "等待诊断刷新";
      }
    },
    { title: "冷却", dataIndex: "cooldown_seconds", width: 90, render: (value: number) => `${value}s` },
    {
      title: "",
      width: 72,
      render: (_, row) => (
        <Button icon={<DeleteOutlined />} type="text" danger onClick={() => void removeRule(row)} />
      )
    }
  ];

  return (
    <section className="panel panel-wide">
      <Typography.Title level={4}>电话价格告警</Typography.Title>
      <Alert
        className="rule-guide"
        type={diagnostics?.phone_enabled === false ? "warning" : "info"}
        showIcon
        message={
          diagnostics?.phone_enabled === false
            ? "电话通道未启用"
            : "达到价格后通过飞书电话加急提醒"
        }
        description={
          diagnostics?.phone_enabled === false
            ? "当前后端没有开启 FEISHU_PHONE_ENABLED；规则会保存，但不会启动电话价格告警循环。"
            : "需要后端配置 FEISHU_APP_ID、FEISHU_APP_SECRET、FEISHU_ALERT_CHAT_ID、FEISHU_PHONE_USER_IDS，并开启 FEISHU_PHONE_ENABLED。"
        }
      />
      {error ? <Alert className="rule-guide" type="error" message={error} showIcon /> : null}
      <Form form={form} layout="vertical" disabled={loading} onFinish={createRule}>
        <div className="form-grid">
          <Form.Item label="规则名称" name="name" rules={[{ required: true }]}>
            <Input placeholder="BTC 合约突破" />
          </Form.Item>
          <Form.Item label="启用" name="enabled" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Form.Item label="标的" name="symbol" rules={[{ required: true }]}>
            <Input placeholder="BTCUSDT" />
          </Form.Item>
          <Form.Item label="交易所" name="exchange">
            <Select allowClear options={exchangeOptions} placeholder="任意交易所" />
          </Form.Item>
          <Form.Item label="市场" name="market_type" rules={[{ required: true }]}>
            <Select options={[{ label: "合约", value: "future" }, { label: "现货", value: "spot" }]} />
          </Form.Item>
          <Form.Item label="价格源" name="price_field" rules={[{ required: true }]}>
            <Select
              options={[
                { label: "标记价", value: "mark_price" },
                { label: "指数价", value: "index_price" },
                { label: "买卖中间价", value: "mid_price" },
                { label: "买一价", value: "bid" },
                { label: "卖一价", value: "ask" }
              ]}
            />
          </Form.Item>
          <Form.Item label="方向" name="condition" rules={[{ required: true }]}>
            <Select options={[{ label: "大于等于", value: "above" }, { label: "小于等于", value: "below" }]} />
          </Form.Item>
          <Form.Item label="目标价格" name="target_price" rules={[{ required: true }]}>
            <InputNumber min={0.00000001} step={1} className="wide-input" />
          </Form.Item>
          <Form.Item label="冷却秒数" name="cooldown_seconds" rules={[{ required: true }]}>
            <InputNumber min={0} step={60} className="wide-input" />
          </Form.Item>
        </div>
        <Space wrap>
          <Button type="primary" htmlType="submit" icon={<PhoneOutlined />}>
            新增电话告警
          </Button>
          <Button onClick={() => void load()}>刷新</Button>
        </Space>
      </Form>
      <Table
        className="phone-alert-table"
        columns={columns}
        dataSource={rules}
        loading={loading}
        rowKey={(row) => row.id ?? `${row.symbol}-${row.target_price}`}
        pagination={false}
        size="middle"
        tableLayout="fixed"
      />
    </section>
  );
}
