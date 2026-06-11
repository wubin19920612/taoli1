import { ReloadOutlined, SyncOutlined } from "@ant-design/icons";
import {
  Alert,
  Button,
  Checkbox,
  InputNumber,
  Space,
  Statistic,
  Table,
  Tag,
  Typography
} from "antd";
import type { ColumnsType } from "antd/es/table";
import dayjs from "dayjs";
import utc from "dayjs/plugin/utc";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  getTradfiPerpMonitorPreview,
  refreshTradfiPerpMonitorPreview
} from "../api/client";
import type {
  TradfiPerpDirection,
  TradfiPerpLeg,
  TradfiPerpMonitorPreview,
  TradfiPerpMonitorRow
} from "../api/types";

dayjs.extend(utc);

function pct(value: number | null | undefined, digits = 4): string {
  return typeof value === "number" && Number.isFinite(value) ? `${value.toFixed(digits)}%` : "-";
}

function signedPct(value: number | null | undefined, digits = 4): string {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "-";
  }
  return `${value >= 0 ? "+" : ""}${value.toFixed(digits)}%`;
}

function price(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "-";
  }
  if (value >= 1000) {
    return value.toFixed(2);
  }
  if (value >= 1) {
    return value.toFixed(4).replace(/0+$/, "").replace(/\.$/, "");
  }
  return value.toPrecision(5);
}

function money(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "-";
  }
  if (value >= 1_000_000_000) {
    return `${(value / 1_000_000_000).toFixed(2)}B`;
  }
  if (value >= 1_000_000) {
    return `${(value / 1_000_000).toFixed(2)}M`;
  }
  if (value >= 1_000) {
    return `${(value / 1_000).toFixed(1)}K`;
  }
  return value.toFixed(0);
}

function time(value: string | null | undefined): string {
  return value ? dayjs.utc(value).utcOffset(8).format("MM-DD HH:mm:ss") : "-";
}

const directionLabel: Record<TradfiPerpDirection, string> = {
  LONG_HL_SHORT_BINANCE: "\u591a Hyper / \u7a7a Binance",
  LONG_BINANCE_SHORT_HL: "\u591a Binance / \u7a7a Hyper"
};

const riskColor: Record<string, string> = {
  LOW_VOLUME: "orange",
  UNKNOWN_VOLUME: "default",
  MISSING_FUNDING: "red",
  MISSING_FUNDING_INTERVAL: "red",
  HL_MARK_INDEX_DEVIATION: "gold",
  BINANCE_MARK_INDEX_DEVIATION: "gold",
  HL_MAINNET_PERP: "blue"
};

function legSummary(leg: TradfiPerpLeg) {
  return (
    <div className="tradfi-leg-cell">
      <Typography.Text strong>{leg.raw_symbol}</Typography.Text>
      <Typography.Text type="secondary">{`mid ${price(leg.mid)} / mark ${price(leg.mark_price)}`}</Typography.Text>
      <Typography.Text type="secondary">{`fund ${signedPct(leg.funding_rate_pct)} / h ${signedPct(leg.funding_rate_hourly_pct)}`}</Typography.Text>
      <Typography.Text type="secondary">{`vol ${money(leg.volume_24h_usdt)} / ${time(leg.timestamp)} UTC+8`}</Typography.Text>
    </div>
  );
}

function rowDirection(row: TradfiPerpMonitorRow) {
  if (!row.best_price_direction) {
    return "-";
  }
  return directionLabel[row.best_price_direction];
}

function fundingDirection(row: TradfiPerpMonitorRow) {
  if (!row.best_funding_direction) {
    return "-";
  }
  return directionLabel[row.best_funding_direction];
}

function buildColumns(): ColumnsType<TradfiPerpMonitorRow> {
  return [
    {
      title: "\u6807\u7684",
      dataIndex: "asset",
      fixed: "left",
      width: 152,
      render: (value: string, row) => (
        <Space size={4} wrap>
          <Typography.Text strong>{value}</Typography.Text>
          {value !== row.binance_base_asset ? <Tag color="cyan">{row.binance_base_asset}</Tag> : null}
          <Tag>{row.hl_dex}</Tag>
        </Space>
      )
    },
    {
      title: "Hyperliquid",
      width: 220,
      render: (_, row) => legSummary(row.hl)
    },
    {
      title: "Binance",
      width: 220,
      render: (_, row) => legSummary(row.binance)
    },
    {
      title: "\u4ef7\u5dee",
      width: 190,
      align: "right",
      sorter: (a, b) => Math.abs(a.mark_spread_pct ?? 0) - Math.abs(b.mark_spread_pct ?? 0),
      render: (_, row) => (
        <div className="tradfi-stack-cell">
          <Typography.Text strong type={(row.mark_spread_pct ?? 0) >= 0 ? "success" : "danger"}>
            {`mark ${signedPct(row.mark_spread_pct)}`}
          </Typography.Text>
          <Typography.Text type="secondary">{`mid ${signedPct(row.mid_spread_pct)}`}</Typography.Text>
          <Typography.Text type="secondary">{`index ${signedPct(row.index_spread_pct)}`}</Typography.Text>
        </div>
      )
    },
    {
      title: "\u5f00\u4ed3\u65b9\u5411",
      width: 190,
      align: "right",
      sorter: (a, b) => (a.best_open_edge_pct ?? -999) - (b.best_open_edge_pct ?? -999),
      render: (_, row) => (
        <div className="tradfi-stack-cell">
          <Typography.Text strong>{rowDirection(row)}</Typography.Text>
          <Typography.Text type={(row.best_open_edge_pct ?? 0) >= 0 ? "success" : "danger"}>
            {signedPct(row.best_open_edge_pct)}
          </Typography.Text>
        </div>
      )
    },
    {
      title: "\u8d44\u91d1\u8d39\u65b9\u5411",
      width: 210,
      align: "right",
      defaultSortOrder: "descend",
      sorter: (a, b) =>
        Math.abs(a.best_funding_edge_hourly_pct ?? 0) - Math.abs(b.best_funding_edge_hourly_pct ?? 0),
      render: (_, row) => (
        <div className="tradfi-stack-cell">
          <Typography.Text strong>{fundingDirection(row)}</Typography.Text>
          <Typography.Text type={(row.best_funding_edge_hourly_pct ?? 0) >= 0 ? "success" : "danger"}>
            {`h ${signedPct(row.best_funding_edge_hourly_pct)}`}
          </Typography.Text>
          <Typography.Text type="secondary">{`d ${signedPct(row.best_funding_edge_daily_pct)}`}</Typography.Text>
        </div>
      )
    },
    {
      title: "\u6d41\u52a8\u6027",
      dataIndex: "min_volume_24h_usdt",
      width: 104,
      align: "right",
      sorter: (a, b) => (a.min_volume_24h_usdt ?? 0) - (b.min_volume_24h_usdt ?? 0),
      render: (value: number | null) => money(value)
    },
    {
      title: "\u98ce\u9669",
      dataIndex: "risk_labels",
      width: 220,
      render: (values: string[]) => (
        <Space size={4} wrap>
          {values.length === 0 ? <Tag color="green">OK</Tag> : null}
          {values.map((value) => (
            <Tag key={value} color={riskColor[value] ?? "default"}>
              {value}
            </Tag>
          ))}
        </Space>
      )
    }
  ];
}

function expandedRow(row: TradfiPerpMonitorRow) {
  return (
    <div className="tradfi-expanded-row">
      <div>
        <Typography.Text type="secondary">Hyper raw</Typography.Text>
        <Typography.Text>{row.hl_raw_symbol}</Typography.Text>
      </div>
      <div>
        <Typography.Text type="secondary">Binance symbol</Typography.Text>
        <Typography.Text>{row.binance_symbol}</Typography.Text>
      </div>
      <div>
        <Typography.Text type="secondary">\u591a Hyper / \u7a7a Binance</Typography.Text>
        <Typography.Text>
          {`${signedPct(row.open_long_hl_short_binance_pct)} / funding h ${signedPct(row.funding_edge_long_hl_short_binance_hourly_pct)}`}
        </Typography.Text>
      </div>
      <div>
        <Typography.Text type="secondary">\u591a Binance / \u7a7a Hyper</Typography.Text>
        <Typography.Text>
          {`${signedPct(row.open_long_binance_short_hl_pct)} / funding h ${signedPct(row.funding_edge_long_binance_short_hl_hourly_pct)}`}
        </Typography.Text>
      </div>
    </div>
  );
}

export function TradfiPerpMonitorPage() {
  const [preview, setPreview] = useState<TradfiPerpMonitorPreview | null>(null);
  const [loading, setLoading] = useState(false);
  const [liveLoading, setLiveLoading] = useState(false);
  const [error, setError] = useState("");
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [liveMode, setLiveMode] = useState(true);
  const [minVolumeK, setMinVolumeK] = useState(1000);
  const [maxMarkDeviation, setMaxMarkDeviation] = useState(2);

  const params = useMemo(
    () => ({
      min_volume_24h_k: minVolumeK,
      max_mark_index_deviation_pct: maxMarkDeviation,
      max_rows: 500
    }),
    [maxMarkDeviation, minVolumeK]
  );

  const loadSnapshot = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setPreview(await getTradfiPerpMonitorPreview({ ...params, live: liveMode }));
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setLoading(false);
    }
  }, [liveMode, params]);

  const refreshLive = useCallback(async () => {
    setLiveLoading(true);
    setError("");
    try {
      setPreview(await refreshTradfiPerpMonitorPreview(params));
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setLiveLoading(false);
    }
  }, [params]);

  useEffect(() => {
    void loadSnapshot();
  }, [loadSnapshot]);

  useEffect(() => {
    if (!autoRefresh) {
      return undefined;
    }
    const id = window.setInterval(() => {
      void loadSnapshot();
    }, 10_000);
    return () => window.clearInterval(id);
  }, [autoRefresh, loadSnapshot]);

  const columns = buildColumns();
  const rows = preview?.rows ?? [];

  return (
    <div className="page tradfi-page">
      {error ? <Alert type="error" message={error} showIcon /> : null}
      <section className="toolbar">
        <div className="toolbar-controls">
          <Typography.Title level={4}>{"Hyperliquid HIP-3 / Binance TradFi Perp \u76d1\u63a7"}</Typography.Title>
          <Typography.Text type="secondary">
            {preview ? `\u6700\u540e\u89c2\u6d4b ${time(preview.observed_at)} UTC+8` : "\u7b49\u5f85\u6570\u636e"}
          </Typography.Text>
        </div>
        <Space wrap className="toolbar-actions">
          <InputNumber
            min={0}
            step={100}
            addonBefore="24h K"
            value={minVolumeK}
            onChange={(value) => setMinVolumeK(Number(value ?? 0))}
          />
          <InputNumber
            min={0}
            step={0.1}
            addonBefore="mark/index %"
            value={maxMarkDeviation}
            onChange={(value) => setMaxMarkDeviation(Number(value ?? 0))}
          />
          <Checkbox checked={autoRefresh} onChange={(event) => setAutoRefresh(event.target.checked)}>
            {"10s \u81ea\u52a8"}
          </Checkbox>
          <Checkbox checked={liveMode} onChange={(event) => setLiveMode(event.target.checked)}>
            {"\u5b9e\u65f6\u6a21\u5f0f"}
          </Checkbox>
          <Button icon={<ReloadOutlined />} onClick={() => void loadSnapshot()} loading={loading}>
            {liveMode ? "\u5237\u65b0" : "\u8bfb\u5feb\u7167"}
          </Button>
          <Button type="primary" icon={<SyncOutlined />} onClick={() => void refreshLive()} loading={liveLoading}>
            {"\u5b9e\u65f6\u5237\u65b0"}
          </Button>
        </Space>
      </section>

      <section className="metric-row tradfi-metrics">
        <Statistic title={"\u5df2\u5339\u914d"} value={preview?.matched_count ?? 0} />
        <Statistic title={"Hyper \u8d44\u4ea7"} value={preview?.hyperliquid_asset_count ?? 0} />
        <Statistic title={"Binance TradFi"} value={preview?.binance_symbol_count ?? 0} />
        <Statistic
          title={"\u6700\u5927\u8d44\u91d1\u8d39 h"}
          value={Math.max(...rows.map((row) => Math.abs(row.best_funding_edge_hourly_pct ?? 0)), 0)}
          precision={4}
          suffix="%"
        />
        <Statistic
          title={"\u6700\u5927 mark \u4ef7\u5dee"}
          value={Math.max(...rows.map((row) => Math.abs(row.mark_spread_pct ?? 0)), 0)}
          precision={4}
          suffix="%"
        />
      </section>

      <Table
        className="opportunity-table tradfi-table"
        columns={columns}
        dataSource={rows}
        loading={loading || liveLoading}
        rowKey="id"
        pagination={{ pageSize: 50, showSizeChanger: true }}
        scroll={{ x: 1510 }}
        size="small"
        tableLayout="fixed"
        expandable={{ expandedRowRender: expandedRow, rowExpandable: () => true }}
      />
    </div>
  );
}
