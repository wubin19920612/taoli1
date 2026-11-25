import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { getMobileCardControl, setMobileCardControl } from "../api/client";
import { MobileCardControlSwitch } from "./MobileCardControlSwitch";

vi.mock("../api/client", () => ({ getMobileCardControl: vi.fn(), setMobileCardControl: vi.fn() }));

const off = {
  env_enabled: true, dry_run: false, runtime_enabled: false, effective: false,
  changed_at: 1700000000, changed_by: "mobile:Chrome / Android"
};

beforeEach(() => {
  vi.mocked(getMobileCardControl).mockReset().mockResolvedValue(off);
  vi.mocked(setMobileCardControl).mockReset();
});

it("shows the environment, effective state and last change", async () => {
  render(<MobileCardControlSwitch />);
  expect((await screen.findByRole("switch")).getAttribute("aria-checked")).toBe("false");
  for (const text of [".env：允许", "dry-run：关闭", "最终状态：未生效", off.changed_by,
    new Date(off.changed_at * 1000).toLocaleString()]) {
    expect(screen.getByText(text, { exact: false })).toBeTruthy();
  }
});

it.each([
  [{ env_enabled: false }, ".env 未允许手机改卡"],
  [{ dry_run: true }, "dry-run 已开启"]
] as const)("disables the switch for %o", async (override, reason) => {
  vi.mocked(getMobileCardControl).mockResolvedValue({ ...off, ...override });
  render(<MobileCardControlSwitch />);
  expect((await screen.findByRole("switch")).hasAttribute("disabled")).toBe(true);
  expect(screen.getByText(reason, { exact: false })).toBeTruthy();
});

it("only sends PUT after confirmation and displays the returned state", async () => {
  vi.mocked(setMobileCardControl).mockResolvedValue({ ...off, runtime_enabled: true, effective: true });
  render(<MobileCardControlSwitch />);
  const toggle = await screen.findByRole("switch");
  fireEvent.click(toggle);
  expect(screen.getByText("打开后，已绑定的手机可以修改 Astro 卡片的暂停/禁开/禁清")).toBeTruthy();
  expect(setMobileCardControl).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "确认打开" }));
  await waitFor(() => expect(toggle.getAttribute("aria-checked")).toBe("true"));
  expect(setMobileCardControl).toHaveBeenCalledWith(true);
});

it("reports a failed disable", async () => {
  vi.mocked(getMobileCardControl).mockResolvedValue({ ...off, runtime_enabled: true, effective: true });
  vi.mocked(setMobileCardControl).mockRejectedValue(new Error("offline"));
  render(<MobileCardControlSwitch />);
  fireEvent.click(await screen.findByRole("switch"));
  expect(await screen.findByText("保存手机改卡开关失败")).toBeTruthy();
  expect(setMobileCardControl).toHaveBeenCalledWith(false);
});
