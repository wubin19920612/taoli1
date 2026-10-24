import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MarketIdentityPanel } from "../src/components/MarketIdentityPanel";

const overview = {
  items: [{ market: { exchange: "hyperliquid", market_type: "future", dex: "io", raw_symbol: "io:PRL" }, asset_id: "crypto.pearl-research", display_name: "Pearl", identity_status: "verified", unit_status: "verified", quote_currency: "USDC", settlement_currency: "USDC", price_multiplier: 1, contract_size: 1, revision: 2, valid_until: "2026-11-01T00:00:00Z", evidence: [{ url: "https://api.hyperliquid.xyz/info", summary: "官方 PEARL 标注" }] }],
  total: 1, coverage: { markets: 10, verified: 1, pending: 9, reference_quotes: 1 }, settings: { mode: "shadow" }
};

describe("MarketIdentityPanel", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("binding-preview")) return Response.json({ affected_opportunities: 3 });
      if (init?.method === "PUT") return Response.json(JSON.parse(String(init.body)));
      return Response.json(overview);
    }));
  });

  it("shows the exact DEX and evidence and preserves optimistic revision when saving", async () => {
    const user = userEvent.setup();
    render(<MarketIdentityPanel />);
    expect(await screen.findByText("io:PRL · io")).toBeTruthy();
    expect(screen.getByText("官方来源").getAttribute("href")).toBe("https://api.hyperliquid.xyz/info");
    await user.click(screen.getByRole("button", { name: "核验市场" }));
    expect(await screen.findByText(/影响当前 3 条机会/)).toBeTruthy();
    await user.type(screen.getByLabelText("本次修改原因"), "复核官方标注，保持独立资产");
    await user.click(screen.getByRole("button", { name: "保存核验" }));
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([url, init]) => String(url).endsWith("/market-identities/binding") && init?.method === "PUT")).toBe(true));
    const write = vi.mocked(fetch).mock.calls.find(([url, init]) => String(url).endsWith("/market-identities/binding") && init?.method === "PUT");
    const payload = JSON.parse(String(write?.[1]?.body));
    expect(payload.expected_revision).toBe(2);
    expect(payload.binding.market).toEqual(overview.items[0].market);
    expect(payload.binding.asset_id).toBe("crypto.pearl-research");
  });
});
