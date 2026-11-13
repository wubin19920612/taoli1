import { ExperimentOutlined, ReloadOutlined } from "@ant-design/icons";
import { Button, Space, Table, Tag, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import dayjs from "dayjs";
import utc from "dayjs/plugin/utc";
import { useEffect, useMemo, useState } from "react";

import { createTestAlertEvent, listAlertEvents } from "../api/client";
import type { AlertEvent, BigSpreadMuteRoute } from "../api/types";
import { BigSpreadMuteDialog } from "../components/BigSpreadMuteDialog";

dayjs.extend(utc);

const messageCellStyle = {
  lineHeight: 1.5,
  whiteSpace: "pre-wrap" as const,
  wordBreak: "break-word" as const
};

const statusLabels: Record<string, string> = {
  sent: "已发送",
  failed: "失败",
  muted: "已静默",
  unknown: "结果未知"
};

function formatUtcPlus8(value: string): string {
  return dayjs.utc(value).utcOffset(8).format("MM-DD HH:mm:ss");
}

const columns: ColumnsType<AlertEvent> = [
  { title: "时间(UTC+8)", dataIndex: "created_at", width: 132, render: formatUtcPlus8 },
  { title: "标的", dataIndex: "symbol", width: 120 },
  {
    title: "来源",
    dataIndex: "rule_id",
    width: 120,
    render: (value: string) => value === "big_spread"
      ? <Tag color="orange">大价差提醒</Tag>
      : "普通告警"
  },
  {
    title: "状态",
    dataIndex: "status",
    width: 96,
    render: (value: string) => (
      <Tag color={value === "sent" ? "green" : value === "failed" ? "red" : undefined}>
        {Object.prototype.hasOwnProperty.call(statusLabels, value) ? statusLabels[value] : value}
      </Tag>
    )
  },
  {
    title: "消息",
    dataIndex: "message",
    render: (value: string) => <div style={messageCellStyle}>{value}</div>
  }
];

function routeFor(event: AlertEvent): BigSpreadMuteRoute | undefined {
  // Parse only the fixed route line; raw symbols, DEX and multipliers stay in the message.
  const match = event.message.match(/^买 ([a-zA-Z0-9_-]+) [^\n]+ → 卖 ([a-zA-Z0-9_-]+) /m);
  return match ? { symbol: event.symbol, buy_exchange: match[1], sell_exchange: match[2] } : undefined;
}

export function AlertHistoryPage({ active = true }: { active?: boolean }) {
  const [muteEvent, setMuteEvent] = useState<AlertEvent | null>(null);
  const muteRoute = useMemo(() => muteEvent ? routeFor(muteEvent) : undefined, [muteEvent]);
  const [events, setEvents] = useState<AlertEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [testing, setTesting] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      setEvents(await listAlertEvents(200));
    } catch (exc) {
      message.error(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (active) void load();
  }, [active]);

  const createTestEvent = async () => {
    setTesting(true);
    try {
      const event = await createTestAlertEvent();
      setEvents((current) => [event, ...current.filter((item) => item.id !== event.id)]);
      message.success("测试告警已创建");
    } catch (exc) {
      message.error(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setTesting(false);
    }
  };

  return (
    <section className="panel alert-history-panel">
      <div className="toolbar">
        <Space>
          <Typography.Title level={4}>告警历史</Typography.Title>
        </Space>
        <Space>
          <Button icon={<ExperimentOutlined />} onClick={() => void createTestEvent()} loading={testing}>
            测试告警
          </Button>
          <Button icon={<ReloadOutlined />} onClick={() => void load()} loading={loading}>
            刷新
          </Button>
        </Space>
      </div>
      <Table
        columns={[...columns, {
          title: "操作", width: 140,
          render: (_, event) => event.rule_id === "big_spread"
            ? <Button disabled={!routeFor(event)} title={routeFor(event) ? undefined : "历史记录缺少路线，请在参数与告警中手动新增"}
                onClick={() => setMuteEvent(event)}>屏蔽此路线</Button>
            : null
        }]}
        dataSource={events}
        rowKey="id"
        loading={loading}
        size="middle"
        tableLayout="fixed"
        scroll={{ x: 760 }}
      />
      <BigSpreadMuteDialog open={Boolean(muteEvent)} route={muteRoute}
        routeDescription={muteEvent?.message.split("\n").find((line) => line.startsWith("买 "))}
        onCancel={() => setMuteEvent(null)} onSaved={() => setMuteEvent(null)} />
    </section>
  );
}
