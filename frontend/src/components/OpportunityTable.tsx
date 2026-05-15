import { ArrowDownOutlined, ArrowUpOutlined } from "@ant-design/icons";
import { Space, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import dayjs from "dayjs";

import type { Opportunity } from "../api/types";
import { RiskTags } from "./RiskTags";

interface OpportunityTableProps {
  opportunities: Opportunity[];
  loading: boolean;
}

function pct(value: number | null | undefined): string {
  return typeof value === "number" ? `${value.toFixed(3)}%` : "-";
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

function leg(exchange: string, marketType: string, side: "buy" | "sell") {
  const icon = side === "buy" ? <ArrowDownOutlined /> : <ArrowUpOutlined />;
  const color = side === "buy" ? "green" : "red";
  return (
    <Space size={6}>
      <Tag color={color} icon={icon}>
        {side.toUpperCase()}
      </Tag>
      <Typography.Text>{`${exchange} ${marketType}`}</Typography.Text>
    </Space>
  );
}

const columns: ColumnsType<Opportunity> = [
  {
    title: "标的",
    dataIndex: "symbol",
    fixed: "left",
    width: 150,
    render: (_, row) => (
      <Space direction="vertical" size={0}>
        <Typography.Text strong>{row.symbol}</Typography.Text>
        <Tag>{row.type}</Tag>
      </Space>
    )
  },
  {
    title: "买入腿",
    width: 180,
    render: (_, row) => leg(row.buy_exchange, row.buy_market_type, "buy")
  },
  {
    title: "卖出腿",
    width: 180,
    render: (_, row) => leg(row.sell_exchange, row.sell_market_type, "sell")
  },
  {
    title: "开仓价差",
    dataIndex: "open_spread_pct",
    sorter: (a, b) => a.open_spread_pct - b.open_spread_pct,
    defaultSortOrder: "descend",
    render: (value: number) => <Typography.Text strong>{pct(value)}</Typography.Text>
  },
  {
    title: "净估算",
    dataIndex: "fee_adjusted_open_pct",
    sorter: (a, b) => a.fee_adjusted_open_pct - b.fee_adjusted_open_pct,
    render: (value: number) => (
      <Typography.Text type={value >= 0 ? "success" : "danger"}>{pct(value)}</Typography.Text>
    )
  },
  {
    title: "平仓价差",
    dataIndex: "close_spread_pct",
    render: (value: number) => pct(value)
  },
  {
    title: "资金费率",
    width: 170,
    render: (_, row) => (
      <Space direction="vertical" size={0}>
        <span>{`${pct(row.funding_rate_buy_pct)} / ${pct(row.funding_rate_sell_pct)}`}</span>
        <Typography.Text type={row.net_funding_pct && row.net_funding_pct < 0 ? "danger" : "secondary"}>
          {`net ${pct(row.net_funding_pct)}`}
        </Typography.Text>
      </Space>
    )
  },
  {
    title: "24h成交额",
    width: 160,
    render: (_, row) => `${money(row.buy_volume_24h_usdt)} / ${money(row.sell_volume_24h_usdt)}`
  },
  {
    title: "风险",
    dataIndex: "risk_labels",
    width: 250,
    render: (labels: string[]) => <RiskTags labels={labels} />
  },
  {
    title: "更新时间",
    dataIndex: "last_seen_at",
    width: 150,
    render: (value: string) => dayjs(value).format("HH:mm:ss")
  }
];

export function OpportunityTable({ opportunities, loading }: OpportunityTableProps) {
  return (
    <Table
      className="opportunity-table"
      columns={columns}
      dataSource={opportunities}
      loading={loading}
      rowKey="id"
      pagination={{ pageSize: 50, showSizeChanger: true }}
      scroll={{ x: 1320 }}
      size="middle"
    />
  );
}
