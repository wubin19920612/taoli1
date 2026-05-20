import { ReloadOutlined } from "@ant-design/icons";
import { Button, Space, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import dayjs from "dayjs";
import { useEffect, useState } from "react";

import { listAlertEvents } from "../api/client";
import type { AlertEvent } from "../api/types";

const messageCellStyle = {
  lineHeight: 1.5,
  whiteSpace: "pre-wrap" as const,
  wordBreak: "break-word" as const
};

const columns: ColumnsType<AlertEvent> = [
  { title: "时间", dataIndex: "created_at", width: 132, render: (value: string) => dayjs(value).format("MM-DD HH:mm:ss") },
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

  return (
    <div className="page">
      <div className="toolbar">
        <Space>
          <Typography.Title level={4}>告警历史</Typography.Title>
        </Space>
        <Button icon={<ReloadOutlined />} onClick={() => void load()} loading={loading} />
      </div>
      <Table columns={columns} dataSource={events} rowKey="id" loading={loading} size="middle" tableLayout="fixed" />
    </div>
  );
}
