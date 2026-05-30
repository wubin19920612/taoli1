import { DeleteOutlined, PlusOutlined, ReloadOutlined, SearchOutlined } from "@ant-design/icons";
import { Button, Input, Select, Space, Table, Tag, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import dayjs from "dayjs";
import utc from "dayjs/plugin/utc";
import { useEffect, useState } from "react";

import {
  createIndexComponentWatchItem,
  deleteIndexComponentWatchItem,
  listIndexComponentChanges,
  listIndexComponentSnapshots,
  listIndexComponentWatchlist,
  listMarkets
} from "../api/client";
import type {
  IndexComponent,
  IndexComponentChange,
  IndexComponentSnapshot,
  IndexComponentWatchItem,
  MarketSnapshot
} from "../api/types";

dayjs.extend(utc);

const EXCHANGE_OPTIONS = [
  { label: "全部交易所", value: "" },
  { label: "Binance", value: "binance" },
  { label: "OKX", value: "okx" },
  { label: "Bybit", value: "bybit" },
  { label: "Gate", value: "gate" },
  { label: "Bitget", value: "bitget" },
  { label: "HTX", value: "htx" },
  { label: "Aster", value: "aster" },
  { label: "Hyperliquid", value: "hyperliquid" }
];

function formatUtcPlus8(value: string): string {
  return dayjs.utc(value).utcOffset(8).format("MM-DD HH:mm:ss");
}

function sourceShortName(source: string): string {
  const value = source.trim();
  return value.replace(/[-_]+/g, " ");
}

function providerShortName(source: string): string {
  const value = source.trim();
  const providerNames: Record<string, string> = {
    "binance-fapi-constituents": "binance"
  };
  if (providerNames[value]) {
    return providerNames[value];
  }
  const [exchange] = value.split(/[-_]+/);
  return exchange || value;
}

function componentSourceName(source: string): string {
  return source
    .split(/[^a-zA-Z0-9]+/)
    .filter(Boolean)
    .map((part) => `${part.charAt(0).toUpperCase()}${part.slice(1)}`)
    .join("");
}

function componentIdentity(component: IndexComponent): string {
  return `${component.source}:${component.symbol}`;
}

function formatWeight(weight?: number | null): string {
  if (weight === undefined || weight === null) {
    return "未给比例";
  }
  return `${(weight * 100).toFixed(2)}%`;
}

function formatPct(value?: number | null, digits = 3): string {
  return typeof value === "number" && Number.isFinite(value) ? `${value.toFixed(digits)}%` : "-";
}

function markIndexDiffPct(market?: MarketSnapshot): number | null {
  if (
    !market ||
    typeof market.mark_price !== "number" ||
    typeof market.index_price !== "number" ||
    market.index_price === 0
  ) {
    return null;
  }
  return ((market.mark_price - market.index_price) / market.index_price) * 100;
}

function componentLabel(component: IndexComponent): string {
  return `${sourceShortName(component.source)} ${formatWeight(component.weight)}`;
}

type ComponentWeightDiff = {
  identity: string;
  source: string;
  symbol: string;
  oldWeight: number;
  newWeight: number;
};

function componentMap(components: IndexComponent[]): Map<string, IndexComponent> {
  return new Map(components.map((component) => [componentIdentity(component), component]));
}

function componentWeightDiffs(change: IndexComponentChange): ComponentWeightDiff[] {
  const oldComponents = componentMap(change.old_components);
  const newComponents = componentMap(change.new_components);
  const identities = Array.from(new Set([...oldComponents.keys(), ...newComponents.keys()])).sort();
  const diffs: ComponentWeightDiff[] = [];
  for (const identity of identities) {
    const oldComponent = oldComponents.get(identity);
    const newComponent = newComponents.get(identity);
    const component = newComponent ?? oldComponent;
    if (!component) {
      continue;
    }
    const oldWeight = oldComponent?.weight ?? 0;
    const newWeight = newComponent?.weight ?? 0;
    if (oldWeight === newWeight) {
      continue;
    }
    diffs.push({
      identity,
      source: component.source,
      symbol: component.symbol,
      oldWeight,
      newWeight
    });
  }
  return diffs;
}

function changeArrow(diff: ComponentWeightDiff): string {
  if (diff.newWeight > diff.oldWeight) {
    return "↑→";
  }
  if (diff.newWeight < diff.oldWeight) {
    return "↓→";
  }
  return "→";
}

function componentDiffLabel(diff: ComponentWeightDiff): string {
  return `${componentSourceName(diff.source)} (${diff.symbol}): 权重 ${formatWeight(diff.oldWeight)} ${changeArrow(diff)} ${formatWeight(diff.newWeight)}`;
}

function CompositionSummary({ change }: { change: IndexComponentChange }) {
  const diffs = componentWeightDiffs(change);
  return (
    <div className="index-component-summary">
      {diffs.length > 0 ? (
        diffs.map((diff) => (
          <div
            className={`index-component-change-line ${
              diff.newWeight > diff.oldWeight ? "index-component-change-up" : "index-component-change-down"
            }`}
            key={diff.identity}
          >
            <span className="index-component-change-dot" aria-hidden="true">
              •
            </span>
            <Typography.Text>{componentDiffLabel(diff)}</Typography.Text>
          </div>
        ))
      ) : (
        <Typography.Text type="secondary">无权重变化</Typography.Text>
      )}
    </div>
  );
}

function ComponentDiffTags({ change }: { change: IndexComponentChange }) {
  const diffs = componentWeightDiffs(change);
  return (
    <div className="index-component-diff-row">
      <Typography.Text type="secondary">成分变更</Typography.Text>
      <div className="index-component-change-list">
        {diffs.length > 0 ? (
          diffs.map((diff) => (
            <div
              className={`index-component-change-line ${
                diff.newWeight > diff.oldWeight ? "index-component-change-up" : "index-component-change-down"
              }`}
              key={diff.identity}
            >
              <span className="index-component-change-dot" aria-hidden="true">
                •
              </span>
              <Typography.Text>{componentDiffLabel(diff)}</Typography.Text>
            </div>
          ))
        ) : (
          <Typography.Text type="secondary">无权重变化</Typography.Text>
        )}
      </div>
    </div>
  );
}

function ExpandedChange(change: IndexComponentChange) {
  return (
    <div className="index-component-diff">
      <ComponentDiffTags change={change} />
    </div>
  );
}

type SnapshotChartRow = {
  snapshot: IndexComponentSnapshot;
  market?: MarketSnapshot;
  referencedFrom?: string;
  matchedReferenceComponents?: IndexComponent[];
};

function componentColor(index: number): string {
  const colors = ["#0f766e", "#2563eb", "#b45309", "#7c3aed", "#dc2626", "#0891b2", "#4d7c0f"];
  return colors[index % colors.length];
}

function sortedComponents(components: IndexComponent[]): IndexComponent[] {
  return [...components].sort((a, b) => (b.weight ?? 0) - (a.weight ?? 0));
}

const EXCHANGE_COMPONENT_ALIASES: Record<string, string[]> = {
  binance: ["binance", "binancefuture", "binancefutures", "binance_future", "binance_futures"],
  gate: ["gate", "gateio", "gatefuture", "gatefutures", "gate_futures"],
  bitget: ["bitget", "bitgetfuture", "bitgetfutures", "bitget_futures"],
  bybit: ["bybit", "bybitfuture", "bybitfutures", "bybit_futures"],
  okx: ["okx", "okex", "okxfuture", "okxfutures", "okx_futures"],
  aster: ["aster", "asterdex"],
  htx: ["htx", "huobi"],
  hyperliquid: ["hyperliquid"],
  mexc: ["mexc", "mxc"],
  mxc: ["mexc", "mxc"],
  kucoin: ["kucoin"],
  coinex: ["coinex"],
  cryptocom: ["cryptocom", "crypto", "crypto.com"],
  "crypto.com": ["cryptocom", "crypto", "crypto.com"]
};

function normalizeComponentSource(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]/g, "");
}

function componentMatchesExchange(component: IndexComponent, exchange: string): boolean {
  const source = normalizeComponentSource(component.source);
  const exchangeKey = normalizeComponentSource(exchange);
  const aliases = EXCHANGE_COMPONENT_ALIASES[exchange.toLowerCase()] ?? EXCHANGE_COMPONENT_ALIASES[exchangeKey] ?? [exchange];
  return aliases.some((alias) => source === normalizeComponentSource(alias));
}

function referencedSnapshotForMarket(
  market: MarketSnapshot,
  snapshots: IndexComponentSnapshot[],
): SnapshotChartRow | null {
  for (const snapshot of snapshots) {
    if (snapshot.exchange.toLowerCase() === market.exchange.toLowerCase()) {
      continue;
    }
    const components = snapshot.components.filter((component) => componentMatchesExchange(component, market.exchange));
    if (components.length === 0) {
      continue;
    }
    return {
      snapshot: {
        ...snapshot,
        exchange: market.exchange,
        symbol: market.symbol,
        component_hash: "",
        source: snapshot.source
      },
      market,
      referencedFrom: snapshot.exchange,
      matchedReferenceComponents: components
    };
  }
  return null;
}

function fallbackReferenceSnapshotForMarket(
  market: MarketSnapshot,
  snapshots: IndexComponentSnapshot[],
): SnapshotChartRow | null {
  const snapshot = snapshots.find((item) => item.exchange.toLowerCase() !== market.exchange.toLowerCase());
  if (!snapshot) {
    return null;
  }
  return {
    snapshot: {
      ...snapshot,
      exchange: market.exchange,
      symbol: market.symbol,
      component_hash: "",
      source: snapshot.source
    },
    market,
    referencedFrom: snapshot.exchange,
    matchedReferenceComponents: []
  };
}

function fundingSpread(markets: MarketSnapshot[], field: "funding_rate_pct" | "funding_next_rate_pct"): number | null {
  const values = markets
    .map((market) => market[field])
    .filter((value): value is number => typeof value === "number" && Number.isFinite(value));
  if (values.length < 2) {
    return null;
  }
  return Math.max(...values) - Math.min(...values);
}

function fundingTrendLabel(currentSpread: number | null, nextSpread: number | null): string {
  if (currentSpread === null || nextSpread === null) {
    return "预测不足";
  }
  const diff = nextSpread - currentSpread;
  if (Math.abs(diff) < 0.005) {
    return "预计持平";
  }
  return diff > 0 ? "预计扩大" : "预计收敛";
}

function IndexComponentMarketChart({
  snapshots,
  referenceSnapshots,
  markets,
  symbol
}: {
  snapshots: IndexComponentSnapshot[];
  referenceSnapshots: IndexComponentSnapshot[];
  markets: MarketSnapshot[];
  symbol: string;
}) {
  const futureMarkets = markets.filter((market) => market.market_type === "future");
  const marketByExchange = new Map(futureMarkets.map((market) => [market.exchange.toLowerCase(), market]));
  const rows: SnapshotChartRow[] = snapshots.map((snapshot) => ({
    snapshot,
    market: marketByExchange.get(snapshot.exchange.toLowerCase())
  }));
  const snapshotExchangeSet = new Set(snapshots.map((snapshot) => snapshot.exchange.toLowerCase()));
  futureMarkets
    .filter((market) => !snapshotExchangeSet.has(market.exchange.toLowerCase()))
    .forEach((market) => {
      const referenced =
        referencedSnapshotForMarket(market, referenceSnapshots) ??
        fallbackReferenceSnapshotForMarket(market, referenceSnapshots);
      if (referenced) {
        rows.push(referenced);
        return;
      }
      rows.push({
        snapshot: {
          exchange: market.exchange,
          symbol: market.symbol,
          components: [],
          component_hash: "",
          source: "",
          observed_at: market.timestamp
        },
        market
      });
    });

  const currentSpread = fundingSpread(futureMarkets, "funding_rate_pct");
  const nextSpread = fundingSpread(futureMarkets, "funding_next_rate_pct");
  const titleSymbol = symbol.trim().toUpperCase() || snapshots[0]?.symbol || futureMarkets[0]?.symbol || "";

  return (
    <section className="panel panel-wide index-component-market-panel" data-testid="index-component-market-chart">
      <div className="index-component-market-head">
        <div>
          <Typography.Title level={5}>指数成分与资金费率</Typography.Title>
          <Typography.Text type="secondary">{titleSymbol || "输入标的后查看各交易所指数篮子和资金费率"}</Typography.Text>
        </div>
        <Space wrap>
          <Tag color="blue">当前资金差 {formatPct(currentSpread)}</Tag>
          <Tag color="purple">下期资金差 {formatPct(nextSpread)}</Tag>
          <Tag color={fundingTrendLabel(currentSpread, nextSpread) === "预计扩大" ? "orange" : "green"}>
            {fundingTrendLabel(currentSpread, nextSpread)}
          </Tag>
        </Space>
      </div>
      {rows.length > 0 ? (
        <div className="index-component-market-rows">
          {rows.map(({ snapshot, market, referencedFrom, matchedReferenceComponents }) => {
            const components = sortedComponents(snapshot.components);
            const diff = markIndexDiffPct(market);
            const matchedComponents = matchedReferenceComponents ?? [];
            return (
              <div className="index-component-market-row" key={`${snapshot.exchange}:${snapshot.symbol}`}>
                <div className="index-component-market-label">
                  <Typography.Text strong>{snapshot.exchange}</Typography.Text>
                  <Typography.Text type="secondary">{market?.raw_symbol ?? snapshot.symbol}</Typography.Text>
                  {referencedFrom ? (
                    <Typography.Text className="index-component-reference-note" type="secondary">
                      参考 {referencedFrom} 指数成分
                    </Typography.Text>
                  ) : null}
                </div>
                <div className="index-component-stack">
                  {components.length > 0 ? (
                    components.map((component, index) => (
                      <div
                        className="index-component-stack-segment"
                        key={`${component.source}:${component.symbol}:${component.weight ?? ""}`}
                        style={{
                          width: `${Math.max((component.weight ?? 0) * 100, 2)}%`,
                          background: componentColor(index)
                        }}
                        title={componentLabel(component)}
                      />
                    ))
                  ) : (
                    <div className="index-component-stack-empty">暂无指数成分</div>
                  )}
                </div>
                <div className="index-component-metrics">
                  <Typography.Text>当前 {formatPct(market?.funding_rate_pct)}</Typography.Text>
                  <Typography.Text>下期 {formatPct(market?.funding_next_rate_pct)}</Typography.Text>
                  <Typography.Text type={typeof diff === "number" && diff > 0 ? "danger" : "secondary"}>
                    标记偏离 {formatPct(diff)}
                  </Typography.Text>
                </div>
                <div className="index-component-market-components">
                  {components.length > 0 ? (
                    <>
                      {components.map((component) => (
                        <Tag
                          className={
                            referencedFrom && componentMatchesExchange(component, snapshot.exchange)
                              ? "index-component-reference-match"
                              : undefined
                          }
                          key={`${snapshot.exchange}:${component.source}:${component.symbol}`}
                        >
                          {componentLabel(component)}
                        </Tag>
                      ))}
                      {referencedFrom ? (
                        <Typography.Text className="index-component-reference-detail" type="secondary">
                          {matchedComponents.length > 0
                            ? `参考篮子包含 ${matchedComponents.map(componentLabel).join(" / ")}`
                            : `参考篮子未包含 ${snapshot.exchange}`}
                        </Typography.Text>
                      ) : null}
                    </>
                  ) : (
                    <Typography.Text type="secondary">无成分数据</Typography.Text>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <div className="spread-history-empty">输入标的并查询后展示指数成分和资金费率</div>
      )}
    </section>
  );
}

const columns: ColumnsType<IndexComponentChange> = [
  { title: "时间(UTC+8)", dataIndex: "created_at", width: 132, render: formatUtcPlus8 },
  { title: "交易所", dataIndex: "exchange", width: 110 },
  { title: "标的", dataIndex: "symbol", width: 130 },
  {
    title: "摘要",
    key: "summary",
    width: 320,
    render: (_, item) => <CompositionSummary change={item} />
  },
  { title: "来源", dataIndex: "source", width: 100, render: providerShortName },
  {
    title: "告警",
    dataIndex: "alert_status",
    width: 90,
    render: (value: string) => <Tag color={value === "sent" ? "green" : value === "failed" ? "red" : "default"}>{value}</Tag>
  }
];

export function IndexComponentChangesPage() {
  const [changes, setChanges] = useState<IndexComponentChange[]>([]);
  const [snapshots, setSnapshots] = useState<IndexComponentSnapshot[]>([]);
  const [referenceSnapshots, setReferenceSnapshots] = useState<IndexComponentSnapshot[]>([]);
  const [watchItems, setWatchItems] = useState<IndexComponentWatchItem[]>([]);
  const [markets, setMarkets] = useState<MarketSnapshot[]>([]);
  const [loading, setLoading] = useState(false);
  const [watchLoading, setWatchLoading] = useState(false);
  const [symbol, setSymbol] = useState("");
  const [exchange, setExchange] = useState("");
  const [watchSymbol, setWatchSymbol] = useState("");

  const loadWatchlist = async () => {
    setWatchLoading(true);
    try {
      setWatchItems(await listIndexComponentWatchlist());
    } catch (exc) {
      message.error(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setWatchLoading(false);
    }
  };

  const addWatchItem = async () => {
    const nextSymbol = watchSymbol.trim().toUpperCase();
    if (!nextSymbol) {
      return;
    }
    setWatchLoading(true);
    try {
      await createIndexComponentWatchItem({ symbol: nextSymbol, note: null });
      setWatchSymbol("");
      await loadWatchlist();
      message.success("已加入监控");
    } catch (exc) {
      message.error(exc instanceof Error ? exc.message : String(exc));
      setWatchLoading(false);
    }
  };

  const removeWatchItem = async (item: IndexComponentWatchItem) => {
    setWatchLoading(true);
    try {
      await deleteIndexComponentWatchItem(item.id);
      setWatchItems((current) => current.filter((candidate) => candidate.id !== item.id));
      message.success("已删除");
    } catch (exc) {
      message.error(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setWatchLoading(false);
    }
  };

  const load = async () => {
    setLoading(true);
    try {
      const symbolFilter = symbol.trim().toUpperCase();
      const exchangeFilter = exchange.trim().toLowerCase();
      const nextChangesPromise = listIndexComponentChanges({
        symbol: symbolFilter,
        exchange: exchangeFilter,
        limit: 200
      });
      const nextSnapshotsPromise = symbolFilter
        ? listIndexComponentSnapshots({
            symbol: symbolFilter,
            exchange: exchangeFilter,
            limit: 500
          })
        : Promise.resolve([]);
      const nextReferenceSnapshotsPromise =
        symbolFilter && exchangeFilter
          ? listIndexComponentSnapshots({
              symbol: symbolFilter,
              limit: 500
            })
          : nextSnapshotsPromise;
      const nextMarketsPromise = symbolFilter
        ? listMarkets({
            symbol: symbolFilter,
            exchange: exchangeFilter,
            market_type: "future"
          })
        : Promise.resolve([]);
      const [nextChanges, nextSnapshots, nextReferenceSnapshots, nextMarkets] = await Promise.all([
        nextChangesPromise,
        nextSnapshotsPromise,
        nextReferenceSnapshotsPromise,
        nextMarketsPromise
      ]);
      setChanges(nextChanges);
      setSnapshots(nextSnapshots);
      setReferenceSnapshots(nextReferenceSnapshots);
      setMarkets(nextMarkets);
    } catch (exc) {
      message.error(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    void loadWatchlist();
    // Initial load only; filter changes are applied by the query button.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="page">
      <div className="toolbar">
        <Space className="toolbar-controls" wrap>
          <Typography.Title level={4}>指数成分变更</Typography.Title>
          <Input
            className="symbol-input"
            placeholder="标的模糊匹配"
            value={symbol}
            onChange={(event) => setSymbol(event.target.value)}
            allowClear
          />
          <Select
            className="index-exchange-select"
            value={exchange}
            options={EXCHANGE_OPTIONS}
            onChange={setExchange}
            popupMatchSelectWidth={false}
            style={{ width: 150 }}
            aria-label="交易所"
          />
        </Space>
        <Space className="toolbar-actions">
          <Button icon={<SearchOutlined />} onClick={() => void load()} loading={loading}>
            查询
          </Button>
          <Button icon={<ReloadOutlined />} onClick={() => void load()} loading={loading} />
        </Space>
      </div>
      <section className="panel panel-wide index-component-watch-panel">
        <div className="index-component-watch-head">
          <div>
            <Typography.Title level={5}>监控标的</Typography.Title>
            <Typography.Text type="secondary">只对这里的标的发送指数成分变更告警，未监控的变更仍记录为 muted。</Typography.Text>
          </div>
          <Space.Compact className="index-component-watch-add">
            <Input
              placeholder="新增监控标的"
              value={watchSymbol}
              onChange={(event) => setWatchSymbol(event.target.value)}
              onPressEnter={() => void addWatchItem()}
              allowClear
            />
            <Button icon={<PlusOutlined />} onClick={() => void addWatchItem()} loading={watchLoading}>
              加入监控
            </Button>
          </Space.Compact>
        </div>
        <div className="index-component-watch-list">
          {watchItems.length > 0 ? (
            watchItems.map((item) => (
              <Tag
                className="index-component-watch-tag"
                key={item.id}
                closable={false}
              >
                <Space size={6}>
                  <Typography.Text strong>{item.symbol}</Typography.Text>
                  {item.note ? <Typography.Text type="secondary">{item.note}</Typography.Text> : null}
                  <Button
                    size="small"
                    type="text"
                    danger
                    icon={<DeleteOutlined />}
                    onClick={() => void removeWatchItem(item)}
                    loading={watchLoading}
                  >
                    删除
                  </Button>
                </Space>
              </Tag>
            ))
          ) : (
            <Typography.Text type="secondary">暂无监控标的</Typography.Text>
          )}
        </div>
      </section>
      <IndexComponentMarketChart
        snapshots={snapshots}
        referenceSnapshots={referenceSnapshots}
        markets={markets}
        symbol={symbol}
      />
      <Table
        className="opportunity-table"
        columns={columns}
        dataSource={changes}
        expandable={{ expandedRowRender: ExpandedChange }}
        rowKey="id"
        loading={loading}
        size="middle"
        tableLayout="fixed"
      />
    </div>
  );
}
