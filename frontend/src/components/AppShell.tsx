import {
  AlertOutlined,
  BellOutlined,
  DashboardOutlined,
  FundProjectionScreenOutlined,
  NotificationOutlined,
  NodeIndexOutlined,
  SettingOutlined
} from "@ant-design/icons";
import { Layout, Menu, Space, Typography } from "antd";
import { useState } from "react";

import { AlertHistoryPage } from "../pages/AlertHistoryPage";
import { AnnouncementsPage } from "../pages/AnnouncementsPage";
import { DashboardPage } from "../pages/DashboardPage";
import { FundingArbitragePage } from "../pages/FundingArbitragePage";
import { IndexComponentChangesPage } from "../pages/IndexComponentChangesPage";
import { SettingsPage } from "../pages/SettingsPage";

type PageKey = "dashboard" | "funding" | "index-components" | "announcements" | "settings" | "history";

export function AppShell() {
  const [page, setPage] = useState<PageKey>("dashboard");
  return (
    <Layout className="app-shell">
      <Layout.Sider breakpoint="lg" collapsedWidth={0} width={216} className="app-sider">
        <div className="brand">
          <Space>
            <AlertOutlined />
            <Typography.Text strong>{"\u5957\u5229\u96f7\u8fbe"}</Typography.Text>
          </Space>
        </div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[page]}
          onClick={(item) => setPage(item.key as PageKey)}
          items={[
            { key: "dashboard", icon: <DashboardOutlined />, label: "\u5b9e\u65f6\u673a\u4f1a" },
            {
              key: "funding",
              icon: <FundProjectionScreenOutlined />,
              label: "\u8d44\u91d1\u8d39\u7387\u5957\u5229"
            },
            {
              key: "index-components",
              icon: <NodeIndexOutlined />,
              label: "指数成分变更"
            },
            {
              key: "announcements",
              icon: <NotificationOutlined />,
              label: "上/下币公告"
            },
            { key: "settings", icon: <SettingOutlined />, label: "\u53c2\u6570\u4e0e\u544a\u8b66" },
            { key: "history", icon: <BellOutlined />, label: "\u544a\u8b66\u5386\u53f2" }
          ]}
        />
      </Layout.Sider>
      <Layout>
        <Layout.Header className="app-header">
          <Typography.Title level={3}>CEX Arbitrage Radar</Typography.Title>
        </Layout.Header>
        <Layout.Content className="app-content">
          {page === "dashboard" ? <DashboardPage /> : null}
          {page === "funding" ? <FundingArbitragePage /> : null}
          {page === "index-components" ? <IndexComponentChangesPage /> : null}
          {page === "announcements" ? <AnnouncementsPage /> : null}
          {page === "settings" ? <SettingsPage /> : null}
          {page === "history" ? <AlertHistoryPage /> : null}
        </Layout.Content>
      </Layout>
    </Layout>
  );
}
