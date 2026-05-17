import { FilterOutlined, ReloadOutlined } from "@ant-design/icons";
import { Button, Input, InputNumber, Segmented, Select, Space, Switch, Tooltip } from "antd";

import type { OpportunityFilters, OpportunityType } from "../api/types";
import { riskLabelOptions } from "../constants/riskLabels";

interface TopFiltersProps {
  filters: OpportunityFilters;
  loading: boolean;
  onChange: (filters: OpportunityFilters) => void;
  onRefresh: () => void;
}

const exchanges = ["binance", "okx", "bybit", "gate", "bitget", "htx", "aster"];
export function TopFilters({ filters, loading, onChange, onRefresh }: TopFiltersProps) {
  const patch = (next: Partial<OpportunityFilters>) => onChange({ ...filters, ...next });
  return (
    <div className="toolbar">
      <Space size={10} wrap>
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
      <Tooltip title="刷新">
        <Button icon={<ReloadOutlined />} loading={loading} onClick={onRefresh} />
      </Tooltip>
    </div>
  );
}
