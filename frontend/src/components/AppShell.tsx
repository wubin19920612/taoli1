import {
  AlertOutlined,
  BellOutlined,
  ClockCircleOutlined,
  DashboardOutlined,
  ExperimentOutlined,
  FundProjectionScreenOutlined,
  LineChartOutlined,
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
import { FundingResearchPage } from "../pages/FundingResearchPage";
import { GateTwapPage } from "../pages/GateTwapPage";
import { IndexComponentChangesPage } from "../pages/IndexComponentChangesPage";
import { SettingsPage } from "../pages/SettingsPage";
import { TradfiPerpMonitorPage } from "../pages/TradfiPerpMonitorPage";

type PageKey =
  | "dashboard"
  | "funding"
  | "funding-research"
  | "tradfi-perp"
  | "gate-twap"
  | "index-components"
  | "announcements"
  | "settings"
  | "history";

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
            {
              key: "funding",
              icon: <FundProjectionScreenOutlined />,
              label: "资金费率套利"
            },
            {
              key: "funding-research",
              icon: <ExperimentOutlined />,
              label: "资金研究"
            },
            {
              key: "tradfi-perp",
              icon: <LineChartOutlined />,
              label: "TradFi 价差"
            },
            {
              key: "gate-twap",
              icon: <ClockCircleOutlined />,
              label: "Gate 定时减仓"
            },
            {
              key: "index-components",
              icon: <NodeIndexOutlined />,
              label: "指数成分变更"
            },
            {
              key: "announcements",
              icon: <NotificationOutlined />,
              label: "上下币公告"
            },
            { key: "settings", icon: <SettingOutlined />, label: "参数与告警" },
            { key: "history", icon: <BellOutlined />, label: "告警历史" }
          ]}
        />
      </Layout.Sider>
      <Layout>
        <Layout.Header className="app-header">
          <Typography.Title level={3}>CEX 套利雷达</Typography.Title>
        </Layout.Header>
        <Layout.Content className="app-content">
          {page === "dashboard" ? <DashboardPage /> : null}
          {page === "funding" ? <FundingArbitragePage /> : null}
          {page === "funding-research" ? <FundingResearchPage /> : null}
          {page === "tradfi-perp" ? <TradfiPerpMonitorPage /> : null}
          {page === "gate-twap" ? <GateTwapPage /> : null}
          {page === "index-components" ? <IndexComponentChangesPage /> : null}
          {page === "announcements" ? <AnnouncementsPage /> : null}
          {page === "settings" ? <SettingsPage /> : null}
          {page === "history" ? <AlertHistoryPage /> : null}
        </Layout.Content>
      </Layout>
    </Layout>
  );
}
