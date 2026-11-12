import { Alert, Card, Descriptions, Space, Tag, Typography } from "antd";
import type { LiveExternalPositions, LiveRemotePositions, LiveSummary } from "../../api/liveExec";
import { programRiskView } from "./programRiskView";

export function LiveProgramRisk({ registry, snapshot, summary, now }: {
  registry: LiveExternalPositions | null; snapshot: LiveRemotePositions | null; summary: LiveSummary | null; now: number;
}) {
  const risk = programRiskView(registry?.income, snapshot, summary, now);
  return <Card title="程序停机依据" data-testid="live-program-risk">
    <Space direction="vertical" style={{ width: "100%" }}>
      <Typography.Text>默认比较本程序累计已实现损益、手续费、资金费和当前浮盈亏之和；达到负阈值时停机并仅平本程序仓位。</Typography.Text>
      <Descriptions size="small" column={{ xs: 1, md: 2, xl: 3 }}>
        <Descriptions.Item label="程序累计损益">{risk.total ?? "—"} USD</Descriptions.Item>
        <Descriptions.Item label="程序损益停机阈值">{risk.limit?.toFixed(2) ?? "—"} USD</Descriptions.Item>
        <Descriptions.Item label="程序今日损益（UTC）">{risk.daily ?? "—"} USD</Descriptions.Item>
        <Descriptions.Item label="程序累计资金费">{risk.funding ?? "—"} USD</Descriptions.Item>
        <Descriptions.Item label="程序当前浮盈亏">{risk.upnl ?? "—"} USD</Descriptions.Item>
      </Descriptions>
      {!risk.verified && <Alert type="warning" showIcon message="程序损益证明不可用或已过期，不能作为当前停机依据" />}
      {risk.reached !== null && <Tag color={risk.reached ? "red" : undefined}>
        {risk.reached ? "已达程序损益停机阈值" : "未达程序损益停机阈值"}</Tag>}
      <Typography.Text type="secondary">日亏停开与累计亏损门槛独立生效，可能更早触发；以上比较不代表当前允许开仓。今日与累计净利润卡片为已平仓统计，不含当前浮盈亏。</Typography.Text>
      <Typography.Text>外仓负债规则</Typography.Text>
      <Tag color="orange">已核实外仓负债：仅告警</Tag>
      <Typography.Text type="secondary">仅适用于执行服务核实的 CrossEx 外仓保证金借款；无法核实、混合归属或 Bitget 债务仍停开，收益证明门禁独立。</Typography.Text>
      <Typography.Text>外仓负债当前归属状态：上游未提供</Typography.Text>
    </Space>
  </Card>;
}
