import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getHealth, listOpportunities } from "../src/api/client";
import type { Opportunity } from "../src/api/types";
import { useRadarStore } from "../src/state/useRadarStore";

vi.mock("../src/api/client", () => ({ getHealth: vi.fn(), listOpportunities: vi.fn() }));

describe("useRadarStore exchange switching", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getHealth).mockResolvedValue({ status: "ok" } as Awaited<ReturnType<typeof getHealth>>);
  });

  it("starts the new filter immediately and discards the older response", async () => {
    let resolveOld!: (rows: Opportunity[]) => void;
    vi.mocked(listOpportunities)
      .mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; }))
      .mockResolvedValueOnce([{ id: "new" } as Opportunity]);
    const { result, rerender } = renderHook(
      ({ exchange }) => useRadarStore({ exchange }, true, { autoRefresh: false }),
      { initialProps: { exchange: "binance" } }
    );
    rerender({ exchange: "okx" });
    await waitFor(() => expect(result.current.opportunities[0]?.id).toBe("new"));
    await act(async () => resolveOld([{ id: "old" } as Opportunity]));
    expect(result.current.opportunities[0]?.id).toBe("new");
    expect(listOpportunities).toHaveBeenCalledTimes(2);
  });

  it("clears previous exchange rows when the next request fails", async () => {
    vi.mocked(listOpportunities)
      .mockResolvedValueOnce([{ id: "old" } as Opportunity])
      .mockRejectedValueOnce(new Error("request failed"));
    const { result, rerender } = renderHook(
      ({ exchange }) => useRadarStore({ exchange }, true, { autoRefresh: false }),
      { initialProps: { exchange: "binance" } }
    );
    await waitFor(() => expect(result.current.opportunities).toHaveLength(1));
    rerender({ exchange: "okx" });
    await waitFor(() => expect(result.current.error).toBe("request failed"));
    expect(result.current.opportunities).toEqual([]);
  });
});
