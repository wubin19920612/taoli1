import {
  AlertOutlined,
  ArrowDownOutlined,
  ArrowUpOutlined,
  BgColorsOutlined,
  BellOutlined,
  ClockCircleOutlined,
  DashboardOutlined,
  EyeInvisibleOutlined,
  ExperimentOutlined,
  FundProjectionScreenOutlined,
  HolderOutlined,
  LineChartOutlined,
  NotificationOutlined,
  NodeIndexOutlined,
  OrderedListOutlined,
  RadarChartOutlined,
  SearchOutlined,
  StockOutlined,
  SettingOutlined,
  ThunderboltOutlined,
  UndoOutlined
} from "@ant-design/icons";
import { Button, ConfigProvider, Layout, Menu, Modal, Segmented, Space, Spin, Tooltip, Typography } from "antd";
import type { ThemeConfig } from "antd";
import {
  lazy,
  Suspense,
  useEffect,
  useMemo,
  useState,
  type ComponentType,
  type LazyExoticComponent,
  type ReactNode
} from "react";

type PageKey =
  | "dashboard"
  | "funding"
  | "funding-research"
  | "opportunity-radar"
  | "instrument"
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
  | "oil-news"
  | "settings"
  | "history";

type LazyPage = LazyExoticComponent<ComponentType>;
type NavigationItem = {
  key: PageKey;
  icon: ReactNode;
  label: string;
};

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
  instrument: lazy(() =>
    import("../pages/InstrumentLookupPage").then((module) => ({
      default: module.InstrumentLookupPage
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
  "oil-news": lazy(() =>
    import("../pages/OilNewsPage").then((module) => ({
      default: module.OilNewsPage
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
const appearanceModeStorageKey = "taoli1:appearance-mode";
const navigationOrderStorageKey = "taoli1:navigation-order.v1";

const defaultNavigationItems: NavigationItem[] = [
  { key: "dashboard", icon: <DashboardOutlined />, label: "实时机会" },
  { key: "funding", icon: <FundProjectionScreenOutlined />, label: "资金费率套利" },
  { key: "funding-research", icon: <ExperimentOutlined />, label: "资金研究" },
  { key: "opportunity-radar", icon: <RadarChartOutlined />, label: "机会雷达" },
  { key: "instrument", icon: <SearchOutlined />, label: "标的查询" },
  { key: "pair-monitor", icon: <StockOutlined />, label: "价差查询" },
  { key: "symbol-spread", icon: <LineChartOutlined />, label: "跨所价差" },
  { key: "premium-index", icon: <LineChartOutlined />, label: "溢价指数" },
  { key: "minute-signals", icon: <ThunderboltOutlined />, label: "1 分钟价差信号" },
  { key: "negative-basis", icon: <RadarChartOutlined />, label: "负基差埋伏" },
  { key: "new-listing", icon: <ThunderboltOutlined />, label: "新币极速" },
  { key: "second-sampling", icon: <ThunderboltOutlined />, label: "1s 采样" },
  { key: "fat-finger", icon: <ExperimentOutlined />, label: "乌龙回测" },
  { key: "tradfi-perp", icon: <LineChartOutlined />, label: "TradFi 价差" },
  { key: "gate-twap", icon: <ClockCircleOutlined />, label: "Gate 定时减仓" },
  { key: "index-components", icon: <NodeIndexOutlined />, label: "指数成分变更" },
  { key: "announcements", icon: <NotificationOutlined />, label: "交易所公告" },
  { key: "oil-news", icon: <NotificationOutlined />, label: "原油新闻" },
  { key: "settings", icon: <SettingOutlined />, label: "参数与告警" },
  { key: "history", icon: <BellOutlined />, label: "告警历史" }
];
const defaultNavigationOrder = defaultNavigationItems.map((item) => item.key);
const navigationItemsByKey = new Map(defaultNavigationItems.map((item) => [item.key, item]));

type AppearanceMode = "quiet" | "standard";

const quietTheme: ThemeConfig = {
  token: {
    colorPrimary: "#586b66",
    colorInfo: "#66737a",
    colorSuccess: "#617168",
    colorWarning: "#81745f",
    colorError: "#806969",
    colorLink: "#536a65",
    colorTextBase: "#303836",
    colorBgBase: "#f3f5f4",
    colorBgLayout: "#ecefed",
    colorBgContainer: "#f7f8f7",
    colorBorder: "#d5dcda"
  },
  components: {
    Button: {
      primaryShadow: "none"
    },
    Menu: {
      darkItemBg: "#3d4543",
      darkItemColor: "#cbd1cf",
      darkItemHoverBg: "#4a5350",
      darkItemSelectedBg: "#59635f",
      darkItemSelectedColor: "#f4f6f5"
    },
    Segmented: {
      itemColor: "#66706d",
      itemSelectedBg: "#f8f9f8",
      itemSelectedColor: "#303836",
      trackBg: "#e4e8e6"
    }
  }
};

function initialAppearanceMode(): AppearanceMode {
  if (typeof window === "undefined") {
    return "standard";
  }
  try {
    return window.localStorage.getItem(appearanceModeStorageKey) === "quiet"
      ? "quiet"
      : "standard";
  } catch {
    return "standard";
  }
}

function normalizeNavigationOrder(value: unknown): PageKey[] {
  const requested = Array.isArray(value) ? value : [];
  const known = requested.filter(
    (key, index): key is PageKey => typeof key === "string"
      && isPageKey(key)
      && requested.indexOf(key) === index
  );
  return [...known, ...defaultNavigationOrder.filter((key) => !known.includes(key))];
}

function initialNavigationOrder(): PageKey[] {
  if (typeof window === "undefined") {
    return defaultNavigationOrder;
  }
  try {
    return normalizeNavigationOrder(JSON.parse(window.localStorage.getItem(navigationOrderStorageKey) ?? "[]"));
  } catch {
    return defaultNavigationOrder;
  }
}

function moveNavigationItem(order: PageKey[], key: PageKey, targetIndex: number): PageKey[] {
  const currentIndex = order.indexOf(key);
  if (currentIndex < 0) return order;
  const nextIndex = Math.max(0, Math.min(order.length - 1, targetIndex));
  if (currentIndex === nextIndex) return order;
  const next = [...order];
  next.splice(currentIndex, 1);
  next.splice(nextIndex, 0, key);
  return next;
}

function placeNavigationItem(order: PageKey[], key: PageKey, target: PageKey, after: boolean): PageKey[] {
  if (key === target) return order;
  const next = order.filter((item) => item !== key);
  const targetIndex = next.indexOf(target);
  if (targetIndex < 0) return order;
  next.splice(targetIndex + Number(after), 0, key);
  return next;
}

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
  const [appearanceMode, setAppearanceMode] = useState<AppearanceMode>(initialAppearanceMode);
  const [navigationOrder, setNavigationOrder] = useState<PageKey[]>(initialNavigationOrder);
  const [draftNavigationOrder, setDraftNavigationOrder] = useState<PageKey[]>(initialNavigationOrder);
  const [navigationOrderOpen, setNavigationOrderOpen] = useState(false);
  const [draggingPage, setDraggingPage] = useState<PageKey | null>(null);
  const CurrentPage = useMemo(() => lazyPages[page], [page]);
  const navigationItems = useMemo(
    () => navigationOrder.map((key) => navigationItemsByKey.get(key)).filter((item): item is NavigationItem => Boolean(item)),
    [navigationOrder]
  );
  const quietMode = appearanceMode === "quiet";

  useEffect(() => {
    try {
      window.localStorage.setItem(appearanceModeStorageKey, appearanceMode);
    } catch {
      // The selected mode still applies for this session when storage is unavailable.
    }
  }, [appearanceMode]);

  useEffect(() => {
    try {
      window.localStorage.setItem(navigationOrderStorageKey, JSON.stringify(navigationOrder));
    } catch {
      // The selected order still applies for this session when storage is unavailable.
    }
  }, [navigationOrder]);

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
    <ConfigProvider theme={quietMode ? quietTheme : undefined}>
      <Layout className={`app-shell app-shell-${appearanceMode}`}>
        <Layout.Sider breakpoint="xl" collapsedWidth={0} width={216} className="app-sider">
          <div className="brand">
            <Space>
              <AlertOutlined />
              <Typography.Text strong>{quietMode ? "数据台" : "套利雷达"}</Typography.Text>
            </Space>
            <Tooltip title="调整菜单顺序">
              <Button
                className="navigation-order-trigger"
                type="text"
                aria-label="调整菜单顺序"
                icon={<OrderedListOutlined />}
                onClick={() => {
                  setDraftNavigationOrder(navigationOrder);
                  setNavigationOrderOpen(true);
                }}
              />
            </Tooltip>
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
            items={navigationItems}
          />
        </Layout.Sider>
        <Layout>
          <Layout.Header className="app-header">
            <Typography.Title level={3}>{quietMode ? "数据工作台" : "CEX 套利雷达"}</Typography.Title>
            <Tooltip title="切换显示配色">
              <Segmented
                className="appearance-switch"
                size="small"
                aria-label="显示配色"
                value={appearanceMode}
                options={[
                  { label: "低调", value: "quiet", icon: <EyeInvisibleOutlined /> },
                  { label: "原配色", value: "standard", icon: <BgColorsOutlined /> }
                ]}
                onChange={(value) => setAppearanceMode(value as AppearanceMode)}
              />
            </Tooltip>
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
        <Modal
          title="调整菜单顺序"
          open={navigationOrderOpen}
          width={520}
          onCancel={() => setNavigationOrderOpen(false)}
          footer={(
            <div className="navigation-order-footer">
              <Button
                aria-label="恢复默认菜单顺序"
                icon={<UndoOutlined />}
                onClick={() => setDraftNavigationOrder(defaultNavigationOrder)}
              >
                恢复默认
              </Button>
              <Space>
                <Button aria-label="取消调整菜单顺序" onClick={() => setNavigationOrderOpen(false)}>取消</Button>
                <Button
                  type="primary"
                  aria-label="保存菜单顺序"
                  onClick={() => {
                    setNavigationOrder(draftNavigationOrder);
                    setNavigationOrderOpen(false);
                  }}
                >
                  保存
                </Button>
              </Space>
            </div>
          )}
        >
          <ol className="navigation-order-list">
            {draftNavigationOrder.map((key, index) => {
              const item = navigationItemsByKey.get(key);
              if (!item) return null;
              return (
                <li
                  key={key}
                  className={draggingPage === key ? "navigation-order-item navigation-order-item-dragging" : "navigation-order-item"}
                  draggable
                  onDragStart={(event) => {
                    setDraggingPage(key);
                    event.dataTransfer.effectAllowed = "move";
                    event.dataTransfer.setData("text/plain", key);
                  }}
                  onDragOver={(event) => {
                    event.preventDefault();
                    event.dataTransfer.dropEffect = "move";
                  }}
                  onDrop={(event) => {
                    event.preventDefault();
                    const draggedKey = draggingPage ?? event.dataTransfer.getData("text/plain");
                    if (!isPageKey(draggedKey)) return;
                    const bounds = event.currentTarget.getBoundingClientRect();
                    setDraftNavigationOrder((current) => placeNavigationItem(
                      current,
                      draggedKey,
                      key,
                      event.clientY > bounds.top + bounds.height / 2
                    ));
                    setDraggingPage(null);
                  }}
                  onDragEnd={() => setDraggingPage(null)}
                >
                  <span className="navigation-order-handle" aria-hidden="true"><HolderOutlined /></span>
                  <span className="navigation-order-icon" aria-hidden="true">{item.icon}</span>
                  <span className="navigation-order-label">{item.label}</span>
                  <Space className="navigation-order-actions" size={2}>
                    <Tooltip title="上移">
                      <Button
                        type="text"
                        size="small"
                        aria-label={`上移 ${item.label}`}
                        icon={<ArrowUpOutlined />}
                        disabled={index === 0}
                        onClick={() => setDraftNavigationOrder((current) => moveNavigationItem(current, key, index - 1))}
                      />
                    </Tooltip>
                    <Tooltip title="下移">
                      <Button
                        type="text"
                        size="small"
                        aria-label={`下移 ${item.label}`}
                        icon={<ArrowDownOutlined />}
                        disabled={index === draftNavigationOrder.length - 1}
                        onClick={() => setDraftNavigationOrder((current) => moveNavigationItem(current, key, index + 1))}
                      />
                    </Tooltip>
                  </Space>
                </li>
              );
            })}
          </ol>
        </Modal>
      </Layout>
    </ConfigProvider>
  );
}
