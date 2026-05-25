import {
  ArrowDownOutlined,
  ArrowUpOutlined,
  AreaChartOutlined,
  EyeInvisibleOutlined,
  EyeOutlined,
  ExperimentOutlined
} from "@ant-design/icons";
import { Button, Space, Table, Tag, Tooltip, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import dayjs from "dayjs";

import type { Opportunity } from "../api/types";
import { RiskTags } from "./RiskTags";

interface OpportunityTableProps {
  opportunities: Opportunity[];
  loading: boolean;
  blockedSymbols?: string[];
  actionLoadingSymbol?: string | null;
  previewLoadingSymbol?: string | null;
  onToggleSymbol?: (symbol: string, block: boolean) => void;
  onPreviewAstro?: (opportunity: Opportunity) => void;
  onOpenHistory?: (opportunity: Opportunity) => void;
}

function pct(value: number | null | undefined): string {
  return typeof value === "number" ? `${value.toFixed(3)}%` : "-";
}

function nextTime(value: string | null | undefined): string {
  return value ? dayjs(value).format("HH:mm") : "-";
}

function interval(value: number | null | undefined): string {
  return typeof value === "number" ? `${value}h` : "-";
}

function money(value: number | null | undefined): string {
  if (typeof value !== "number") {
    return "-";
  }
  if (value >= 1_000_000_000) {
    return `${(value / 1_000_000_000).toFixed(2)}B`;
  }
  if (value >= 1_000_000) {
    return `${(value / 1_000_000).toFixed(2)}M`;
  }
  if (value >= 1_000) {
    return `${(value / 1_000).toFixed(2)}K`;
  }
  return value.toFixed(2);
}

const EXCHANGE_CODES: Record<string, string> = {
  aster: "as",
  binance: "bn",
  bitget: "bg",
  bybit: "bb",
  gate: "gt",
  htx: "ht",
  hyperliquid: "hl",
  okx: "ok"
};

function exchangeCode(exchange: string): string {
  const normalized = exchange.trim().toLowerCase();
  if (EXCHANGE_CODES[normalized]) {
    return EXCHANGE_CODES[normalized];
  }
  const compact = normalized.replace(/[^a-z0-9]/g, "");
  return compact ? compact.slice(0, 4) : exchange;
}

function leg(exchange: string, marketType: string, side: "buy" | "sell") {
  const icon = side === "buy" ? <ArrowDownOutlined /> : <ArrowUpOutlined />;
  const color = side === "buy" ? "green" : "red";
  const fullName = `${exchange} ${marketType}`;
  return (
    <div className="leg-cell">
      <Tag color={color} icon={icon} className="leg-tag">
        {side.toUpperCase()}
      </Tag>
      <Typography.Text className="leg-text" title={fullName}>
        {exchangeCode(exchange)}
      </Typography.Text>
    </div>
  );
}

function fundingPair(left: number | null | undefined, right: number | null | undefined): string {
  return `${pct(left)} / ${pct(right)}`;
}

function sideNextCycleFundingRate(
  marketType: string,
  nextRate: number | null | undefined,
  currentRate: number | null | undefined
): number | null {
  if (marketType === "spot") {
    return 0;
  }
  if (typeof nextRate === "number") {
    return nextRate;
  }
  return typeof currentRate === "number" ? currentRate : null;
}

function nextCycleFundingEdge(row: Opportunity): number | null {
  if (typeof row.net_funding_next_pct === "number") {
    return row.net_funding_next_pct;
  }
  const buyRate = sideNextCycleFundingRate(
    row.buy_market_type,
    row.funding_next_rate_buy_pct,
    row.funding_rate_buy_pct
  );
  const sellRate = sideNextCycleFundingRate(
    row.sell_market_type,
    row.funding_next_rate_sell_pct,
    row.funding_rate_sell_pct
  );
  if (typeof buyRate === "number" && typeof sellRate === "number") {
    return sellRate - buyRate;
  }
  return row.net_funding_pct;
}

function normalizeSymbol(value: string): string {
  return value.toUpperCase().replace(/[-_]/g, "");
}

function isBlocked(symbol: string, blockedSymbols: string[] | undefined): boolean {
  const normalized = normalizeSymbol(symbol);
  return (blockedSymbols ?? []).some((item) => normalizeSymbol(item) === normalized);
}

function FundingCell({ row }: { row: Opportunity }) {
  const cycleFundingEdge = nextCycleFundingEdge(row);
  const cycleType =
    typeof cycleFundingEdge === "number" && cycleFundingEdge < 0 ? "danger" : "secondary";
  return (
    <div className="funding-cell">
      <div className="funding-row">
        <span className="funding-label">{"\u5f53\u524d"}</span>
        <Typography.Text className="funding-value">
          {fundingPair(row.funding_rate_buy_pct, row.funding_rate_sell_pct)}
        </Typography.Text>
      </div>
      <div className="funding-row">
        <span className="funding-label">{"\u9884\u6d4b"}</span>
        <Typography.Text className="funding-value">
          {fundingPair(row.funding_next_rate_buy_pct, row.funding_next_rate_sell_pct)}
        </Typography.Text>
      </div>
      <div className="funding-row">
        <span className="funding-label">{"\u5468\u671f\u51c0"}</span>
        <Typography.Text className="funding-value" type={cycleType}>
          {pct(cycleFundingEdge)}
        </Typography.Text>
      </div>
      <div className="funding-row">
        <span className="funding-label">{"\u7ed3\u7b97"}</span>
        <Typography.Text className="funding-value" type="secondary">
          {`${nextTime(row.funding_next_time_buy)} / ${nextTime(row.funding_next_time_sell)} | ${interval(row.buy_funding_interval_hours)} / ${interval(row.sell_funding_interval_hours)}`}
        </Typography.Text>
      </div>
    </div>
  );
}

function buildColumns(
  blockedSymbols: string[] | undefined,
  actionLoadingSymbol: string | null | undefined,
  previewLoadingSymbol: string | null | undefined,
  onToggleSymbol: ((symbol: string, block: boolean) => void) | undefined,
  onPreviewAstro: ((opportunity: Opportunity) => void) | undefined,
  onOpenHistory: ((opportunity: Opportunity) => void) | undefined
): ColumnsType<Opportunity> {
  return [
    {
      title: "",
      fixed: "left",
      width: 44,
      render: (_, row) => {
        const blocked = isBlocked(row.symbol, blockedSymbols);
        const normalized = normalizeSymbol(row.symbol);
        return onToggleSymbol ? (
          <Button
            type="text"
            size="small"
            icon={blocked ? <EyeOutlined /> : <EyeInvisibleOutlined />}
            aria-label={`${blocked ? "\u53d6\u6d88\u5c4f\u853d" : "\u5c4f\u853d"} ${row.symbol}`}
            loading={actionLoadingSymbol === normalized}
            onClick={() => onToggleSymbol(row.symbol, !blocked)}
          />
        ) : null;
      }
    },
    {
      title: "",
      fixed: "left",
      width: 44,
      render: (_, row) => {
        const normalized = normalizeSymbol(row.symbol);
        return onPreviewAstro ? (
          <Tooltip title="Astro dry-run">
            <Button
              type="text"
              size="small"
              icon={<ExperimentOutlined />}
              aria-label={`Astro ${row.symbol}`}
              loading={previewLoadingSymbol === normalized}
              onClick={() => onPreviewAstro(row)}
            />
          </Tooltip>
        ) : null;
      }
    },
    {
      title: "",
      fixed: "left",
      width: 44,
      render: (_, row) =>
        onOpenHistory ? (
          <Tooltip title="价差历史统计">
            <Button
              type="text"
              size="small"
              icon={<AreaChartOutlined />}
              aria-label={`价差历史 ${row.symbol}`}
              onClick={() => onOpenHistory(row)}
            />
          </Tooltip>
        ) : null
    },
    {
      title: "Symbol",
      dataIndex: "symbol",
      fixed: "left",
      width: 118,
      render: (_, row) => (
        <Space direction="vertical" size={2} className="symbol-cell">
          <Typography.Text strong>{row.symbol}</Typography.Text>
          <Tag>{row.type}</Tag>
        </Space>
      )
    },
    {
      title: "Buy leg",
      width: 88,
      render: (_, row) => leg(row.buy_exchange, row.buy_market_type, "buy")
    },
    {
      title: "Sell leg",
      width: 88,
      render: (_, row) => leg(row.sell_exchange, row.sell_market_type, "sell")
    },
    {
      title: "Open spread",
      dataIndex: "open_spread_pct",
      width: 108,
      align: "right",
      sorter: (a, b) => a.open_spread_pct - b.open_spread_pct,
      defaultSortOrder: "descend",
      render: (value: number) => <Typography.Text strong>{pct(value)}</Typography.Text>
    },
    {
      title: "Net fee adj.",
      dataIndex: "fee_adjusted_open_pct",
      width: 104,
      align: "right",
      sorter: (a, b) => a.fee_adjusted_open_pct - b.fee_adjusted_open_pct,
      render: (value: number) => (
        <Typography.Text type={value >= 0 ? "success" : "danger"}>{pct(value)}</Typography.Text>
      )
    },
    {
      title: "Close spread",
      dataIndex: "close_spread_pct",
      width: 104,
      align: "right",
      render: (value: number) => pct(value)
    },
    {
      title: "Funding",
      width: 276,
      render: (_, row) => <FundingCell row={row} />
    },
    {
      title: "24h volume",
      width: 134,
      align: "right",
      render: (_, row) => `${money(row.buy_volume_24h_usdt)} / ${money(row.sell_volume_24h_usdt)}`
    },
    {
      title: "Risk",
      dataIndex: "risk_labels",
      width: 224,
      render: (labels: string[]) => <RiskTags labels={labels} />
    },
    {
      title: "Updated",
      dataIndex: "last_seen_at",
      width: 104,
      render: (value: string) => dayjs(value).format("HH:mm:ss")
    }
  ];
}

export function OpportunityTable({
  opportunities,
  loading,
  blockedSymbols,
  actionLoadingSymbol,
  previewLoadingSymbol,
  onToggleSymbol,
  onPreviewAstro,
  onOpenHistory
}: OpportunityTableProps) {
  return (
    <Table
      className="opportunity-table"
      columns={buildColumns(
        blockedSymbols,
        actionLoadingSymbol,
        previewLoadingSymbol,
        onToggleSymbol,
        onPreviewAstro,
        onOpenHistory
      )}
      dataSource={opportunities}
      loading={loading}
      rowKey="id"
      pagination={{ pageSize: 50, showSizeChanger: true }}
      scroll={{ x: 1480 }}
      size="small"
      tableLayout="fixed"
    />
  );
}
