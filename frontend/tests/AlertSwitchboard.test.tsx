import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Form, Input, Switch } from "antd";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  getAnnouncementSettings, getRiskSettings, listPhonePriceAlertRules,
  updateAnnouncementSettings, updateRiskSettings
} from "../src/api/client";
import type { AlertRule, AnnouncementSettings, RiskSettings } from "../src/api/types";
import { AlertSwitchboard } from "../src/components/AlertSwitchboard";

vi.mock("../src/api/client", () => ({
  getAnnouncementSettings: vi.fn(), getRiskSettings: vi.fn(), listPhonePriceAlertRules: vi.fn(),
  updateAnnouncementSettings: vi.fn(), updateRiskSettings: vi.fn()
}));

const risk: RiskSettings = {
  big_spread_alert_enabled: true, big_spread_alert_auto_create: true, big_spread_phone_enabled: false,
  big_spread_alert_pct: 3, big_spread_alert_hits: 2, big_spread_alert_cooldown_seconds: 600,
  big_spread_alert_symbol_cooldown_seconds: 1800, big_spread_phone_start_hour: 22,
  big_spread_phone_end_hour: 7, big_spread_phone_min_spread_pct: 5, big_spread_phone_max_per_night: 3,
  multi_exchange_premium_alert_enabled: true, multi_exchange_premium_threshold_pct: 2,
  multi_exchange_premium_cooldown_seconds: 900, min_volume_24h_usdt: 1000000,
  stale_after_seconds: 30, huge_spread_pct: 10, wide_spread_pct: 3, mark_index_deviation_pct: 1,
  funding_against_pct: 0.01, signal_slippage_buffer_pct: 0.05, min_effective_open_pct: 0.05,
  max_open_spread_decay_pct: 60, signal_validation_notional_usdt: 1000, orderbook_depth_safety_multiple: 2,
  orderbook_depth_band_pct: 0.1, min_top_of_book_depth_usdt: 0, signal_strategy_notes: "本地未保存备注",
  ticker_collision_symbols: [], excluded_symbols: [], ignored_exchanges: [], symbol_aliases: []
};
const announcement: AnnouncementSettings = {
  enabled: true, listing_delisting_alerts_enabled: true, launchpool_alerts_enabled: true,
  other_alerts_enabled: false, bootstrap_alerts_enabled: false, event_reminders_enabled: true,
  poll_interval_seconds: 30, alert_max_age_minutes: 30, record_exchanges: ["gate"],
  alert_exchanges: ["binance"], other_alerts_filter: "arbitrage_relevant", event_reminder_minutes_before: 30
};
const navigate = vi.fn();
function Harness({ loading = false, error = "" }: { loading?: boolean; error?: string }) {
  const [riskForm] = Form.useForm<RiskSettings>();
  return <>
    <AlertSwitchboard riskForm={riskForm} rules={[{ enabled: true }, { enabled: false }] as AlertRule[]}
      loading={loading} error={error} saveRisk={updateRiskSettings} onNavigate={navigate} />
    <Form form={riskForm} initialValues={risk}>
      <Form.Item name="big_spread_alert_enabled" valuePropName="checked"><Switch aria-label="风险表单大价差" /></Form.Item>
      <Form.Item name="signal_strategy_notes"><Input aria-label="风险表单备注" /></Form.Item>
    </Form>
  </>;
}

describe("AlertSwitchboard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getRiskSettings).mockReset().mockResolvedValue(risk);
    vi.mocked(updateRiskSettings).mockReset().mockImplementation(async (value) => value);
    vi.mocked(getAnnouncementSettings).mockReset().mockResolvedValue(announcement);
    vi.mocked(updateAnnouncementSettings).mockReset().mockImplementation(async (value) => value);
    vi.mocked(listPhonePriceAlertRules).mockReset().mockResolvedValue([
      { enabled: true }, { enabled: false }, { enabled: true }
    ] as Awaited<ReturnType<typeof listPhonePriceAlertRules>>);
  });

  it("renders all switches, threshold summaries and read-only rule counts", async () => {
    render(<Harness />);
    expect(await screen.findByText("已启用 2 / 共 3 条")).toBeTruthy();
    for (const name of ["大价差提醒", "大价差自动建卡", "夜间电话加急", "多交易所溢价告警",
      "交易所公告总开关", "上币与下币公告", "新币挖矿公告", "其他公告", "启动时历史公告", "公告事件提醒"]) {
      expect(screen.getByRole("switch", { name })).toBeTruthy();
    }
    expect(screen.getByText(/阈值 ≥3%.*连续 2 次.*600 秒.*1800 秒/)).toBeTruthy();
    expect(screen.getByText(/22:00-7:00 \/ ≥5% \/ 每晚≤3/)).toBeTruthy();
    expect(screen.getByText(/绝对值 ≥2%.*900 秒/)).toBeTruthy();
    const rules = screen.getByRole("group", { name: "普通告警规则" });
    expect(within(rules).getByText("已启用 1 / 共 2 条")).toBeTruthy();
    expect(within(rules).queryByRole("switch")).toBeNull();
    expect(within(screen.getByRole("group", { name: "手机价格提醒" })).queryByRole("switch")).toBeNull();
    expect(getRiskSettings).not.toHaveBeenCalled();
  });

  it.each([undefined, 7, 14, 0])("shows the new-symbol phone window for %s days", async (days) => {
    function PhoneSummary() {
      const [riskForm] = Form.useForm<RiskSettings>();
      return <>
        <AlertSwitchboard riskForm={riskForm} rules={[]} loading={false}
          saveRisk={updateRiskSettings} onNavigate={navigate} />
        <Form form={riskForm} initialValues={{ ...risk, big_spread_phone_new_symbol_days: days }} />
      </>;
    }
    render(<PhoneSummary />);
    const row = screen.getByRole("group", { name: "夜间电话加急" });
    await within(row).findByText(/22:00-7:00/);
    if (days === 0) {
      expect(within(row).queryByText(/仅新标的/)).toBeNull();
    } else {
      expect(within(row).getByText(new RegExp(`仅新标的\\(${days ?? 7}天\\)`))).toBeTruthy();
    }
  });

  it("saves only the clicked risk field over fresh server values and preserves local edits", async () => {
    const latest = { ...risk, big_spread_alert_pct: 9, excluded_symbols: ["NEWUSDT"], signal_strategy_notes: "服务器备注" };
    vi.mocked(getRiskSettings).mockResolvedValue(latest);
    render(<Harness />);
    fireEvent.click(screen.getByRole("switch", { name: "大价差提醒" }));
    await waitFor(() => expect(updateRiskSettings).toHaveBeenCalledWith({ ...latest, big_spread_alert_enabled: false }));
    expect(getRiskSettings).toHaveBeenCalledTimes(1);
    expect(screen.getByLabelText("风险表单大价差").getAttribute("aria-checked")).toBe("false");
    expect((screen.getByLabelText("风险表单备注") as HTMLInputElement).value).toBe("本地未保存备注");
  });

  it.each([
    ["大价差自动建卡", "big_spread_alert_auto_create", false],
    ["夜间电话加急", "big_spread_phone_enabled", true],
    ["多交易所溢价告警", "multi_exchange_premium_alert_enabled", false]
  ] as const)("saves %s using only its own risk field", async (label, field, checked) => {
    render(<Harness />);
    fireEvent.click(screen.getByRole("switch", { name: label }));
    await waitFor(() => expect(updateRiskSettings).toHaveBeenCalledWith({ ...risk, [field]: checked }));
  });

  it("rolls back both switches and reports a risk save failure", async () => {
    vi.mocked(updateRiskSettings).mockRejectedValue(new Error("风险保存不可用"));
    render(<Harness />);
    fireEvent.click(screen.getByRole("switch", { name: "大价差提醒" }));
    expect(await screen.findByText(/保存失败：风险保存不可用/)).toBeTruthy();
    expect(screen.getByRole("switch", { name: "大价差提醒" }).getAttribute("aria-checked")).toBe("true");
    expect(screen.getByLabelText("风险表单大价差").getAttribute("aria-checked")).toBe("true");
  });

  it("does not write stale settings when reading the latest risk settings fails", async () => {
    vi.mocked(getRiskSettings).mockRejectedValue(new Error("读取最新配置失败"));
    render(<Harness />);
    fireEvent.click(screen.getByRole("switch", { name: "大价差提醒" }));
    expect(await screen.findByText(/保存失败：读取最新配置失败/)).toBeTruthy();
    expect(updateRiskSettings).not.toHaveBeenCalled();
    expect(screen.getByRole("switch", { name: "大价差提醒" }).getAttribute("aria-checked")).toBe("true");
  });

  it("reflects edits from the existing risk form without issuing a save", async () => {
    render(<Harness />);
    fireEvent.click(screen.getByLabelText("风险表单大价差"));
    await waitFor(() => expect(screen.getByRole("switch", { name: "大价差提醒" }).getAttribute("aria-checked")).toBe("false"));
    expect(updateRiskSettings).not.toHaveBeenCalled();
  });

  it("keeps the board usable and offers retry when announcements fail to load", async () => {
    vi.mocked(getAnnouncementSettings).mockRejectedValueOnce(new Error("公告服务不可用"));
    render(<Harness />);
    expect(await screen.findByText("交易所公告加载失败")).toBeTruthy();
    expect(screen.getByRole("switch", { name: "大价差提醒" }).hasAttribute("disabled")).toBe(false);
    expect(screen.getByRole("switch", { name: "交易所公告总开关" }).hasAttribute("disabled")).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "重试公告设置" }));
    await waitFor(() => expect(screen.queryByText("交易所公告加载失败")).toBeNull());
    expect(screen.getByRole("switch", { name: "交易所公告总开关" }).getAttribute("aria-checked")).toBe("true");
  });

  it("re-reads announcements and preserves all other fields when changing a category", async () => {
    const latest = { ...announcement, poll_interval_seconds: 90, alert_exchanges: ["okx"], enabled: false };
    vi.mocked(getAnnouncementSettings).mockResolvedValueOnce(announcement).mockResolvedValueOnce(latest);
    render(<Harness />);
    await waitFor(() => expect(screen.getByRole("switch", { name: "其他公告" }).hasAttribute("disabled")).toBe(false));
    fireEvent.click(screen.getByRole("switch", { name: "其他公告" }));
    await waitFor(() => expect(updateAnnouncementSettings).toHaveBeenCalledWith({ ...latest, other_alerts_enabled: true }));
    expect(screen.getByRole("switch", { name: "交易所公告总开关" }).getAttribute("aria-checked")).toBe("false");
  });

  it("rolls back and reports announcement save failures", async () => {
    vi.mocked(updateAnnouncementSettings).mockRejectedValue(new Error("公告保存不可用"));
    render(<Harness />);
    await waitFor(() => expect(screen.getByRole("switch", { name: "交易所公告总开关" }).hasAttribute("disabled")).toBe(false));
    fireEvent.click(screen.getByRole("switch", { name: "交易所公告总开关" }));
    expect(await screen.findByText(/保存失败：公告保存不可用/)).toBeTruthy();
    expect(screen.getByRole("switch", { name: "交易所公告总开关" }).getAttribute("aria-checked")).toBe("true");
  });

  it("disables risk controls while their shared settings are loading or unavailable", async () => {
    const view = render(<Harness loading />);
    expect(screen.getByRole("switch", { name: "大价差提醒" }).hasAttribute("disabled")).toBe(true);
    view.rerender(<Harness error="加载失败" />);
    expect(screen.getByRole("switch", { name: "大价差提醒" }).hasAttribute("disabled")).toBe(true);
    expect(updateRiskSettings).not.toHaveBeenCalled();
    await screen.findByText("已启用 2 / 共 3 条");
  });

  it("navigates to the appropriate settings tab or announcements page", async () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole("button", { name: "大价差提醒去设置" }));
    expect(navigate).toHaveBeenLastCalledWith("risk", "anchor-big-spread");
    fireEvent.click(screen.getByRole("button", { name: "普通告警规则去设置" }));
    expect(navigate).toHaveBeenLastCalledWith("rules");
    fireEvent.click(screen.getByRole("button", { name: "手机价格提醒去设置" }));
    expect(navigate).toHaveBeenLastCalledWith("rules");
    expect(screen.getByRole("link", { name: "交易所公告总开关去设置" }).getAttribute("href")).toBe("?page=announcements");
    await screen.findByText("已启用 2 / 共 3 条");
  });
});
