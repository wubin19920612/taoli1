import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { QuoteAgeTag } from "../src/components/QuoteAgeTag";

describe("QuoteAgeTag", () => {
  it.each([
    [0, "0.0s", "green"],
    [1200, "1.2s", "green"],
    [4999, "5.0s", "green"],
    [5000, "5.0s", "yellow"],
    [30000, "30.0s", "yellow"],
    [30001, "30.0s", "red"]
  ])("colors %s ms using the unrounded age", (ageMs, label, color) => {
    render(<QuoteAgeTag ageMs={ageMs} />);
    expect(screen.getByText(label).classList.contains(`ant-tag-${color}`)).toBe(true);
  });

  it.each([undefined, null, NaN, Infinity])("hides missing or invalid age %s", (ageMs) => {
    const { container } = render(<QuoteAgeTag ageMs={ageMs} />);
    expect(container.textContent).toBe("");
  });

  it("shows the signed source age and time source on hover", async () => {
    render(<QuoteAgeTag ageMs={1200} sourceAgeMs={-250} timeSource="upstream" />);
    await userEvent.hover(screen.getByText("1.2s"));
    expect((await screen.findByRole("tooltip")).textContent).toBe("来源年龄：-250ms；时间来源：upstream");
  });

  it("labels unavailable source metadata without inventing values", async () => {
    render(<QuoteAgeTag ageMs={0} sourceAgeMs={null} timeSource={null} />);
    await userEvent.hover(screen.getByText("0.0s"));
    expect((await screen.findByRole("tooltip")).textContent).toBe("来源年龄：未知；时间来源：未知");
  });
});
