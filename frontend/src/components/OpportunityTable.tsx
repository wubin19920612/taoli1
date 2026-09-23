import {
  AreaChartOutlined,
  EyeInvisibleOutlined,
  EyeOutlined,
  ExperimentOutlined,
  LineChartOutlined
} from "@ant-design/icons";
import { Button, Space, Table, Tag, Tooltip, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import dayjs from "dayjs";

import type { MarketType, Opportunity } from "../api/types";
import { marketTypeText } from "../constants/marketLabels";
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

const EXCHANGE_LABELS: Record<string, string> = {
  aster: "Aster",
  binance: "Binance",
  bitget: "Bitget",
  bybit: "Bybit",
  gate: "Gate",
  htx: "HTX",
  hyperliquid: "Hyper",
  lighter: "Lighter",
  "rh-lighter": "RH Lighter",
  okx: "OKX"
};

function exchangeLabel(exchange: string): string {
  const normalized = exchange.trim().toLowerCase();
  if (EXCHANGE_LABELS[normalized]) {
    return EXCHANGE_LABELS[normalized];
  }
  return exchange;
}

function leg(
  exchange: string,
  marketType: string,
  rawSymbol?: string | null,
  canonicalSymbol?: string,
  dex?: string | null,
  priceMultiplier?: number,
  contractSizeMultiplier?: number | null
) {
  const rawSuffix =
    rawSymbol && canonicalSymbol && normalizeSymbol(rawSymbol) !== normalizeSymbol(canonicalSymbol)
      ? rawSymbol
      : "";
  const marketLabel = marketTypeText(exchange, marketType, rawSymbol, canonicalSymbol);
  const meta = [
    marketLabel,
    dex ? `DEX ${dex}` : "",
    rawSuffix ? `原始 ${rawSuffix}` : "",
    priceMultiplier !== undefined ? `价格倍率 ${priceMultiplier}x` : "",
    contractSizeMultiplier !== undefined && contractSizeMultiplier !== null
      ? `数量乘数 ${contractSizeMultiplier}`
      : ""
  ].filter(Boolean).join(" ");
  const fullName = [exchange, meta].filter(Boolean).join(" ");
  return (
    <div className="leg-cell">
      <Typography.Text className="leg-text" title={fullName}>
        {exchangeLabel(exchange)}
      </Typography.Text>
      <Typography.Text className="leg-meta" title={fullName}>
        {meta || marketLabel}
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

function sideCurrentFundingRate(
  marketType: string,
  currentRate: number | null | undefined
): number | null {
  return marketType === "spot" ? 0 : (typeof currentRate === "number" ? currentRate : null);
}

function normalizedFundingEdges(row: Opportunity): { hourly: number | null; daily: number | null } {
  const hourly =
    typeof row.net_funding_next_hourly_pct === "number"
      ? row.net_funding_next_hourly_pct
      : row.net_funding_hourly_pct;
  const daily =
    typeof row.net_funding_next_daily_pct === "number"
      ? row.net_funding_next_daily_pct
      : typeof row.net_funding_daily_pct === "number"
        ? row.net_funding_daily_pct
        : typeof hourly === "number"
          ? hourly * 24
          : null;
  return { hourly, daily };
}

function periodFundingPct(value: number | null, period: string): string {
  return typeof value === "number" ? `${pct(value)}/${period}` : "-";
}

function normalizeSymbol(value: string): string {
  return value.toUpperCase().replace(/[-_/]/g, "");
}

function isBlocked(symbol: string, blockedSymbols: string[] | undefined): boolean {
  const normalized = normalizeSymbol(symbol);
  return (blockedSymbols ?? []).some((item) => normalizeSymbol(item) === normalized);
}

function routeSymbol(exchange: string, rawSymbol: string | null | undefined, canonicalSymbol: string): string {
  const value = rawSymbol?.trim() || canonicalSymbol;
  const separator = exchange === "hyperliquid" ? value.indexOf(":") : -1;
  return (separator > 0 ? value.slice(separator + 1) : value).toUpperCase();
}

function setLegIdentityParams(
  url: URL,
  key: 1 | 2,
  leg: {
    exchange: string;
    marketType: MarketType;
    rawSymbol?: string | null;
    canonicalSymbol: string;
    dex?: string | null;
    priceMultiplier?: number;
    contractSizeMultiplier?: number | null;
  }
): void {
  const rawSymbol = leg.rawSymbol?.trim() || leg.canonicalSymbol;
  url.searchParams.set(`leg${key}_exchange`, leg.exchange);
  url.searchParams.set(`leg${key}_market_type`, leg.marketType);
  url.searchParams.set(`leg${key}_symbol`, routeSymbol(leg.exchange, rawSymbol, leg.canonicalSymbol));
  url.searchParams.set(`leg${key}_raw_symbol`, rawSymbol);
  url.searchParams.set(`leg${key}_price_multiplier`, String(leg.priceMultiplier ?? 1));
  if (leg.dex) url.searchParams.set(`leg${key}_dex`, leg.dex);
  else url.searchParams.delete(`leg${key}_dex`);
  if (leg.contractSizeMultiplier !== null && leg.contractSizeMultiplier !== undefined) {
    url.searchParams.set(`leg${key}_contract_size_multiplier`, String(leg.contractSizeMultiplier));
  } else {
    url.searchParams.delete(`leg${key}_contract_size_multiplier`);
  }
}

function openPairSpread(row: Opportunity): void {
  const url = new URL(window.location.href);
  url.searchParams.set("page", "pair-monitor");
  setLegIdentityParams(url, 1, {
    exchange: row.buy_exchange,
    marketType: row.buy_market_type,
    rawSymbol: row.buy_raw_symbol,
    canonicalSymbol: row.symbol,
    dex: row.buy_dex,
    priceMultiplier: row.buy_price_multiplier,
    contractSizeMultiplier: row.buy_contract_size_multiplier
  });
  setLegIdentityParams(url, 2, {
    exchange: row.sell_exchange,
    marketType: row.sell_market_type,
    rawSymbol: row.sell_raw_symbol,
    canonicalSymbol: row.symbol,
    dex: row.sell_dex,
    priceMultiplier: row.sell_price_multiplier,
    contractSizeMultiplier: row.sell_contract_size_multiplier
  });
  url.searchParams.set("leg2_multiplier", "1");
  url.searchParams.set("hours", "4");
  url.searchParams.set("interval_minutes", "5");
  window.history.pushState({}, "", `${url.pathname}${url.search}${url.hash}`);
  window.dispatchEvent(new Event("taoli1:navigate"));
}

function FundingCell({ row }: { row: Opportunity }) {
  const normalizedFunding = normalizedFundingEdges(row);
  const normalizedType =
    typeof normalizedFunding.hourly === "number" && normalizedFunding.hourly < 0
      ? "danger"
      : "secondary";
  const currentBuyRate = sideCurrentFundingRate(row.buy_market_type, row.funding_rate_buy_pct);
  const currentSellRate = sideCurrentFundingRate(row.sell_market_type, row.funding_rate_sell_pct);
  const predictedBuyRate = sideNextCycleFundingRate(
    row.buy_market_type,
    row.funding_next_rate_buy_pct,
    row.funding_rate_buy_pct
  );
  const predictedSellRate = sideNextCycleFundingRate(
    row.sell_market_type,
    row.funding_next_rate_sell_pct,
    row.funding_rate_sell_pct
  );
  return (
    <div className="funding-cell">
      <div className="funding-row">
        <span className="funding-label">{"\u5f53\u524d"}</span>
        <Typography.Text className="funding-value">
          {fundingPair(currentBuyRate, currentSellRate)}
        </Typography.Text>
      </div>
      <div className="funding-row">
        <span className="funding-label">{"\u9884\u6d4b"}</span>
        <Typography.Text className="funding-value">
          {fundingPair(predictedBuyRate, predictedSellRate)}
        </Typography.Text>
      </div>
      <div className="funding-row">
        <span className="funding-label">{"\u6bcf\u5c0f\u65f6\u51c0"}</span>
        <Typography.Text className="funding-value" type={normalizedType}>
          {periodFundingPct(normalizedFunding.hourly, "h")}
        </Typography.Text>
      </div>
      <div className="funding-row">
        <span className="funding-label">24h净</span>
        <Typography.Text className="funding-value" type={normalizedType}>
          {periodFundingPct(normalizedFunding.daily, "24h")}
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
      title: "",
      fixed: "left",
      width: 44,
      render: (_, row) => (
        <Tooltip title="跳到价差查询，按买入腿/卖出腿查看价差曲线">
          <Button
            type="text"
            size="small"
            icon={<LineChartOutlined />}
            aria-label={`价差查询 ${row.symbol}`}
            onClick={() => openPairSpread(row)}
          />
        </Tooltip>
      )
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
      title: "买入交易所",
      width: 104,
      render: (_, row) => leg(
        row.buy_exchange,
        row.buy_market_type,
        row.buy_raw_symbol,
        row.symbol,
        row.buy_dex,
        row.buy_price_multiplier,
        row.buy_contract_size_multiplier
      )
    },
    {
      title: "卖出交易所",
      width: 104,
      render: (_, row) => leg(
        row.sell_exchange,
        row.sell_market_type,
        row.sell_raw_symbol,
        row.symbol,
        row.sell_dex,
        row.sell_price_multiplier,
        row.sell_contract_size_multiplier
      )
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
      scroll={{ x: 1556 }}
      size="small"
      tableLayout="fixed"
    />
  );
}
