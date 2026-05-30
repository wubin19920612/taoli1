import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { PhonePriceAlertsPanel } from "../src/pages/PhonePriceAlertsPanel";

describe("PhonePriceAlertsPanel", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/phone-alerts/diagnostics")) {
          return Response.json({
            phone_enabled: true,
            items: [
              {
                rule_id: "rule-1",
                rule_name: "ESPORTS reduce",
                symbol: "ESPORTSUSDT",
                exchange: "binance",
                market_type: "future",
                price_field: "mark_price",
                resolved_price_field: null,
                condition: "above",
                target_price: 0.0423,
                market_found: false,
                observed_price: null,
                triggered: false,
                exchange_error: "Client error '451 '",
                reason: "market not found; exchange error: Client error '451 '"
              }
            ]
          });
        }
        if (url.includes("/phone-alerts/rules")) {
          return Response.json([
            {
              id: "rule-1",
              name: "ESPORTS reduce",
              enabled: true,
              symbol: "ESPORTSUSDT",
              exchange: "binance",
              market_type: "future",
              price_field: "mark_price",
              condition: "above",
              target_price: 0.0423,
              cooldown_seconds: 300
            }
          ]);
        }
        return Response.json([]);
      })
    );
  });

  it("shows diagnostics when a phone price alert cannot see market data", async () => {
    render(<PhonePriceAlertsPanel />);

    expect(await screen.findByText("ESPORTSUSDT")).toBeTruthy();
    expect(await screen.findByText("market not found; exchange error: Client error '451 '")).toBeTruthy();
    expect(screen.getByText("未找到行情")).toBeTruthy();
  });
});
