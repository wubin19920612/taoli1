import {
  AlertOutlined,
  BellOutlined,
  DashboardOutlined,
  SettingOutlined
} from "@ant-design/icons";
import { Layout, Menu, Space, Typography } from "antd";
import { useState } from "react";

import { AlertHistoryPage } from "../pages/AlertHistoryPage";
import { DashboardPage } from "../pages/DashboardPage";
import { SettingsPage } from "../pages/SettingsPage";

type PageKey = "dashboard" | "settings" | "history";

export function AppShell() {
  const [page, setPage] = useState<PageKey>("dashboard");
  return (
    <Layout className="app-shell">
      <Layout.Sider breakpoint="lg" collapsedWidth={0} width={216} className="app-sider">
        <div className="brand">
          <Space>
            <AlertOutlined />
            <Typography.Text strong>套利雷达</Typography.Text>
          </Space>
        </div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[page]}
          onClick={(item) => setPage(item.key as PageKey)}
          items={[
            { key: "dashboard", icon: <DashboardOutlined />, label: "实时机会" },
            { key: "settings", icon: <SettingOutlined />, label: "参数与告警" },
            { key: "history", icon: <BellOutlined />, label: "告警历史" }
          ]}
        />
      </Layout.Sider>
      <Layout>
        <Layout.Header className="app-header">
          <Typography.Title level={3}>CEX Arbitrage Radar</Typography.Title>
        </Layout.Header>
        <Layout.Content className="app-content">
          {page === "dashboard" ? <DashboardPage /> : null}
          {page === "settings" ? <SettingsPage /> : null}
          {page === "history" ? <AlertHistoryPage /> : null}
        </Layout.Content>
      </Layout>
    </Layout>
  );
}
