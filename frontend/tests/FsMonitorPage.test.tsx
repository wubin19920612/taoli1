import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BorrowInfo, FsMonitorPage, candidateStage, signalReady } from "../src/pages/FsMonitorPage";
import type { FsCandidate, FsReport, FsSettings } from "../src/api/fsTypes";
import * as api from "../src/api/client";

vi.mock("../src/api/client", () => ({
  getFsReport: vi.fn(), getFsSettings: vi.fn(), updateFsSettings: vi.fn(),
  refreshFsMonitor: vi.fn(), getFsHistory: vi.fn(), getFsEvents: vi.fn(),
  listAccountConnections: vi.fn(), saveDashboardPassword: vi.fn()
}));
const settings: FsSettings = {
  enabled: true, astro_auto_create: false, exchanges: ["bybit", "binance", "okx", "bitget", "gate"], account_id: null, target_usdt: 1000, holding_hours: 8,
  scan_interval_seconds: 60, max_candidates: 15, min_volume_usdt: 100000,
  min_net_pct: 0.1, spot_fee_pct: 0.1, future_fee_pct: 0.055,
  execution_buffer_pct: 0.05, basis_stress_pct: 0.5
};
const candidate: FsCandidate = {
  symbol: "MEWUSDT", base: "MEW", exchange: "bybit", quote: "USDT", multiplier: 1,
  observed_at: new Date().toISOString(), valid_until: new Date(Date.now() + 60000).toISOString(),
  funding_pct: -1, interval_hours: 4, next_funding_at: null, hourly_borrow_pct: 0.03,
  borrow_rate_source: "public_no_vip", product_borrowable: true, public_vip_limit: 1000000,
  public_borrow: { scope: "bybit_no_vip", status: "available", source_url: "https://api.bybit.com/v5/spot-margin-trade/data",
    observed_at: new Date().toISOString(), reference_limit: 1000000, minimum_borrow: null, margin_supported: null },
  account_max_loan: null, available_inventory: null, inventory_at: null, account_state: "not_configured",
  quantity: 2000000, borrow_required: 2001000, capacity_usdt: null, spot_volume_usdt: 1000000,
  future_volume_usdt: 3000000, prices: {}, source_times: {}, fee_source: "configured_assumption",
  spot_fee_pct: 0.1, future_fee_pct: 0.055, identity_state: "unverified", asset_id: null,
  stage: "discovered", blockers: ["account_not_configured", "fees_unverified", "identity_unverified"], scenarios: []
};
const report: FsReport = { observed_at: new Date().toISOString(), scanning: false, stale: false,
  error: null, negative_count: 42, settings, candidates: [candidate], sources: [] };

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getFsReport).mockResolvedValue(report);
  vi.mocked(api.getFsSettings).mockResolvedValue(settings);
  vi.mocked(api.getFsEvents).mockResolvedValue([]);
  vi.mocked(api.getFsHistory).mockResolvedValue([]);
  vi.mocked(api.listAccountConnections).mockResolvedValue({ connections: [] } as never);
  vi.mocked(api.refreshFsMonitor).mockResolvedValue({ queued: true });
  vi.mocked(api.updateFsSettings).mockImplementation(async x => x);
});
afterEach(cleanup);

describe("FS observation page", () => {
  it("keeps public borrow support separate from unknown account inventory", async () => {
    render(<FsMonitorPage />);
    await screen.findByText("MEWUSDT");
    expect(screen.getByText(/产品支持/)).toBeTruthy();
    expect(screen.getByText("公开参考上限：1,000,000 MEW")).toBeTruthy();
    expect(screen.getByText("账户额度 / 库存：未接入查询")).toBeTruthy();
    expect(screen.getByText(/未选择借币账户；手续费采用配置假设；资产身份或单位尚未核验/)).toBeTruthy();
    expect(screen.queryByText("成本通过（预估）")).toBeNull();
  });
  it("invalidates expired green state on the client", () => {
    expect(candidateStage({ ...candidate, stage: "cost_pass", valid_until: new Date(Date.now() - 1).toISOString() })).toBe("stale");
  });
  it("keeps public signal separate from account cost and expires it", async () => {
    const ready = { ...candidate, signal_ready: true, astro_state: "existing" };
    expect(signalReady(ready)).toBe(true);
    expect(signalReady({ ...ready, valid_until: new Date(Date.now() - 1).toISOString() })).toBe(false);
    expect(signalReady({ ...ready, valid_until: null })).toBe(false);
    vi.mocked(api.getFsReport).mockResolvedValue({ ...report, candidates: [ready] });
    render(<FsMonitorPage />);
    await screen.findByText(/信号达标（公开预估）/);
    expect(screen.getByText(/同路线卡片已存在/)).toBeTruthy();
    expect(screen.queryByText("成本通过（预估）")).toBeNull();
  });
  it("allows explicit automatic disabled-card configuration without selecting an account", async () => {
    render(<FsMonitorPage />);
    await screen.findByText("MEWUSDT");
    fireEvent.click(screen.getByText("监测配置与借币账户"));
    fireEvent.click(screen.getByRole("switch", { name: "信号达标后自动建立 Astro 停用卡片" }));
    fireEvent.click(screen.getByRole("button", { name: /保存配置/ }));
    await waitFor(() => expect(api.updateFsSettings).toHaveBeenCalledWith({ ...settings, astro_auto_create: true }));
  });
  it("saves explicit settings and requests refresh without trade actions", async () => {
    render(<FsMonitorPage />);
    await screen.findByText("MEWUSDT");
    fireEvent.click(screen.getByText("监测配置与借币账户"));
    fireEvent.click(screen.getByRole("button", { name: /保存配置/ }));
    await waitFor(() => expect(api.updateFsSettings).toHaveBeenCalledWith(settings));
    fireEvent.click(screen.getByRole("button", { name: "请求刷新" }));
    await waitFor(() => expect(api.refreshFsMonitor).toHaveBeenCalledTimes(1));
  });
  it("shows same-symbol candidates separately by exchange and public-data gaps", async () => {
    vi.mocked(api.getFsReport).mockResolvedValue({ ...report,
      exchange_status: { bybit: "ok", okx: "ok", gate: "public_query_failed" },
      candidates: [candidate, { ...candidate, exchange: "okx", blockers: ["borrow_rate_requires_account"] }]
    });
    render(<FsMonitorPage />);
    await waitFor(() => expect(screen.getAllByText("MEWUSDT")).toHaveLength(2));
    expect(screen.getByText("OKX：1 条观察")).toBeTruthy();
    expect(screen.getByText("Gate：公开接口查询失败")).toBeTruthy();
    expect(screen.getByText("借币利率需账户接口确认")).toBeTruthy();
  });
  it("shows access errors and allows password retry", async () => {
    vi.mocked(api.getFsReport).mockRejectedValue(new Error("仪表盘密码错误"));
    render(<FsMonitorPage />);
    await screen.findByText("仪表盘密码错误");
    fireEvent.change(screen.getByLabelText("仪表盘密码"), { target: { value: "test-password" } });
    fireEvent.click(screen.getByRole("button", { name: "验证并重新读取" }));
    expect(api.saveDashboardPassword).toHaveBeenCalledWith("test-password");
  });
});

describe("FS borrow reference display", () => {
  it("shows zero inventory independently from a positive public limit", () => {
    render(<BorrowInfo row={{ ...candidate, account_state: "checked", account_max_loan: 123, available_inventory: 0 }} />);
    expect(screen.getByText("公开参考上限：1,000,000 MEW")).toBeTruthy();
    expect(screen.getByText("账户最大可借：123 MEW")).toBeTruthy();
    expect(screen.getByText("可借库存：0 MEW")).toBeTruthy();
  });
  it("does not turn minimum amounts into a public upper limit or round them to zero", () => {
    render(<BorrowInfo row={{ ...candidate, exchange: "bitget", public_borrow: { ...candidate.public_borrow!,
      scope: "bitget_cross", reference_limit: null, minimum_borrow: 1e-8 } }} />);
    expect(screen.getByText("最小借币量：0.00000001 MEW")).toBeTruthy();
    expect(screen.getByText("该公开接口未提供上限")).toBeTruthy();
    expect(screen.queryByText(/公开参考上限/)).toBeNull();
  });
  it("distinguishes Binance margin market support from base-coin borrowing", () => {
    render(<BorrowInfo row={{ ...candidate, exchange: "binance", product_borrowable: null,
      public_borrow: { ...candidate.public_borrow!, scope: "binance_margin", reference_limit: null, margin_supported: true } }} />);
    expect(screen.getByText("杠杆交易支持，借币待核验")).toBeTruthy();
    expect(screen.queryByText("产品支持借币")).toBeNull();
  });
  it("labels stale reference values and account results instead of implying current capacity", () => {
    render(<BorrowInfo row={{ ...candidate, stage: "stale", account_state: "checked", account_max_loan: 10, available_inventory: 0 }} />);
    expect(screen.getByText(/公开参考已过期/)).toBeTruthy();
    expect(screen.getByText("上次库存：0 MEW（已过期）")).toBeTruthy();
    expect(screen.queryByText(/账户最大可借/)).toBeNull();
  });
  it.each(["permission_denied", "query_failed"])("explains account %s without hiding public reference", state => {
    render(<BorrowInfo row={{ ...candidate, account_state: state }} />);
    expect(screen.getByText("公开参考上限：1,000,000 MEW")).toBeTruthy();
    expect(screen.getByText(state === "permission_denied" ? "账户额度 / 库存：账户查询权限不足" : "账户额度 / 库存：账户查询失败")).toBeTruthy();
  });
  it("shows a public query failure without borrowing the legacy cap", () => {
    render(<BorrowInfo row={{ ...candidate, public_borrow: { ...candidate.public_borrow!,
      status: "query_failed", observed_at: null, reference_limit: null } }} />);
    expect(screen.getByText("公开查询失败")).toBeTruthy();
    expect(screen.queryByText(/公开参考上限/)).toBeNull();
  });
});
