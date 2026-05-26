import { CheckCircleOutlined, ExclamationCircleOutlined } from "@ant-design/icons";
import { Space, Tag, Tooltip } from "antd";

import { riskLabelDescription, riskLabelName } from "../constants/riskLabels";

interface RiskTagsProps {
  labels: string[];
}

const severityColors: Record<string, string> = {
  LOW_VOLUME: "gold",
  STALE_DATA: "orange",
  HUGE_SPREAD_VERIFY: "red",
  WIDE_SPREAD: "volcano",
  SAME_TICKER_RISK: "purple",
  FUNDING_AGAINST: "magenta",
  MARK_INDEX_DEVIATION: "red",
  MISSING_FUNDING: "cyan",
  THIN_ORDER_BOOK: "orange",
  EDGE_AFTER_SLIPPAGE_TOO_SMALL: "gold",
  TRANSIENT_SIGNAL: "volcano"
};

export function RiskTags({ labels }: RiskTagsProps) {
  if (labels.length === 0) {
    return (
      <Tag icon={<CheckCircleOutlined />} color="green">
        无风险
      </Tag>
    );
  }
  return (
    <Space size={[4, 4]} wrap>
      {labels.map((label) => (
        <Tooltip key={label} title={riskLabelDescription.get(label)}>
          <Tag icon={<ExclamationCircleOutlined />} color={severityColors[label] ?? "default"}>
            {riskLabelName.has(label) ? `${riskLabelName.get(label)} (${label})` : label}
          </Tag>
        </Tooltip>
      ))}
    </Space>
  );
}
