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
  MISSING_FUNDING: "cyan"
};

export function RiskTags({ labels }: RiskTagsProps) {
  if (labels.length === 0) {
    return (
      <Tag icon={<CheckCircleOutlined />} color="green">
        CLEAN
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
