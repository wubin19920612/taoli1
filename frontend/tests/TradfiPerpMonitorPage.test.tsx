import { act, fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import { TradfiPerpMonitorPage } from "../src/pages/TradfiPerpMonitorPage";

it("finishes the queued filter refresh after automatic polling is disabled", async () => {
  vi.useFakeTimers();
  const preview = { rows: [], matched_count: 0, hyperliquid_asset_count: 0, binance_symbol_count: 0 };
  let release!: (response: Response) => void;
  const fetchPreview = vi.fn()
    .mockImplementationOnce(() => new Promise<Response>(resolve => { release = resolve; }))
    .mockImplementation(() => Promise.resolve(Response.json(preview)));
  vi.stubGlobal("fetch", fetchPreview);
  const view = render(<TradfiPerpMonitorPage />);
  try {
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(fetchPreview).toHaveBeenCalledTimes(1);
    fireEvent.change(screen.getAllByRole("spinbutton")[0], { target: { value: "2000" } });
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    fireEvent.click(screen.getByRole("checkbox", { name: "10s 自动" }));
    await act(async () => { await vi.advanceTimersByTimeAsync(20_000); });
    expect(fetchPreview).toHaveBeenCalledTimes(1);
    await act(async () => release(Response.json(preview)));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(fetchPreview).toHaveBeenCalledTimes(2);
    expect(String(fetchPreview.mock.calls[1][0])).toContain("min_volume_24h_k=2000");
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(fetchPreview).toHaveBeenCalledTimes(2);
  } finally {
    view.unmount();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  }
});

it("serializes a filter refresh across the old deadline and restarts a full 10s interval", async () => {
  vi.useFakeTimers();
  const preview = { rows: [], matched_count: 0, hyperliquid_asset_count: 0, binance_symbol_count: 0 };
  let release!: (response: Response) => void;
  let hold = false;
  const fetchPreview = vi.fn(() => hold
    ? new Promise<Response>(resolve => { release = resolve; })
    : Promise.resolve(Response.json(preview)));
  vi.stubGlobal("fetch", fetchPreview);
  const view = render(<TradfiPerpMonitorPage />);
  try {
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(fetchPreview).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(9900); });
    hold = true;
    fireEvent.change(screen.getAllByRole("spinbutton")[0], { target: { value: "2000" } });
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(fetchPreview).toHaveBeenCalledTimes(2);
    expect(String(vi.mocked(fetch).mock.calls[1][0])).toContain("min_volume_24h_k=2000");
    await act(async () => { await vi.advanceTimersByTimeAsync(10_100); });
    expect(fetchPreview).toHaveBeenCalledTimes(2);
    hold = false;
    await act(async () => release(Response.json(preview)));
    await act(async () => { await vi.advanceTimersByTimeAsync(9999); });
    expect(fetchPreview).toHaveBeenCalledTimes(2);
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(fetchPreview).toHaveBeenCalledTimes(3);
  } finally {
    view.unmount();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  }
});
