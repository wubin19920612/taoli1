import { ExperimentOutlined, ReloadOutlined } from "@ant-design/icons";
import { Button, Space, Table, Tag, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import dayjs from "dayjs";
import utc from "dayjs/plugin/utc";
import { useEffect, useState } from "react";

import { createTestAlertEvent, listAlertEvents } from "../api/client";
import type { AlertEvent } from "../api/types";

dayjs.extend(utc);

const messageCellStyle = {
  lineHeight: 1.5,
  whiteSpace: "pre-wrap" as const,
  wordBreak: "break-word" as const
};

function formatUtcPlus8(value: string): string {
  return dayjs.utc(value).utcOffset(8).format("MM-DD HH:mm:ss");
}

const columns: ColumnsType<AlertEvent> = [
  { title: "时间(UTC+8)", dataIndex: "created_at", width: 132, render: formatUtcPlus8 },
  { title: "标的", dataIndex: "symbol", width: 120 },
  { title: "状态", dataIndex: "status", width: 96, render: (value: string) => <Tag color={value === "sent" ? "green" : "red"}>{value}</Tag> },
  {
    title: "消息",
    dataIndex: "message",
    render: (value: string) => <div style={messageCellStyle}>{value}</div>
  }
];

export function AlertHistoryPage() {
  const [events, setEvents] = useState<AlertEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [testing, setTesting] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      setEvents(await listAlertEvents(200));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

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
    <div className="page">
      <div className="toolbar">
        <Space>
          <Typography.Title level={4}>告警历史</Typography.Title>
        </Space>
        <Space>
          <Button icon={<ExperimentOutlined />} onClick={() => void createTestEvent()} loading={testing}>
            测试告警
          </Button>
          <Button icon={<ReloadOutlined />} onClick={() => void load()} loading={loading} />
        </Space>
      </div>
      <Table columns={columns} dataSource={events} rowKey="id" loading={loading} size="middle" tableLayout="fixed" />
    </div>
  );
}
