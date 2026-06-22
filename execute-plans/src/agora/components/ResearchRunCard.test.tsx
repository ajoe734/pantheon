import React from "react";
import { cleanup, render, screen, fireEvent } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ResearchRunCard } from "./ResearchRunCard";
import type { WorkshopCard } from "@/lib/bff-v1/agora/workshops";

afterEach(cleanup);

const baseCard: WorkshopCard = {
  spec_version: "1.0",
  card_id: "card-rr-001",
  card_type: "research_progress",
  workshop_id: "ws-001",
  sequence_no: 6,
  status: "running",
  title: "Momentum Backtest Stage 1",
  summary: "Running in-sample prototype backtest via vectorbt.",
  payload: {
    run_id: "run-001",
    plan_id: "plan-001",
    stage_id: "s1",
    stage_type: "prototype_backtest",
    execution_status: "running",
    progress: 42,
    backend: "vectorbt",
    latest_progress_message: "Processing bar 420 / 1000",
    warnings: ["Data gap detected at 2022-03-15"],
    blocking_reasons: [],
    started_at: "2026-06-22T10:00:00Z",
    updated_at: "2026-06-22T10:05:00Z",
  },
  created_at: "2026-06-22T10:00:00Z",
  allowed_actions: { cancel: true },
};

describe("ResearchRunCard", () => {
  it("renders with correct testid", () => {
    render(<ResearchRunCard card={baseCard} />);
    expect(screen.getByTestId("research-run-card-card-rr-001")).toBeDefined();
  });

  it("displays card title and summary", () => {
    render(<ResearchRunCard card={baseCard} />);
    expect(screen.getByText("Momentum Backtest Stage 1")).toBeDefined();
    expect(screen.getByText(/in-sample prototype backtest/)).toBeDefined();
  });

  it("shows card status badge", () => {
    render(<ResearchRunCard card={baseCard} />);
    expect(screen.getByTestId("research-run-card-card-rr-001-card-status").textContent).toBe(
      "running"
    );
  });

  it("shows execution status", () => {
    render(<ResearchRunCard card={baseCard} />);
    expect(screen.getByTestId("research-run-card-card-rr-001-execution-status").textContent).toBe(
      "running"
    );
  });

  it("renders progress bar container", () => {
    render(<ResearchRunCard card={baseCard} />);
    expect(screen.getByTestId("research-run-card-card-rr-001-progress-bar")).toBeDefined();
  });

  it("shows progress percentage", () => {
    render(<ResearchRunCard card={baseCard} />);
    expect(screen.getByTestId("research-run-card-card-rr-001-progress-pct").textContent).toBe(
      "42%"
    );
  });

  it("shows latest progress message", () => {
    render(<ResearchRunCard card={baseCard} />);
    const msgEl = screen.getByTestId("research-run-card-card-rr-001-message");
    expect(msgEl.textContent).toContain("Processing bar 420 / 1000");
  });

  it("shows warnings section when warnings present", () => {
    render(<ResearchRunCard card={baseCard} />);
    const warnEl = screen.getByTestId("research-run-card-card-rr-001-warnings");
    expect(warnEl.textContent).toContain("Data gap detected");
  });

  it("does not show warnings section when empty", () => {
    const card: WorkshopCard = {
      ...baseCard,
      payload: { ...baseCard.payload, warnings: [] },
    };
    render(<ResearchRunCard card={card} />);
    expect(screen.queryByTestId("research-run-card-card-rr-001-warnings")).toBeNull();
  });

  it("does not show blocking section when empty", () => {
    render(<ResearchRunCard card={baseCard} />);
    expect(screen.queryByTestId("research-run-card-card-rr-001-blocking")).toBeNull();
  });

  it("shows blocking reasons when present", () => {
    const card: WorkshopCard = {
      ...baseCard,
      payload: { ...baseCard.payload, blocking_reasons: ["Missing data source authorization"] },
    };
    render(<ResearchRunCard card={card} />);
    const blockingEl = screen.getByTestId("research-run-card-card-rr-001-blocking");
    expect(blockingEl.textContent).toContain("Missing data source authorization");
  });

  it("renders cancel button when allowed_actions.cancel is true", () => {
    render(<ResearchRunCard card={baseCard} />);
    expect(screen.getByTestId("research-run-card-card-rr-001-cancel")).toBeDefined();
  });

  it("does not render cancel button when not allowed", () => {
    const card: WorkshopCard = { ...baseCard, allowed_actions: { cancel: false } };
    render(<ResearchRunCard card={card} />);
    expect(screen.queryByTestId("research-run-card-card-rr-001-cancel")).toBeNull();
  });

  it("renders Ask Servant button when onContinueDiscussion provided", () => {
    render(<ResearchRunCard card={baseCard} onContinueDiscussion={() => undefined} />);
    expect(screen.getByTestId("research-run-card-card-rr-001-discuss")).toBeDefined();
  });

  it("calls onContinueDiscussion with card_id on click", () => {
    const handler = vi.fn();
    render(<ResearchRunCard card={baseCard} onContinueDiscussion={handler} />);
    fireEvent.click(screen.getByTestId("research-run-card-card-rr-001-discuss"));
    expect(handler).toHaveBeenCalledWith("card-rr-001");
  });

  it("does not render Ask Servant button when no callback provided", () => {
    render(<ResearchRunCard card={baseCard} />);
    expect(screen.queryByTestId("research-run-card-card-rr-001-discuss")).toBeNull();
  });

  it("handles succeeded status", () => {
    const card: WorkshopCard = {
      ...baseCard,
      status: "completed",
      payload: { ...baseCard.payload, execution_status: "succeeded", progress: 100 },
    };
    render(<ResearchRunCard card={card} />);
    expect(screen.getByTestId("research-run-card-card-rr-001-execution-status").textContent).toBe(
      "succeeded"
    );
    expect(screen.getByTestId("research-run-card-card-rr-001-progress-pct").textContent).toBe(
      "100%"
    );
  });

  it("clamps progress above 100", () => {
    const card: WorkshopCard = {
      ...baseCard,
      payload: { ...baseCard.payload, progress: 150 },
    };
    render(<ResearchRunCard card={card} />);
    expect(screen.getByTestId("research-run-card-card-rr-001-progress-pct").textContent).toBe(
      "100%"
    );
  });

  it("clamps progress below 0", () => {
    const card: WorkshopCard = {
      ...baseCard,
      payload: { ...baseCard.payload, progress: -5 },
    };
    render(<ResearchRunCard card={card} />);
    expect(screen.getByTestId("research-run-card-card-rr-001-progress-pct").textContent).toBe(
      "0%"
    );
  });

  it("does not show message when latest_progress_message absent", () => {
    const card: WorkshopCard = {
      ...baseCard,
      payload: { ...baseCard.payload, latest_progress_message: undefined },
    };
    render(<ResearchRunCard card={card} />);
    expect(screen.queryByTestId("research-run-card-card-rr-001-message")).toBeNull();
  });
});
