import {
  CloseOutlined,
  DeleteOutlined,
  DragOutlined,
  LineChartOutlined,
  MinusOutlined,
  PushpinOutlined,
  ReloadOutlined
} from "@ant-design/icons";
import { Alert, Button, Empty, Segmented, Spin, Tag, Tooltip, Typography } from "antd";
import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent } from "react";

import {
  getFloatingWatchSettings,
  listPairSpreadPresets,
  lookupInstrument,
  queryPairSpread
} from "../api/client";
import type {
  FloatingWatchSettings,
  InstrumentLookupResult,
  MarketSnapshot,
  PairSpreadPreset,
  PairSpreadQueryResult
} from "../api/types";
import {
  FLOATING_WATCH_UPDATED_EVENT,
  removeFloatingWatchPair,
  removeFloatingWatchSymbol
} from "../utils/floatingWatch";

const REFRESH_INTERVAL_MS = 10_000;
const POSITION_STORAGE_KEY = "taoli1:floating-watch-position.v1";
const COLLAPSED_STORAGE_KEY = "taoli1:floating-watch-collapsed.v1";
const emptySettings: FloatingWatchSettings = { symbols: [], pair_ids: [] };
const exchangeLabels: Record<string, string> = {
  aster: "Aster",
  binance: "Binance",
  binance_alpha: "Binance Alpha",
  bitget: "Bitget",
  bybit: "Bybit",
  gate: "Gate",
  hyperliquid: "Hyperliquid",
  okx: "OKX"
};

type WatchMode = "symbols" | "pairs";
type SavedPosition = { left: number; top: number };
type InstrumentState = { result: InstrumentLookupResult | null; error: string };
type PairState = { result: PairSpreadQueryResult | null; error: string };

async function mapWithConcurrency<T, R>(
  items: T[],
  concurrency: number,
  worker: (item: T) => Promise<R>
): Promise<R[]> {
  const results = new Array<R>(items.length);
  let nextIndex = 0;
  const runners = Array.from({ length: Math.min(concurrency, items.length) }, async () => {
    while (nextIndex < items.length) {
      const index = nextIndex;
      nextIndex += 1;
      results[index] = await worker(items[index]);
    }
  });
  await Promise.all(runners);
  return results;
}

function clampPosition(position: SavedPosition, width: number, height: number): SavedPosition {
  const maxLeft = Math.max(8, window.innerWidth - width - 8);
  const maxTop = Math.max(8, window.innerHeight - height - 8);
  return {
    left: Math.max(8, Math.min(maxLeft, position.left)),
    top: Math.max(8, Math.min(maxTop, position.top))
  };
}

function loadPosition(): SavedPosition | null {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(POSITION_STORAGE_KEY) ?? "null") as Partial<SavedPosition> | null;
    return parsed && Number.isFinite(parsed.left) && Number.isFinite(parsed.top)
      ? { left: Number(parsed.left), top: Number(parsed.top) }
      : null;
  } catch {
    return null;
  }
}

function marketPrice(market: MarketSnapshot | null): number | null {
  if (!market) return null;
  if (typeof market.bid === "number" && typeof market.ask === "number") {
    return (market.bid + market.ask) / 2;
  }
  return market.mark_price ?? market.bid ?? market.ask ?? null;
}

function instrumentPriceRange(result: InstrumentLookupResult | null): { min: number; max: number } | null {
  const prices = result?.exchanges.flatMap((item) => [marketPrice(item.spot), marketPrice(item.future)])
    .filter((value): value is number => typeof value === "number" && Number.isFinite(value)) ?? [];
  return prices.length ? { min: Math.min(...prices), max: Math.max(...prices) } : null;
}

function price(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "-";
  const absolute = Math.abs(value);
  if (absolute >= 10_000) return value.toLocaleString("en-US", { maximumFractionDigits: 2 });
  if (absolute >= 100) return value.toFixed(3).replace(/0+$/, "").replace(/\.$/, "");
  if (absolute >= 1) return value.toFixed(5).replace(/0+$/, "").replace(/\.$/, "");
  return value.toPrecision(6);
}

function signedPct(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "-";
  return `${value >= 0 ? "+" : ""}${value.toFixed(3)}%`;
}

function tone(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "neutral";
  return value > 0 ? "positive" : value < 0 ? "negative" : "neutral";
}

function marketLabel(value: string): string {
  return value === "spot" ? "现货" : "永续";
}

function pairLegLabel(preset: PairSpreadPreset, side: 1 | 2): string {
  const exchange = preset[`leg${side}_exchange`];
  const dex = preset[`leg${side}_dex`];
  const venue = exchange === "hyperliquid" && dex && dex !== "main"
    ? `Hyperliquid ${dex}`
    : exchangeLabels[exchange] ?? exchange;
  return `${venue} ${marketLabel(preset[`leg${side}_market_type`])}`;
}

function openInstrument(symbol: string): void {
  const url = new URL(window.location.href);
  url.searchParams.set("page", "instrument");
  url.searchParams.set("symbol", symbol);
  window.history.pushState({}, "", `${url.pathname}${url.search}${url.hash}`);
  window.dispatchEvent(new Event("taoli1:navigate"));
}

function openPair(preset: PairSpreadPreset): void {
  const url = new URL(window.location.href);
  url.searchParams.set("page", "pair-monitor");
  url.searchParams.delete("symbol");
  ([1, 2] as const).forEach((side) => {
    url.searchParams.set(`leg${side}_exchange`, preset[`leg${side}_exchange`]);
    url.searchParams.set(`leg${side}_market_type`, preset[`leg${side}_market_type`]);
    url.searchParams.set(`leg${side}_symbol`, preset[`leg${side}_symbol`]);
    const dex = preset[`leg${side}_dex`];
    if (dex) url.searchParams.set(`leg${side}_dex`, dex);
    else url.searchParams.delete(`leg${side}_dex`);
  });
  url.searchParams.set("leg2_multiplier", String(preset.leg2_multiplier));
  url.searchParams.set("hours", String(preset.hours));
  url.searchParams.set("interval_seconds", String(preset.intervalSeconds));
  url.searchParams.delete("interval_minutes");
  window.history.pushState({}, "", `${url.pathname}${url.search}${url.hash}`);
  window.dispatchEvent(new Event("taoli1:navigate"));
}

export function FloatingWatchPanel({ visible, onClose }: { visible: boolean; onClose: () => void }) {
  const [mode, setMode] = useState<WatchMode>("symbols");
  const [collapsed, setCollapsed] = useState(() => window.localStorage.getItem(COLLAPSED_STORAGE_KEY) === "1");
  const [position, setPosition] = useState<SavedPosition | null>(loadPosition);
  const [settings, setSettings] = useState<FloatingWatchSettings>(emptySettings);
  const [presets, setPresets] = useState<PairSpreadPreset[]>([]);
  const [instruments, setInstruments] = useState<Record<string, InstrumentState>>({});
  const [pairs, setPairs] = useState<Record<string, PairState>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [removing, setRemoving] = useState("");
  const panelRef = useRef<HTMLElement | null>(null);
  const refreshQueue = useRef<Promise<void>>(Promise.resolve());

  const refresh = useCallback((requestedMode: WatchMode): Promise<void> => {
    const run = async () => {
      setLoading(true);
      try {
        const nextSettings = await getFloatingWatchSettings();
        setSettings(nextSettings);
        if (requestedMode === "symbols") {
          const instrumentEntries = await mapWithConcurrency(
            nextSettings.symbols,
            3,
            async (symbol): Promise<[string, InstrumentState]> => {
              try {
                return [symbol, { result: await lookupInstrument(symbol), error: "" }];
              } catch (caught) {
                return [symbol, { result: null, error: caught instanceof Error ? caught.message : String(caught) }];
              }
            }
          );
          setInstruments(Object.fromEntries(instrumentEntries));
        } else {
          const allPresets = await listPairSpreadPresets();
          const watchedPresets = nextSettings.pair_ids
            .map((id) => allPresets.find((preset) => preset.id === id))
            .filter((preset): preset is PairSpreadPreset => Boolean(preset));
          const pairEntries = await mapWithConcurrency(
            watchedPresets,
            3,
            async (preset): Promise<[string, PairState]> => {
              try {
                const result = await queryPairSpread({
                  leg1_exchange: preset.leg1_exchange,
                  leg1_market_type: preset.leg1_market_type,
                  leg1_dex: preset.leg1_dex || undefined,
                  leg1_symbol: preset.leg1_symbol,
                  leg2_exchange: preset.leg2_exchange,
                  leg2_market_type: preset.leg2_market_type,
                  leg2_dex: preset.leg2_dex || undefined,
                  leg2_symbol: preset.leg2_symbol,
                  leg2_multiplier: preset.leg2_multiplier,
                  hours: 1,
                  interval_seconds: 5,
                  include_current: true
                });
                return [preset.id, { result, error: "" }];
              } catch (caught) {
                return [preset.id, { result: null, error: caught instanceof Error ? caught.message : String(caught) }];
              }
            }
          );
          setPresets(watchedPresets);
          setPairs(Object.fromEntries(pairEntries));
        }
        setError("");
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : String(caught));
      } finally {
        setLoading(false);
      }
    };
    const queued = refreshQueue.current.catch(() => undefined).then(run);
    refreshQueue.current = queued;
    return queued;
  }, []);

  useEffect(() => {
    if (!visible || collapsed) return undefined;
    let stopped = false;
    let timer: number | undefined;
    const poll = async () => {
      await refresh(mode);
      if (!stopped) timer = window.setTimeout(() => void poll(), REFRESH_INTERVAL_MS);
    };
    void poll();
    return () => {
      stopped = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [collapsed, mode, refresh, visible]);

  useEffect(() => {
    const handleUpdate = (event: Event) => {
      const updated = (event as CustomEvent<FloatingWatchSettings>).detail;
      if (updated) setSettings(updated);
      setCollapsed(false);
      window.localStorage.setItem(COLLAPSED_STORAGE_KEY, "0");
      if (visible) void refresh(mode);
    };
    window.addEventListener(FLOATING_WATCH_UPDATED_EVENT, handleUpdate);
    return () => window.removeEventListener(FLOATING_WATCH_UPDATED_EVENT, handleUpdate);
  }, [mode, refresh, visible]);

  useEffect(() => {
    if (!visible || !position) return undefined;
    const keepPanelInViewport = () => {
      if (window.innerWidth <= 600) return;
      const panel = panelRef.current;
      if (!panel) return;
      setPosition((current) => {
        if (!current) return current;
        const bounds = panel.getBoundingClientRect();
        const next = clampPosition(current, bounds.width, bounds.height);
        if (next.left === current.left && next.top === current.top) return current;
        window.localStorage.setItem(POSITION_STORAGE_KEY, JSON.stringify(next));
        return next;
      });
    };
    keepPanelInViewport();
    window.addEventListener("resize", keepPanelInViewport);
    return () => window.removeEventListener("resize", keepPanelInViewport);
  }, [collapsed, position, visible]);

  const missingPairIds = useMemo(
    () => settings.pair_ids.filter((id) => !presets.some((preset) => preset.id === id)),
    [presets, settings.pair_ids]
  );

  if (!visible) return null;

  const panelStyle: CSSProperties | undefined = position
    ? { left: position.left, top: position.top, right: "auto", bottom: "auto" }
    : undefined;

  const startDrag = (event: ReactPointerEvent<HTMLDivElement>) => {
    if ((event.target as HTMLElement).closest("button")) return;
    if (window.innerWidth <= 600) return;
    const panel = event.currentTarget.closest<HTMLElement>(".floating-watch-panel");
    if (!panel) return;
    const bounds = panel.getBoundingClientRect();
    const offsetX = event.clientX - bounds.left;
    const offsetY = event.clientY - bounds.top;
    const move = (pointerEvent: PointerEvent) => {
      setPosition(clampPosition(
        { left: pointerEvent.clientX - offsetX, top: pointerEvent.clientY - offsetY },
        bounds.width,
        bounds.height
      ));
    };
    const stop = (pointerEvent: PointerEvent) => {
      move(pointerEvent);
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
      const next = clampPosition(
        { left: pointerEvent.clientX - offsetX, top: pointerEvent.clientY - offsetY },
        bounds.width,
        bounds.height
      );
      window.localStorage.setItem(POSITION_STORAGE_KEY, JSON.stringify(next));
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
  };

  const setPanelCollapsed = (next: boolean) => {
    setCollapsed(next);
    window.localStorage.setItem(COLLAPSED_STORAGE_KEY, next ? "1" : "0");
  };

  const removeSymbol = async (symbol: string) => {
    setRemoving(`symbol:${symbol}`);
    try {
      const next = await removeFloatingWatchSymbol(symbol);
      setSettings(next);
      setInstruments((current) => {
        const copy = { ...current };
        delete copy[symbol];
        return copy;
      });
      setError("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setRemoving("");
    }
  };

  const removePair = async (pairId: string) => {
    setRemoving(`pair:${pairId}`);
    try {
      const next = await removeFloatingWatchPair(pairId);
      setSettings(next);
      setPresets((current) => current.filter((preset) => preset.id !== pairId));
      setError("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setRemoving("");
    }
  };

  return (
    <aside
      ref={panelRef}
      className={`floating-watch-panel${collapsed ? " floating-watch-panel-collapsed" : ""}`}
      style={panelStyle}
      aria-label="关注浮窗"
    >
      <div className="floating-watch-header" onPointerDown={startDrag}>
        <span className="floating-watch-drag" aria-hidden="true"><DragOutlined /></span>
        <PushpinOutlined />
        <Typography.Text strong>关注行情</Typography.Text>
        <Tag>{settings.symbols.length + settings.pair_ids.length}</Tag>
        <span className="floating-watch-header-actions">
          {!collapsed ? (
            <Tooltip title="立即刷新">
              <Button aria-label="刷新关注行情" type="text" size="small" icon={<ReloadOutlined spin={loading} />} onClick={() => void refresh(mode)} />
            </Tooltip>
          ) : null}
          <Tooltip title={collapsed ? "展开" : "收起"}>
            <Button
              aria-label={collapsed ? "展开关注浮窗" : "收起关注浮窗"}
              type="text"
              size="small"
              icon={collapsed ? <LineChartOutlined /> : <MinusOutlined />}
              onClick={() => setPanelCollapsed(!collapsed)}
            />
          </Tooltip>
          <Tooltip title="隐藏">
            <Button aria-label="隐藏关注浮窗" type="text" size="small" icon={<CloseOutlined />} onClick={onClose} />
          </Tooltip>
        </span>
      </div>
      {!collapsed ? (
        <div className="floating-watch-body">
          <Segmented
            block
            size="small"
            value={mode}
            options={[
              { label: `标的 ${settings.symbols.length}`, value: "symbols" },
              { label: `交易对 ${settings.pair_ids.length}`, value: "pairs" }
            ]}
            onChange={(value) => setMode(value as WatchMode)}
          />
          {error ? <Alert type="warning" showIcon message={error} /> : null}
          {loading && settings.symbols.length + settings.pair_ids.length === 0 ? <Spin className="floating-watch-loading" /> : null}
          {mode === "symbols" ? (
            <div className="floating-watch-list">
              {settings.symbols.map((symbol) => {
                const state = instruments[symbol];
                const range = instrumentPriceRange(state?.result ?? null);
                const bestSpread = state?.result?.spreads.length
                  ? Math.max(...state.result.spreads.map((spread) => spread.executable_spread_pct))
                  : null;
                return (
                  <div className="floating-watch-row" key={symbol}>
                    <button className="floating-watch-row-main" type="button" onClick={() => openInstrument(symbol)}>
                      <span className="floating-watch-row-title">{symbol.replace(/USDT$/, "")}</span>
                      <span className="floating-watch-row-sub">{range ? `${price(range.min)} - ${price(range.max)}` : state?.error || "等待刷新"}</span>
                      <span className={`floating-watch-value floating-watch-value-${tone(bestSpread)}`}>{signedPct(bestSpread)}</span>
                    </button>
                    <Tooltip title="取消关注">
                      <Button
                        aria-label={`取消关注标的 ${symbol}`}
                        type="text"
                        size="small"
                        danger
                        icon={<DeleteOutlined />}
                        loading={removing === `symbol:${symbol}`}
                        onClick={() => void removeSymbol(symbol)}
                      />
                    </Tooltip>
                  </div>
                );
              })}
              {!loading && settings.symbols.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有关注标的" /> : null}
            </div>
          ) : (
            <div className="floating-watch-list">
              {presets.map((preset) => {
                const state = pairs[preset.id];
                const current = state?.result?.current;
                const spread = current?.open_spread_pct ?? current?.spread_pct ?? null;
                return (
                  <div className="floating-watch-row floating-watch-pair-row" key={preset.id}>
                    <button className="floating-watch-row-main" type="button" onClick={() => openPair(preset)}>
                      <span className="floating-watch-row-title">{preset.leg1_symbol.replace(/USDT$/, "")} / {preset.leg2_symbol.replace(/USDT$/, "")}</span>
                      <span className="floating-watch-row-sub">{pairLegLabel(preset, 1)} {price(current?.leg1.price)} → {pairLegLabel(preset, 2)} {price(current?.leg2.price)}</span>
                      <span className={`floating-watch-value floating-watch-value-${tone(spread)}`}>{state?.error ? "异常" : signedPct(spread)}</span>
                    </button>
                    <Tooltip title="取消关注">
                      <Button
                        aria-label={`取消关注交易对 ${preset.id}`}
                        type="text"
                        size="small"
                        danger
                        icon={<DeleteOutlined />}
                        loading={removing === `pair:${preset.id}`}
                        onClick={() => void removePair(preset.id)}
                      />
                    </Tooltip>
                  </div>
                );
              })}
              {missingPairIds.map((pairId) => (
                <div className="floating-watch-row floating-watch-missing-row" key={pairId}>
                  <div className="floating-watch-row-main">
                    <span className="floating-watch-row-title">已删除的交易对</span>
                    <span className="floating-watch-row-sub">保存配置不存在，请移出浮窗</span>
                  </div>
                  <Button aria-label={`取消关注交易对 ${pairId}`} type="text" size="small" danger icon={<DeleteOutlined />} onClick={() => void removePair(pairId)} />
                </div>
              ))}
              {!loading && settings.pair_ids.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有关注交易对" /> : null}
            </div>
          )}
          <div className="floating-watch-footer">
            <Typography.Text type="secondary">每 10 秒刷新 · 点击行查看详情</Typography.Text>
          </div>
        </div>
      ) : null}
    </aside>
  );
}
