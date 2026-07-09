import { FilterOutlined, ReloadOutlined } from "@ant-design/icons";
import { Button, Input, InputNumber, Segmented, Select, Space, Switch, Tooltip } from "antd";

import type { OpportunityFilters, OpportunityType } from "../api/types";
import { riskLabelOptions } from "../constants/riskLabels";

interface TopFiltersProps {
  filters: OpportunityFilters;
  loading: boolean;
  autoRefresh: boolean;
  refreshIntervalMs: number;
  onChange: (filters: OpportunityFilters) => void;
  onRefresh: () => void;
  onAutoRefreshChange: (enabled: boolean) => void;
  onRefreshIntervalChange: (intervalMs: number) => void;
}

const exchanges = ["binance", "okx", "bybit", "gate", "bitget", "htx", "aster", "hyperliquid"];
const refreshIntervalOptions = [
  { label: "15s", value: 15000 },
  { label: "30s", value: 30000 },
  { label: "60s", value: 60000 }
];

export function TopFilters({
  filters,
  loading,
  autoRefresh,
  refreshIntervalMs,
  onChange,
  onRefresh,
  onAutoRefreshChange,
  onRefreshIntervalChange
}: TopFiltersProps) {
  const patch = (next: Partial<OpportunityFilters>) => onChange({ ...filters, ...next });
  return (
    <div className="toolbar">
      <Space size={10} wrap className="toolbar-controls">
        <Segmented
          options={[
            { label: "全部", value: "" },
            { label: "SF", value: "SF" },
            { label: "FF", value: "FF" },
            { label: "SS", value: "SS" }
          ]}
          value={filters.type ?? ""}
          onChange={(value) => patch({ type: (value || undefined) as OpportunityType | undefined })}
        />
        <Select
          mode="multiple"
          allowClear
          className="type-exclude-select"
          placeholder="屏蔽类型"
          aria-label="屏蔽类型"
          maxTagCount="responsive"
          options={[
            { label: "SF", value: "SF" },
            { label: "FF", value: "FF" },
            { label: "SS", value: "SS" }
          ]}
          value={filters.exclude_types}
          onChange={(value) =>
            patch({
              exclude_types: value.length > 0 ? (value as OpportunityType[]) : undefined
            })
          }
        />
        <Input
          allowClear
          className="symbol-input"
          placeholder="标的"
          prefix={<FilterOutlined />}
          value={filters.symbol}
          onChange={(event) => patch({ symbol: event.target.value || undefined })}
        />
        <Select
          allowClear
          className="exchange-select"
          placeholder="交易所"
          options={exchanges.map((item) => ({ label: item, value: item }))}
          value={filters.exchange}
          onChange={(value) => patch({ exchange: value })}
        />
        <InputNumber
          className="spread-input"
          min={0}
          step={0.1}
          placeholder="开仓价差 %"
          suffix="%"
          value={filters.min_open_spread_pct}
          onChange={(value) => patch({ min_open_spread_pct: value ?? undefined })}
        />
        <InputNumber
          className="volume-input"
          min={0}
          step={100}
          placeholder="成交额 K"
          suffix="K"
          value={filters.min_volume_24h_k}
          onChange={(value) => patch({ min_volume_24h_k: value ?? undefined })}
        />
        <InputNumber
          className="limit-input"
          min={20}
          max={500}
          step={10}
          placeholder="Rows"
          value={filters.limit}
          onChange={(value) => patch({ limit: value ?? undefined })}
        />
        <Select
          mode="multiple"
          allowClear
          className="risk-select"
          placeholder="隐藏风险"
          maxTagCount="responsive"
          options={riskLabelOptions.map((item) => ({
            value: item.value,
            label: `${item.label} (${item.value})`
          }))}
          value={filters.hidden_risk_labels}
          onChange={(value) => patch({ hidden_risk_labels: value })}
          disabled={filters.include_risky ?? false}
        />
        <Space size={6}>
          <Switch
            checked={filters.include_risky ?? false}
            onChange={(checked) => patch({ include_risky: checked })}
          />
          <span>显示排查项</span>
        </Space>
      </Space>
      <div className="toolbar-actions">
        <Space size={8} wrap>
          <Space size={6}>
            <Switch checked={autoRefresh} onChange={onAutoRefreshChange} />
            <span>Auto</span>
          </Space>
          <Select
            className="refresh-interval-select"
            disabled={!autoRefresh}
            options={refreshIntervalOptions}
            value={refreshIntervalMs}
            onChange={onRefreshIntervalChange}
          />
        </Space>
        <Tooltip title="刷新">
          <Button icon={<ReloadOutlined />} loading={loading} onClick={onRefresh} />
        </Tooltip>
      </div>
    </div>
  );
}
