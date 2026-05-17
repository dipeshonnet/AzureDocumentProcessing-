import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ConfidenceBadge } from "./ConfidenceBadge";

describe("ConfidenceBadge", () => {
  it("shows low confidence as an accessible confidence value", () => {
    render(<ConfidenceBadge value={0.42} label="Type confidence" />);

    expect(screen.getByLabelText("Type confidence: 42%")).toBeInTheDocument();
    expect(screen.getByText("42%")).toBeInTheDocument();
  });
});
