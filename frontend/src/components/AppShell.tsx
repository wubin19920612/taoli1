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
  RadarChartOutlined,
  StockOutlined,
  SettingOutlined,
  ThunderboltOutlined
} from "@ant-design/icons";
import { Layout, Menu, Space, Spin, Typography } from "antd";
import {
  lazy,
  Suspense,
  useEffect,
  useMemo,
  useState,
  type ComponentType,
  type LazyExoticComponent
} from "react";

type PageKey =
  | "dashboard"
  | "funding"
  | "funding-research"
  | "opportunity-radar"
  | "pair-monitor"
  | "symbol-spread"
  | "premium-index"
  | "minute-signals"
  | "negative-basis"
  | "new-listing"
  | "second-sampling"
  | "fat-finger"
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
  "opportunity-radar": lazy(() =>
    import("../pages/OpportunityRadarPage").then((module) => ({
      default: module.OpportunityRadarPage
    }))
  ),
  "pair-monitor": lazy(() =>
    import("../pages/PairMonitorPage").then((module) => ({
      default: module.PairMonitorPage
    }))
  ),
  "symbol-spread": lazy(() =>
    import("../pages/SymbolSpreadPage").then((module) => ({
      default: module.SymbolSpreadPage
    }))
  ),
  "premium-index": lazy(() =>
    import("../pages/PremiumIndexPage").then((module) => ({
      default: module.PremiumIndexPage
    }))
  ),
  "minute-signals": lazy(() =>
    import("../pages/MinuteSignalPage").then((module) => ({
      default: module.MinuteSignalPage
    }))
  ),
  "negative-basis": lazy(() =>
    import("../pages/NegativeBasisMonitorPage").then((module) => ({
      default: module.NegativeBasisMonitorPage
    }))
  ),
  "new-listing": lazy(() =>
    import("../pages/NewListingMonitorPage").then((module) => ({
      default: module.NewListingMonitorPage
    }))
  ),
  "second-sampling": lazy(() =>
    import("../pages/SecondLevelSamplingPage").then((module) => ({
      default: module.SecondLevelSamplingPage
    }))
  ),
  "fat-finger": lazy(() =>
    import("../pages/FatFingerBacktestPage").then((module) => ({
      default: module.FatFingerBacktestPage
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

const pageKeys = Object.keys(lazyPages) as PageKey[];

function isPageKey(value: string | null): value is PageKey {
  return value !== null && pageKeys.includes(value as PageKey);
}

function pageFromUrl(): PageKey | null {
  if (typeof window === "undefined") {
    return null;
  }
  const requested = new URLSearchParams(window.location.search).get("page");
  return isPageKey(requested) ? requested : null;
}

function pushPageToUrl(page: PageKey): void {
  if (typeof window === "undefined") {
    return;
  }
  const url = new URL(window.location.href);
  url.searchParams.set("page", page);
  window.history.pushState({}, "", `${url.pathname}${url.search}${url.hash}`);
}

export function AppShell() {
  const [page, setPage] = useState<PageKey>(() => pageFromUrl() ?? "dashboard");
  const CurrentPage = useMemo(() => lazyPages[page], [page]);

  useEffect(() => {
    const syncPageFromUrl = () => {
      const requested = pageFromUrl();
      if (requested) {
        setPage(requested);
      }
    };
    window.addEventListener("popstate", syncPageFromUrl);
    window.addEventListener("taoli1:navigate", syncPageFromUrl);
    return () => {
      window.removeEventListener("popstate", syncPageFromUrl);
      window.removeEventListener("taoli1:navigate", syncPageFromUrl);
    };
  }, []);

  return (
    <Layout className="app-shell">
      <Layout.Sider breakpoint="xl" collapsedWidth={0} width={216} className="app-sider">
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
          onClick={(item) => {
            const nextPage = item.key as PageKey;
            setPage(nextPage);
            pushPageToUrl(nextPage);
          }}
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
              key: "opportunity-radar",
              icon: <RadarChartOutlined />,
              label: "机会雷达"
            },
            {
              key: "pair-monitor",
              icon: <StockOutlined />,
              label: "价差查询"
            },
            {
              key: "symbol-spread",
              icon: <LineChartOutlined />,
              label: "跨所价差"
            },
            {
              key: "premium-index",
              icon: <LineChartOutlined />,
              label: "溢价指数"
            },
            {
              key: "minute-signals",
              icon: <ThunderboltOutlined />,
              label: "1 分钟价差信号"
            },
            {
              key: "negative-basis",
              icon: <RadarChartOutlined />,
              label: "负基差埋伏"
            },
            {
              key: "new-listing",
              icon: <ThunderboltOutlined />,
              label: "新币极速"
            },
            {
              key: "second-sampling",
              icon: <ThunderboltOutlined />,
              label: "1s 采样"
            },
            {
              key: "fat-finger",
              icon: <ExperimentOutlined />,
              label: "乌龙回测"
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
