import { ReloadOutlined, SaveOutlined } from "@ant-design/icons";
import {
  Alert,
  Button,
  Form,
  InputNumber,
  Space,
  Statistic,
  Switch,
  Table,
  Tag,
  Typography,
  message
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useCallback, useEffect, useState } from "react";

import {
  getFundingArbitragePreview,
  getFundingArbitrageSettings,
  updateFundingArbitrageSettings
} from "../api/client";
import type {
  AdlRiskLevel,
  FundingArbitrageCandidate,
  FundingArbitrageDecision,
  FundingArbitragePreview,
  FundingArbitrageSettings,
  FundingSource
} from "../api/types";

const defaultFundingSettings: FundingArbitrageSettings = {
  enabled: false,
  max_candidates: 50,
  min_entry_edge_pct: 0.03,
  min_hold_edge_pct: 0,
  min_exit_edge_pct: 0,
  min_funding_edge_pct: 0.02,
  min_volume_24h_usdt: 1_000_000,
  max_mark_index_deviation_pct: 1,
  max_basis_width_pct: 3,
  slippage_buffer_pct: 0.05,
  basis_risk_weight: 1,
  confidence_penalty_pct: 0.02,
  min_minutes_to_settlement: 5,
  max_minutes_to_settlement: 90,
  adl_block_score: 80,
  leverage: 1,
  notional_per_symbol_usdt: 100,
  prefer_hyperliquid: true
};

type FundingSettingsForm = Omit<FundingArbitrageSettings, "min_volume_24h_usdt"> & {
  min_volume_24h_k: number;
};

const decisionColor: Record<FundingArbitrageDecision, string> = {
  ENTER: "green",
  HOLD: "blue",
  EXIT_SOON: "orange",
  EXIT_NOW: "red",
  BLOCKED: "default"
};

const decisionText: Record<FundingArbitrageDecision, string> = {
  ENTER: "\u8fdb\u5165",
  HOLD: "\u6301\u6709",
  EXIT_SOON: "\u89c2\u5bdf\u9000\u51fa",
  EXIT_NOW: "\u7acb\u5373\u9000\u51fa",
  BLOCKED: "\u963b\u65ad"
};

const adlColor: Record<AdlRiskLevel, string> = {
  LOW: "green",
  MEDIUM: "gold",
  HIGH: "orange",
  BLOCKED: "red"
};

const fundingSourceText: Record<FundingSource, string> = {
  predicted: "\u9884\u6d4b",
  fallback_current: "\u5f53\u524d\u56de\u9000",
  missing: "\u7f3a\u5931"
};

function pct(value: number | null | undefined, digits = 3): string {
  return typeof value === "number" && Number.isFinite(value) ? `${value.toFixed(digits)}%` : "-";
}

function compactMoney(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "-";
  }
  if (value >= 1_000_000) {
    return `${(value / 1_000_000).toFixed(2)}M`;
  }
  if (value >= 1_000) {
    return `${(value / 1_000).toFixed(1)}K`;
  }
  return value.toFixed(0);
}

function leg(exchange: string, marketType: string): string {
  return `${exchange} ${marketType}`;
}

function normalizeSettings(value?: Partial<FundingArbitrageSettings>): FundingArbitrageSettings {
  return {
    ...defaultFundingSettings,
    ...(value ?? {})
  };
}

function settingsToForm(settings: FundingArbitrageSettings): FundingSettingsForm {
  return {
    ...settings,
    min_volume_24h_k: Math.round(settings.min_volume_24h_usdt / 1000)
  };
}

function settingsFromForm(values: Partial<FundingSettingsForm>): FundingArbitrageSettings {
  const merged = {
    ...settingsToForm(defaultFundingSettings),
    ...values
  };
  const { min_volume_24h_k: minVolumeK, ...settings } = merged;
  return {
    ...defaultFundingSettings,
    ...settings,
    min_volume_24h_usdt: (minVolumeK ?? 0) * 1000
  };
}

const columns: ColumnsType<FundingArbitrageCandidate> = [
  {
    title: "\u51b3\u7b56",
    dataIndex: "decision",
    fixed: "left",
    width: 104,
    render: (value: FundingArbitrageDecision) => <Tag color={decisionColor[value]}>{decisionText[value]}</Tag>
  },
  {
    title: "\u6807\u7684",
    dataIndex: "symbol",
    fixed: "left",
    width: 132,
    render: (value: string, row) => (
      <Space size={4} wrap>
        <Typography.Text strong>{value}</Typography.Text>
        <Tag>{row.type}</Tag>
        {row.uses_hyperliquid ? <Tag color="cyan">Hyper</Tag> : null}
      </Space>
    )
  },
  {
    title: "\u591a\u5934",
    width: 142,
    render: (_, row) => leg(row.long_exchange, row.long_market_type)
  },
  {
    title: "\u7a7a\u5934",
    width: 142,
    render: (_, row) => leg(row.short_exchange, row.short_market_type)
  },
  {
    title: "\u4e0b\u5468\u671f\u8d44\u91d1\u5dee",
    dataIndex: "next_funding_edge_pct",
    width: 126,
    align: "right",
    sorter: (a, b) => (a.next_funding_edge_pct ?? -999) - (b.next_funding_edge_pct ?? -999),
    render: (value: number | null) => (
      <Typography.Text type={typeof value === "number" && value >= 0 ? "success" : "danger"}>
        {pct(value)}
      </Typography.Text>
    )
  },
  {
    title: "\u7efc\u5408\u9884\u671f",
    dataIndex: "expected_cycle_pnl_pct",
    width: 116,
    align: "right",
    defaultSortOrder: "descend",
    sorter: (a, b) => a.expected_cycle_pnl_pct - b.expected_cycle_pnl_pct,
    render: (value: number) => (
      <Typography.Text strong type={value >= 0 ? "success" : "danger"}>
        {pct(value)}
      </Typography.Text>
    )
  },
  {
    title: "\u57fa\u5dee\u98ce\u9669",
    width: 128,
    align: "right",
    render: (_, row) => (
      <Space direction="vertical" size={0}>
        <Typography.Text>{pct(row.basis_risk_penalty_pct)}</Typography.Text>
        <Typography.Text type="secondary">{`\u5bbd ${pct(row.basis_width_pct)}`}</Typography.Text>
      </Space>
    )
  },
  {
    title: "\u7ed3\u7b97",
    dataIndex: "minutes_to_settlement",
    width: 88,
    align: "right",
    render: (value: number | null) => (typeof value === "number" ? `${value.toFixed(0)}m` : "-")
  },
  {
    title: "ADL proxy",
    width: 116,
    render: (_, row) => (
      <Space direction="vertical" size={0}>
        <Tag color={adlColor[row.adl_risk_level]}>{row.adl_risk_level}</Tag>
        <Typography.Text type="secondary">{row.adl_risk_score.toFixed(1)}</Typography.Text>
      </Space>
    )
  },
  {
    title: "\u8d44\u91d1\u6765\u6e90",
    dataIndex: "funding_source",
    width: 112,
    render: (value: FundingSource) => fundingSourceText[value]
  },
  {
    title: "\u6d41\u52a8\u6027",
    width: 128,
    align: "right",
    render: (_, row) => (
      <Space direction="vertical" size={0}>
        <Typography.Text>{compactMoney(row.volume_24h_usdt)}</Typography.Text>
        <Typography.Text type="secondary">{compactMoney(row.depth_usdt)}</Typography.Text>
      </Space>
    )
  },
  {
    title: "\u539f\u56e0",
    dataIndex: "decision_reasons",
    render: (values: string[]) => values.join("; ")
  }
];

export function FundingArbitragePage() {
  const [form] = Form.useForm<FundingSettingsForm>();
  const [settings, setSettings] = useState<FundingArbitrageSettings>(defaultFundingSettings);
  const [preview, setPreview] = useState<FundingArbitragePreview | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [nextSettings, nextPreview] = await Promise.all([
        getFundingArbitrageSettings(),
        getFundingArbitragePreview()
      ]);
      const normalized = normalizeSettings(nextSettings);
      setSettings(normalized);
      form.setFieldsValue(settingsToForm(normalized));
      setPreview(nextPreview);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setLoading(false);
    }
  }, [form]);

  useEffect(() => {
    form.setFieldsValue(settingsToForm(defaultFundingSettings));
    void load();
  }, [form, load]);

  const save = async () => {
    setSaving(true);
    setError("");
    try {
      const values = await form.validateFields();
      const saved = normalizeSettings(await updateFundingArbitrageSettings(settingsFromForm(values)));
      setSettings(saved);
      form.setFieldsValue(settingsToForm(saved));
      setPreview(await getFundingArbitragePreview());
      message.success("\u8d44\u91d1\u8d39\u7387\u5957\u5229\u53c2\u6570\u5df2\u4fdd\u5b58");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="page funding-page">
      {error ? <Alert type="error" message={error} showIcon /> : null}
      <section className="toolbar">
        <div className="toolbar-controls">
          <Typography.Title level={4}>{"\u8d44\u91d1\u8d39\u7387\u5957\u5229"}</Typography.Title>
          <Typography.Text type="secondary">
            {"\u6eda\u52a8\u4e00\u5468\u671f carry \u9884\u89c8"}
          </Typography.Text>
        </div>
        <div className="toolbar-actions">
          <Button icon={<ReloadOutlined />} onClick={() => void load()} loading={loading}>
            {"\u5237\u65b0"}
          </Button>
        </div>
      </section>

      <section className="metric-row funding-metrics">
        <Statistic
          title={"\u5019\u9009"}
          value={preview?.displayed_candidates ?? 0}
          suffix={`/ ${preview?.total_pairs_evaluated ?? 0}`}
        />
        <Statistic title="ENTER" value={preview?.enter_count ?? 0} />
        <Statistic title="HOLD" value={preview?.hold_count ?? 0} />
        <Statistic title={"\u9000\u51fa"} value={preview?.exit_count ?? 0} />
        <Statistic title={"\u963b\u65ad"} value={preview?.blocked_count ?? 0} />
        <Statistic title={"ADL \u963b\u65ad"} value={preview?.blocked_adl_risk ?? 0} />
      </section>

      <section className="panel panel-wide">
        <Form
          form={form}
          layout="vertical"
          disabled={loading || saving}
          onValuesChange={() => {
            const values = form.getFieldsValue(true) as Partial<FundingSettingsForm>;
            setSettings(settingsFromForm({ ...settingsToForm(settings), ...values }));
          }}
        >
          <div className="funding-settings-grid">
            <Form.Item label={"\u542f\u7528\u9884\u89c8"} name="enabled" valuePropName="checked">
              <Switch />
            </Form.Item>
            <Form.Item label={"Hyper \u4f18\u5148"} name="prefer_hyperliquid" valuePropName="checked">
              <Switch />
            </Form.Item>
            <Form.Item label={"\u6700\u591a\u5019\u9009"} name="max_candidates" rules={[{ required: true }]}>
              <InputNumber min={1} max={500} className="wide-input" />
            </Form.Item>
            <Form.Item
              label={"\u6bcf\u6807\u7684\u8d44\u91d1 USDT"}
              name="notional_per_symbol_usdt"
              rules={[{ required: true }]}
            >
              <InputNumber min={1} step={10} className="wide-input" />
            </Form.Item>
            <Form.Item label={"\u8fdb\u5165\u7efc\u5408\u9608\u503c"} name="min_entry_edge_pct" rules={[{ required: true }]}>
              <InputNumber step={0.01} suffix="%" className="wide-input" />
            </Form.Item>
            <Form.Item label={"\u6301\u6709\u7efc\u5408\u9608\u503c"} name="min_hold_edge_pct" rules={[{ required: true }]}>
              <InputNumber step={0.01} suffix="%" className="wide-input" />
            </Form.Item>
            <Form.Item label={"\u9000\u51fa\u7efc\u5408\u9608\u503c"} name="min_exit_edge_pct" rules={[{ required: true }]}>
              <InputNumber step={0.01} suffix="%" className="wide-input" />
            </Form.Item>
            <Form.Item label={"\u539f\u59cb\u8d44\u8d39\u4e0b\u9650"} name="min_funding_edge_pct" rules={[{ required: true }]}>
              <InputNumber step={0.01} suffix="%" className="wide-input" />
            </Form.Item>
            <Form.Item label={"24h \u6210\u4ea4\u989d K"} name="min_volume_24h_k" rules={[{ required: true }]}>
              <InputNumber min={0} step={100} suffix="K" className="wide-input" />
            </Form.Item>
            <Form.Item label={"\u6700\u5927\u57fa\u5dee\u5bbd\u5ea6"} name="max_basis_width_pct" rules={[{ required: true }]}>
              <InputNumber min={0} step={0.1} suffix="%" className="wide-input" />
            </Form.Item>
            <Form.Item
              label={"\u6700\u5927\u6807\u8bb0\u504f\u79bb"}
              name="max_mark_index_deviation_pct"
              rules={[{ required: true }]}
            >
              <InputNumber min={0} step={0.1} suffix="%" className="wide-input" />
            </Form.Item>
            <Form.Item label={"ADL \u963b\u65ad\u5206"} name="adl_block_score" rules={[{ required: true }]}>
              <InputNumber min={0} step={5} className="wide-input" />
            </Form.Item>
            <Form.Item label={"\u6700\u5c0f\u7ed3\u7b97\u5206\u949f"} name="min_minutes_to_settlement" rules={[{ required: true }]}>
              <InputNumber min={0} step={1} className="wide-input" />
            </Form.Item>
            <Form.Item label={"\u6700\u5927\u7ed3\u7b97\u5206\u949f"} name="max_minutes_to_settlement" rules={[{ required: true }]}>
              <InputNumber min={1} step={5} className="wide-input" />
            </Form.Item>
            <Form.Item label={"\u6ed1\u70b9\u7f13\u51b2"} name="slippage_buffer_pct" rules={[{ required: true }]}>
              <InputNumber min={0} step={0.01} suffix="%" className="wide-input" />
            </Form.Item>
            <Form.Item label={"\u57fa\u5dee\u6743\u91cd"} name="basis_risk_weight" rules={[{ required: true }]}>
              <InputNumber min={0} step={0.1} className="wide-input" />
            </Form.Item>
            <Form.Item label={"\u7f6e\u4fe1\u60e9\u7f5a"} name="confidence_penalty_pct" rules={[{ required: true }]}>
              <InputNumber min={0} step={0.01} suffix="%" className="wide-input" />
            </Form.Item>
            <Form.Item label={"\u6760\u6746"} name="leverage" rules={[{ required: true }]}>
              <InputNumber min={1} step={1} className="wide-input" />
            </Form.Item>
          </div>
          <Button type="primary" icon={<SaveOutlined />} onClick={() => void save()} loading={saving}>
            {"\u4fdd\u5b58\u7b56\u7565\u53c2\u6570"}
          </Button>
        </Form>
      </section>

      <Table
        className="opportunity-table funding-table"
        columns={columns}
        dataSource={preview?.candidates ?? []}
        loading={loading}
        rowKey="id"
        pagination={{ pageSize: 50, showSizeChanger: true }}
        scroll={{ x: 1560 }}
        size="small"
        tableLayout="fixed"
      />
    </div>
  );
}
