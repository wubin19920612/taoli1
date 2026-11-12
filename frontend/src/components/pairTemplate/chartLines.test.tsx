import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PairSpreadChart } from "../../pages/PairMonitorPage";
import { chartFixture } from "../pairChartV2/chartTestUtils";

describe("original chart template lines", () => {
  it("shows dashed opening/closing lines and replaces them when the selection changes", () => {
    const result = chartFixture();
    const { rerender } = render(<PairSpreadChart result={result} templateLines={{ open: 1.1, close: 0.3 }} />);
    expect(screen.getByTestId("template-line-open").querySelector("line")?.getAttribute("stroke-dasharray")).toBe("6 4");
    expect(screen.getByTestId("template-line-open").textContent).toContain("模板开仓线 +1.10%");
    rerender(<PairSpreadChart result={result} templateLines={{ open: -1.1, close: -0.3 }} />);
    expect(screen.getByTestId("template-line-open").textContent).toContain("-1.10%");
    rerender(<PairSpreadChart result={result} templateLines={null} />);
    expect(screen.queryByTestId("template-line-open")).toBeNull();
  });
});
