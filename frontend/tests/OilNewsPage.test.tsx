import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { OilNewsPage } from "../src/pages/OilNewsPage";

const settings = {
  enabled: true,
  poll_interval_seconds: 300,
  feishu_notifications_enabled: true,
  alert_min_severity: "high",
  alert_max_age_minutes: 120,
  bootstrap_alerts_enabled: false
};

const news = [{
  id: "oil-1",
  fingerprint: "fp-1",
  external_id: "ext-1",
  source: "Financial Times",
  source_feed: "google-oil-geopolitics",
  title: "Iran and Gulf states to meet in push for Hormuz deal",
  title_zh: "伊朗与海湾国家将会晤，推动达成霍尔木兹协议",
  url: "https://example.com/oil-1",
  summary: "Officials will discuss shipping arrangements.",
  summary_zh: "官员们将讨论航运安排。",
  published_at: "2026-09-11T04:00:00Z",
  fetched_at: "2026-09-11T04:01:00Z",
  categories: ["霍尔木兹/航运", "地缘冲突"],
  severity: "critical",
  impact_score: 100,
  direction: "short",
  confidence: 0.95,
  horizon: "6小时-3天",
  rationale: ["霍尔木兹协议降低封锁与运输中断风险"],
  risk_note: "若谈判破裂或出现新的袭击，空头逻辑可能快速反转。",
  market: {
    symbol: "CLUSDT",
    price: 98.4,
    change_1h_pct: -1.2,
    observed_at: "2026-09-11T04:01:00Z"
  },
  alert_status: "sent",
  alerted_at: "2026-09-11T04:01:00Z"
}];

describe("OilNewsPage", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        const method = init?.method ?? "GET";
        if (url.includes("/settings/oil-news")) {
          return Response.json(method === "PUT" ? JSON.parse(String(init?.body)) : settings);
        }
        if (url.includes("/oil-news/refresh")) {
          return Response.json({
            fetched_count: 1,
            relevant_count: 1,
            inserted_count: 0,
            alerted_count: 0,
            market: news[0].market,
            errors: []
          });
        }
        if (url.includes("/oil-news")) {
          return Response.json(news);
        }
        return Response.json({});
      })
    );
  });

  it("renders oil news direction, impact and market confirmation", async () => {
    render(<OilNewsPage />);

    expect(await screen.findByText("原油重大新闻")).toBeTruthy();
    expect(await screen.findByText("临时监控")).toBeTruthy();
    expect(await screen.findByText(news[0].title_zh)).toBeTruthy();
    expect(await screen.findByText(news[0].title)).toBeTruthy();
    expect(await screen.findByText("极重大")).toBeTruthy();
    expect((await screen.findAllByText("做空倾向")).length).toBeGreaterThanOrEqual(2);
    expect(await screen.findByText("行情确认")).toBeTruthy();
    expect(await screen.findByText("已推送")).toBeTruthy();

    fireEvent.click(screen.getByLabelText("Expand row"));
    expect(await screen.findByText("判定依据")).toBeTruthy();
    expect(await screen.findByText("中文摘要")).toBeTruthy();
    expect(await screen.findByText(news[0].summary_zh)).toBeTruthy();
    expect(await screen.findByText(/霍尔木兹协议降低封锁与运输中断风险/)).toBeTruthy();
    expect(await screen.findByText(/CLUSDT 98.40/)).toBeTruthy();
  });

  it("persists settings and supports a manual refresh", async () => {
    render(<OilNewsPage />);
    await screen.findByText(news[0].title);

    await userEvent.click(screen.getByRole("button", { name: /保存监测配置/ }));
    await waitFor(() => {
      const putCall = vi.mocked(fetch).mock.calls.find(
        (call) => String(call[0]).includes("/settings/oil-news") && call[1]?.method === "PUT"
      );
      expect(putCall).toBeTruthy();
      expect(JSON.parse(String(putCall?.[1]?.body))).toMatchObject(settings);
    });

    await userEvent.click(screen.getByRole("button", { name: /抓取最新/ }));
    await waitFor(() => {
      expect(vi.mocked(fetch).mock.calls.some(
        (call) => String(call[0]).includes("/oil-news/refresh") && call[1]?.method === "POST"
      )).toBe(true);
    });
  });
});
