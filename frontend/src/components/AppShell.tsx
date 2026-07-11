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
  StockOutlined,
  SettingOutlined
} from "@ant-design/icons";
import { Layout, Menu, Space, Spin, Typography } from "antd";
import {
  lazy,
  Suspense,
  useMemo,
  useState,
  type ComponentType,
  type LazyExoticComponent
} from "react";

type PageKey =
  | "dashboard"
  | "funding"
  | "funding-research"
  | "pair-monitor"
  | "premium-index"
  | "tradfi-perp"
  | "gate-twap"
  | "index-components"
  | "announcements"
  | "settings"
  | "history";

type LazyPage = LazyExoticComponent<ComponentType>;

const lazyPages: Record<PageKey, LazyPage> = {
  dashboard: lazy(() =>
    import("../pages/DashboardPage").then((module) => ({ default: module.DashboardPage }))
  ),
  funding: lazy(() =>
    import("../pages/FundingArbitragePage").then((module) => ({
      default: module.FundingArbitragePage
    }))
  ),
  "funding-research": lazy(() =>
    import("../pages/FundingResearchPage").then((module) => ({
      default: module.FundingResearchPage
    }))
  ),
  "pair-monitor": lazy(() =>
    import("../pages/PairMonitorPage").then((module) => ({
      default: module.PairMonitorPage
    }))
  ),
  "premium-index": lazy(() =>
    import("../pages/PremiumIndexPage").then((module) => ({
      default: module.PremiumIndexPage
    }))
  ),
  "tradfi-perp": lazy(() =>
    import("../pages/TradfiPerpMonitorPage").then((module) => ({
      default: module.TradfiPerpMonitorPage
    }))
  ),
  "gate-twap": lazy(() =>
    import("../pages/GateTwapPage").then((module) => ({ default: module.GateTwapPage }))
  ),
  "index-components": lazy(() =>
    import("../pages/IndexComponentChangesPage").then((module) => ({
      default: module.IndexComponentChangesPage
    }))
  ),
  announcements: lazy(() =>
    import("../pages/AnnouncementsPage").then((module) => ({
      default: module.AnnouncementsPage
    }))
  ),
  settings: lazy(() =>
    import("../pages/SettingsPage").then((module) => ({ default: module.SettingsPage }))
  ),
  history: lazy(() =>
    import("../pages/AlertHistoryPage").then((module) => ({ default: module.AlertHistoryPage }))
  )
};

export function AppShell() {
  const [page, setPage] = useState<PageKey>("dashboard");
  const CurrentPage = useMemo(() => lazyPages[page], [page]);

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
              key: "pair-monitor",
              icon: <StockOutlined />,
              label: "价差查询"
            },
            {
              key: "premium-index",
              icon: <LineChartOutlined />,
              label: "溢价指数"
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
          <Suspense
            fallback={
              <div className="page-loading">
                <Spin />
              </div>
            }
          >
            <CurrentPage />
          </Suspense>
        </Layout.Content>
      </Layout>
    </Layout>
  );
}
