import { Tag, Tooltip } from "antd";

interface QuoteAgeTagProps {
  ageMs?: number | null;
  sourceAgeMs?: number | null;
  timeSource?: string | null;
}

export function QuoteAgeTag({ ageMs, sourceAgeMs, timeSource }: QuoteAgeTagProps) {
  if (typeof ageMs !== "number" || !Number.isFinite(ageMs)) {
    return null;
  }
  const color = ageMs < 5000 ? "green" : ageMs <= 30000 ? "yellow" : "red";
  const label = `${(ageMs / 1000).toFixed(1)}s`;
  const sourceAge = typeof sourceAgeMs === "number" && Number.isFinite(sourceAgeMs)
    ? `${sourceAgeMs}ms`
    : "未知";
  return (
    <Tooltip title={`来源年龄：${sourceAge}；时间来源：${timeSource || "未知"}`}>
      <Tag color={color} aria-label={`报价年龄 ${label}`} style={{ width: "fit-content" }}>{label}</Tag>
    </Tooltip>
  );
}
