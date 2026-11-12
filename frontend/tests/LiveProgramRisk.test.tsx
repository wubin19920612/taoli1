import { act, cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LiveExecPage } from "../src/pages/LiveExecPage";
import { fixtures } from "./liveExecAdminFixtures";
import { liveFixtures } from "./liveExecFixtures";
import { remoteAt, remoteSnapshot } from "./liveExecRemoteFixtures";

// Fixed source: 679791a, external_accounting.py result and round fourteen contract.
const proof = { verified: true, scope: "program", day: "2026-11-12", captured_at: Date.parse(remoteAt) / 1000,
  snapshot_version: 17, revision: 0, equity_revision: 0, program_total: "-50.000000000000000001",
  program_daily: "-4.000000000000000001", program_funding_total: "-0.125", program_upnl: "-1.25",
  equity_daily: "999999", program_journal: "synthetic-journal" };
let income: Record<string, unknown> | null;
let remote: Record<string, unknown>;
let summary: Record<string, unknown>;
let externalError: boolean;
const flush = async () => { await act(async () => { for (let i = 0; i < 40; i++) await Promise.resolve(); }); };
const risk = () => within(screen.getByTestId("live-program-risk"));
const value = (label: string) => risk().getByText(label).closest(".ant-descriptions-item")!
  .querySelector(".ant-descriptions-item-content")!.textContent;

beforeEach(() => {
  vi.useFakeTimers(); vi.setSystemTime(new Date(remoteAt));
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
  income = { ...proof }; remote = structuredClone(remoteSnapshot); externalError = false;
  summary = { ...liveFixtures.summary, equity_baseline_revision: 0, effective_risk_limits: {
    max_equity_drop_usd: 50, daily_loss_stop_usd: 5, total_loss_stop_usd: 20,
  } };
  vi.stubGlobal("fetch", vi.fn(async (input: string) => {
    const path = new URL(input).pathname.replace("/api/live-exec/", "");
    const data = path === "summary" ? summary : path === "remote-positions" ? remote
      : path === "external-positions" ? { ...fixtures["external-positions"], income }
        : fixtures[path as keyof typeof fixtures] ?? liveFixtures[path as keyof typeof liveFixtures];
    return { ok: true, status: 200, json: async () => path === "external-positions" && externalError
      ? { available: false, error: "timeout" } : { available: true, data: structuredClone(data) } };
  }));
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("第十四轮程序风险与整户观测分开显示", () => {
  it("compares the verified program total with the effective limit, preserving decimal precision", async () => {
    render(<LiveExecPage />); await flush();
    expect(value("程序累计损益")).toBe("-50.000000000000000001 USD");
    expect(value("程序今日损益（UTC）")).toBe("-4.000000000000000001 USD");
    expect(value("程序累计资金费")).toBe("-0.125 USD");
    expect(value("程序当前浮盈亏")).toBe("-1.25 USD");
    expect(value("程序损益停机阈值")).toBe("50.00 USD");
    expect(risk().getByText("已达程序损益停机阈值").className).toContain("red");
    expect(risk().getByText(/已实现损益、手续费、资金费和当前浮盈亏/)).toBeTruthy();
    expect(risk().queryByText(/999999/)).toBeNull();
    const account = within(screen.getByTestId("live-account-crossex"));
    expect(account.getByText("140.00").className).not.toContain("danger");
    expect(screen.getByTestId("live-account-crossex").textContent).not.toContain("/ 50.00");
    expect(screen.getAllByText(/整户权益仅作观测/).length).toBeGreaterThan(0);
  });

  it.each(["0", "12.5", "-49.999999999999999999"])("does not flag %s as reaching a 50 USD loss limit", async total => {
    income!.program_total = total;
    render(<LiveExecPage />); await flush();
    expect(value("程序累计损益")).toBe(`${total} USD`);
    expect(risk().getByText("未达程序损益停机阈值")).toBeTruthy();
    expect(risk().queryByText("已达程序损益停机阈值")).toBeNull();
  });

  it.each(["-50", "-5e1"])("includes equality at the negative limit: %s", async total => {
    income!.program_total = total;
    render(<LiveExecPage />); await flush();
    expect(risk().getByText("已达程序损益停机阈值")).toBeTruthy();
  });

  it.each(["-50\n", "1e10000", "-Infinity", "-" + "5".repeat(512)])("rejects a non-contract decimal %j", async total => {
    income!.program_total = total;
    render(<LiveExecPage />); await flush();
    expect(value("程序累计损益")).toBe("— USD");
    expect(risk().queryByText("已达程序损益停机阈值")).toBeNull();
  });

  it.each(["missing", "unverified", "old_scope", "version", "stale", "future", "missing_time", "snapshot_missing",
    "snapshot_old", "revision", "wrong_day", "invalid_decimal", "read_failure", "missing_journal", "missing_revision"])(
    "does not present %s proof as a current program stop basis", async state => {
      if (state === "missing") income = null;
      if (state === "unverified") income!.verified = false;
      if (state === "old_scope") delete income!.scope;
      if (state === "version") income!.snapshot_version = 16;
      if (state === "stale") income!.captured_at = proof.captured_at - 11;
      if (state === "future") income!.captured_at = proof.captured_at + 1;
      if (state === "missing_time") delete income!.captured_at;
      if (state === "snapshot_missing") remote.available = false;
      if (state === "snapshot_old") remote.stale = true;
      if (state === "revision") income!.equity_revision = 1;
      if (state === "wrong_day") income!.day = "2026-11-11";
      if (state === "invalid_decimal") income!.program_total = "NaN";
      if (state === "read_failure") externalError = true;
      if (state === "missing_journal") delete income!.program_journal;
      if (state === "missing_revision") delete income!.revision;
      render(<LiveExecPage />); await flush();
      expect(value("程序累计损益")).toBe("— USD");
      expect(risk().getByText(/程序损益证明不可用或已过期/)).toBeTruthy();
      expect(risk().queryByText("已达程序损益停机阈值")).toBeNull();
      expect(risk().queryByText("未达程序损益停机阈值")).toBeNull();
      expect(risk().queryByText(/999999/)).toBeNull();
    });

  it("revokes current proof on a newer snapshot and on local expiry without fabricating zero", async () => {
    render(<LiveExecPage />); await flush();
    expect(value("程序累计损益")).toContain(proof.program_total);
    remote.snapshot_version = 18;
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(value("程序累计损益")).toBe("— USD");
    remote.snapshot_version = 17;
    await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
    expect(value("程序累计损益")).toBe("— USD");
  });

  it("refreshes program proof alongside remote snapshots at five seconds", async () => {
    render(<LiveExecPage />); await flush();
    const nextAt = "2026-11-12T00:00:05Z";
    remote = { ...remoteSnapshot, snapshot_at: nextAt, snapshot_version: 18,
      positions: remoteSnapshot.positions.map(row => ({ ...row, snapshot_at: nextAt, snapshot_version: 18 })) };
    income = { ...proof, captured_at: proof.captured_at + 5, snapshot_version: 18, program_total: "-10.125" };
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(value("程序累计损益")).toBe("-10.125 USD");
    expect(risk().getByText("未达程序损益停机阈值")).toBeTruthy();
  });

  it("uses a stricter code/env cap as well as the frozen runtime setting", async () => {
    summary.caps = { ...liveFixtures.summary.caps, equity_drop_max_usd: 30 };
    income!.program_total = "-30";
    render(<LiveExecPage />); await flush();
    expect(value("程序损益停机阈值")).toBe("30.00 USD");
    expect(risk().getByText("已达程序损益停机阈值")).toBeTruthy();
  });

  it("shows alert-only as a verified external liability rule and leaves unpublished attribution unknown", async () => {
    render(<LiveExecPage />); await flush();
    expect(screen.getByText("已核实外仓负债：仅告警")).toBeTruthy();
    expect(screen.getByText(/外仓负债当前归属状态：上游未提供/)).toBeTruthy();
    expect(screen.getByText(/无法核实、混合归属或 Bitget 债务仍停开/)).toBeTruthy();
  });
});
