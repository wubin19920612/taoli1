import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getShadowBuckets, getShadowStatus, getShadowTrades } from "../src/api/client";
import { ShadowLedgerPage } from "../src/pages/ShadowLedgerPage";

vi.mock("../src/api/client", () => ({ getShadowBuckets: vi.fn(), getShadowStatus: vi.fn(), getShadowTrades: vi.fn() }));

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getShadowStatus).mockResolvedValue({ enabled: false, ready: true, open_count: 0,
    dropped: 2, errors: 0, pending_writes: 0, catalog_updated_at: null, catalog_entries: 0, catalog_errors: 0 });
  vi.mocked(getShadowBuckets).mockResolvedValue([]);
  vi.mocked(getShadowTrades).mockResolvedValue([]);
});

describe("ShadowLedgerPage", () => {
  it("defaults to excluding fake observations and reloads when explicitly included", async () => {
    render(<ShadowLedgerPage />);
    await screen.findByText("记录已关闭");
    await waitFor(() => expect(getShadowBuckets).toHaveBeenCalledWith(expect.objectContaining({ dims: "variant,pair", include_fake: false })));
    expect(screen.getByText("暂无已平仓统计")).toBeTruthy();
    fireEvent.click(screen.getByRole("checkbox", { name: "包含假机会" }));
    await waitFor(() => expect(getShadowBuckets).toHaveBeenLastCalledWith(expect.objectContaining({ include_fake: true })));
    fireEvent.click(screen.getByRole("button", { name: "刷 新" }));
    await waitFor(() => expect(getShadowStatus).toHaveBeenCalledTimes(3));
  });

  it("shows an API failure rather than treating it as a successful empty result", async () => {
    vi.mocked(getShadowBuckets).mockRejectedValue(new Error("影子账本数据库尚未就绪"));
    render(<ShadowLedgerPage />);
    expect(await screen.findByText("影子账本数据库尚未就绪")).toBeTruthy();
  });

  it("shows route identity and fake reasons on recent trades", async () => {
    vi.mocked(getShadowTrades).mockResolvedValue([{
      id: "test", symbol: "ANTH", variant: "conv-half", pair: "hyperliquid/xyz → lighter", routes: "native/crossex",
      executable: true, fake_flag: true, fake_score: 2, fake_reasons: ["frozen_leg", "spread_plateau"],
      opened_at: "2026-11-03T00:00:00Z", closed_at: null, exit_reason: null, net_bps: null,
      open_spread_pct: 2.5, close_spread_pct: null, holding_seconds: null,
      buy_market_key: { exchange: "hyperliquid", raw_symbol: "xyz:ANTH", dex: "xyz" },
      sell_market_key: { exchange: "lighter", raw_symbol: "ANTH", dex: null }
    }]);
    render(<ShadowLedgerPage />);
    expect(await screen.findByText("hyperliquid:xyz:ANTH → lighter:ANTH")).toBeTruthy();
    expect(screen.getByText("假机会 (2) 价格冻结、价差平台")).toBeTruthy();
  });
});
