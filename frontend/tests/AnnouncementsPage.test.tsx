import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AnnouncementsPage } from "../src/pages/AnnouncementsPage";

describe("AnnouncementsPage", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        const method = init?.method ?? "GET";
        if (url.includes("/settings/announcements")) {
          if (method === "PUT") {
            return Response.json(JSON.parse(String(init?.body)));
          }
          return Response.json({
            enabled: true,
            poll_interval_seconds: 120,
            record_exchanges: ["binance", "okx", "bybit", "gate", "bitget", "hyperliquid"],
            alert_exchanges: ["bybit"],
            listing_delisting_alerts_enabled: true,
            launchpool_alerts_enabled: true,
            bootstrap_alerts_enabled: false,
            alert_max_age_minutes: 30,
            event_reminders_enabled: true,
            event_reminder_minutes_before: 30
          });
        }
        if (url.includes("/announcements/exchanges")) {
          return Response.json([
            { label: "Binance", value: "binance" },
            { label: "OKX", value: "okx" },
            { label: "Bybit", value: "bybit" },
            { label: "Gate", value: "gate" },
            { label: "Bitget", value: "bitget" },
            { label: "Hyperliquid", value: "hyperliquid" }
          ]);
        }
        if (url.includes("/announcements")) {
          return Response.json([
            {
              id: "ann-1",
              exchange: "bybit",
              announcement_id: "new-listing",
              kind: "listing",
              title: "New listing: WDCUSDT Perpetual Contract",
              url: "https://announcements.bybit.com/en-US/article/new-listing/",
              source: "bybit-v5-announcements",
              category: "new_crypto",
              symbols: ["WDCUSDT"],
              market_type: "futures",
              asset_research: [
                {
                  symbol: "WDCUSDT",
                  canonical_symbol: "WDC",
                  asset_type: "crypto",
                  name: "Worldcoin Data Chain",
                  summary: "Worldcoin Data Chain，公开项目介绍。",
                  business: "Provides a data availability and settlement network.",
                  sources: [{ title: "CoinGecko", url: "https://www.coingecko.com/en/coins/wdc" }],
                  status: "found",
                  searched_at: "2026-05-30T08:01:00Z"
                }
              ],
              event_time: "2026-05-30T09:00:00Z",
              summary: "listing: symbols=WDCUSDT; market=futures; event_time=2026-05-30T09:00:00Z",
              published_at: "2026-05-30T08:00:00Z",
              fetched_at: "2026-05-30T08:01:00Z",
              alert_status: "sent",
              event_reminder_status: "pending",
              event_reminder_sent_at: null
            },
            {
              id: "ann-2",
              exchange: "gate",
              announcement_id: "gate-launchpool-363",
              kind: "launchpool",
              title: "Gate Launchpool Project #363",
              url: "https://www.gate.com/announcements/article/51430",
              source: "gate-next-announcements",
              category: "newspotlistings",
              symbols: [],
              market_type: null,
              asset_research: [],
              event_time: null,
              event_schedule: [],
              summary: "launchpool",
              published_at: "2026-05-30T08:05:00Z",
              fetched_at: "2026-05-30T08:06:00Z",
              alert_status: "sent",
              event_reminder_status: "not_applicable",
              event_reminder_sent_at: null
            }
          ]);
        }
        return Response.json([]);
      })
    );
  });

  it("loads announcement settings and renders recorded listing announcements", async () => {
    render(<AnnouncementsPage />);

    expect(await screen.findByText("交易所公告监控")).toBeTruthy();
    expect(await screen.findByText("上/下币告警 开启")).toBeTruthy();
    expect(await screen.findByText("Launchpool 告警 开启")).toBeTruthy();
    expect(await screen.findByText("New listing: WDCUSDT Perpetual Contract")).toBeTruthy();
    expect(await screen.findByText("WDCUSDT")).toBeTruthy();
    expect(await screen.findByText("合约")).toBeTruthy();
    expect(await screen.findByText("1/1 已找到")).toBeTruthy();
    expect(await screen.findByText("新币上架")).toBeTruthy();
    expect(await screen.findByText("待提醒")).toBeTruthy();
    expect(await screen.findByText("Gate Launchpool Project #363")).toBeTruthy();
    expect((await screen.findAllByText("Launchpool")).length).toBeGreaterThanOrEqual(1);
    expect(await screen.findByText("Launchpool 活动")).toBeTruthy();
    fireEvent.click(screen.getAllByLabelText("Expand row")[0]);
    expect(await screen.findByText("完整标题")).toBeTruthy();
    expect(await screen.findByText("结构化摘要")).toBeTruthy();
    expect(await screen.findByText("Worldcoin Data Chain")).toBeTruthy();
    expect(await screen.findByText("具体业务：")).toBeTruthy();
    expect(await screen.findByText("CoinGecko")).toBeTruthy();
    expect(await screen.findByText("原始分类")).toBeTruthy();
    expect(screen.getAllByText("listing: symbols=WDCUSDT; market=futures; event_time=2026-05-30T09:00:00Z").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("Bybit").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("上币").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("sent").length).toBeGreaterThanOrEqual(1);

    await waitFor(() => {
      const calls = vi.mocked(fetch).mock.calls.map((call) => String(call[0]));
      expect(calls.some((url) => url.includes("/settings/announcements"))).toBe(true);
      expect(calls.some((url) => url.includes("/announcements/exchanges"))).toBe(true);
      expect(calls.some((url) => url.includes("/announcements?"))).toBe(true);
    });
  });

  it("saves announcement monitoring settings", async () => {
    render(<AnnouncementsPage />);

    await screen.findByText("New listing: WDCUSDT Perpetual Contract");
    await userEvent.click(screen.getByRole("button", { name: /保存公告监控/ }));

    await waitFor(() => {
      const putCall = vi
        .mocked(fetch)
        .mock.calls.find((call) => String(call[0]).includes("/settings/announcements") && call[1]?.method === "PUT");
      expect(putCall).toBeTruthy();
      expect(JSON.parse(String(putCall?.[1]?.body))).toMatchObject({
        enabled: true,
        poll_interval_seconds: 120,
        record_exchanges: ["binance", "okx", "bybit", "gate", "bitget", "hyperliquid"],
        alert_exchanges: ["bybit"],
        listing_delisting_alerts_enabled: true,
        launchpool_alerts_enabled: true,
        bootstrap_alerts_enabled: false,
        alert_max_age_minutes: 30,
        event_reminders_enabled: true,
        event_reminder_minutes_before: 30
      });
    });
  });

  it("shows spot trading open separately from the notice's other stages", async () => {
    const originalFetch = vi.mocked(fetch);
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (!String(input).includes("/announcements?")) {
        return originalFetch(input, init);
      }
      return Response.json([{
        id: "pons", exchange: "okx", announcement_id: "pons", kind: "listing",
        title: "欧易关于上线 PONS/USDT 现货交易的公告", url: "https://www.okx.com/help/pons",
        source: "okx", category: "announcements-new-listings", symbols: ["PONS/USDT"],
        market_type: "spot", asset_research: [], event_time: "2026-09-15T14:30:00Z",
        event_schedule: [
          { symbol: "PONS/USDT", event_time: "2026-09-15T08:00:00Z", note: "充币开放" },
          { symbol: "PONS/USDT", event_time: "2026-09-15T13:30:00Z", note: "提前挂单（至 2026-09-15 22:30 UTC+8）" },
          { symbol: "PONS/USDT", event_time: "2026-09-15T14:30:00Z", note: "现货交易开盘" },
          { symbol: "PONS/USDT", event_time: "2026-09-15T16:30:00Z", note: "提币开放" }
        ],
        published_at: "2026-09-15T08:00:09Z", fetched_at: "2026-09-15T08:01:00Z",
        alert_status: "sent", event_reminder_status: "pending", event_reminder_sent_at: null
      }]);
    }));

    render(<AnnouncementsPage />);
    expect(await screen.findByText("现货交易开盘 09-15 22:30:00 UTC+8")).toBeTruthy();
    expect(screen.queryByText(/分批/)).toBeNull();
    fireEvent.click(screen.getByLabelText("Expand row"));
    expect(await screen.findByText("公告时间安排")).toBeTruthy();
    expect(await screen.findByText("充币开放")).toBeTruthy();
    expect(await screen.findByText("提前挂单（至 2026-09-15 22:30 UTC+8）")).toBeTruthy();
    expect(await screen.findByText("提币开放")).toBeTruthy();
  });

  it("labels derivatives opening as derivatives, not spot or batches", async () => {
    const originalFetch = vi.mocked(fetch);
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (!String(input).includes("/announcements?")) {
        return originalFetch(input, init);
      }
      return Response.json([{
        id: "wdc", exchange: "bybit", announcement_id: "wdc", kind: "listing",
        title: "New listing: WDCUSDT Perpetual Contract", url: "https://announcements.bybit.com/article/wdc",
        source: "bybit", category: "new_crypto", symbols: ["WDCUSDT"], market_type: "futures",
        asset_research: [], event_time: "2026-09-15T14:30:00Z",
        event_schedule: [{ symbol: "WDCUSDT", event_time: "2026-09-15T14:30:00Z", note: "合约交易开盘" }],
        published_at: "2026-09-15T08:00:00Z", fetched_at: "2026-09-15T08:01:00Z",
        alert_status: "sent", event_reminder_status: "pending", event_reminder_sent_at: null
      }]);
    }));

    render(<AnnouncementsPage />);
    expect(await screen.findByText("合约交易开盘 09-15 22:30:00 UTC+8")).toBeTruthy();
    expect(screen.queryByText(/分批/)).toBeNull();
    expect(screen.queryByText(/现货交易开盘/)).toBeNull();
  });
});
