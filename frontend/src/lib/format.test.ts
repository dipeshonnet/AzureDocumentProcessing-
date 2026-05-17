import { describe, expect, it } from "vitest";
import { applicantName, confidenceTone, formatPercent, formatScore, humanize } from "./format";

describe("format helpers", () => {
  it("formats score and confidence values", () => {
    expect(formatScore(3.25, 5)).toBe("3.25 / 5");
    expect(formatPercent(0.82)).toBe("82%");
    expect(confidenceTone(0.4)).toBe("low");
    expect(confidenceTone(0.72)).toBe("medium");
    expect(confidenceTone(0.93)).toBe("high");
  });

  it("formats reviewer labels", () => {
    expect(applicantName("Ada", "Lovelace")).toBe("Ada Lovelace");
    expect(humanize("needs_more_information")).toBe("Needs More Information");
  });
});
