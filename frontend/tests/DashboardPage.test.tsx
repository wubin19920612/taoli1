import { render, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DashboardPage } from "../src/pages/DashboardPage";

describe("DashboardPage", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/health")) {
          return Response.json({
            status: "ok",
            markets: 0,
            opportunities: 0,
            exchange_errors: {}
          });
        }
        if (url.includes("/opportunities")) {
          return Response.json([]);
        }
        return Response.json({});
      })
    );
  });

  it("hides non-actionable risk rows by default", async () => {
    render(<DashboardPage />);

    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining("include_risky=false"),
        expect.anything()
      );
    });
  });

  it("sends adjustable hidden risk labels and min volume in K", async () => {
    render(<DashboardPage />);

    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining("hidden_risk_labels=LOW_VOLUME%2CSTALE_DATA%2CHUGE_SPREAD_VERIFY"),
        expect.anything()
      );
      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining("min_volume_24h_k=1000"),
        expect.anything()
      );
    });
  });
});
