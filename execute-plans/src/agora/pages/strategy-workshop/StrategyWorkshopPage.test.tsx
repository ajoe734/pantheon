import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/bff-v1/agora/workshops", () => ({
  listWorkshops: vi.fn().mockResolvedValue([]),
  getWorkshop: vi.fn().mockResolvedValue(null),
  getWorkshopCompleteness: vi.fn().mockResolvedValue(null),
  getWorkshopReadiness: vi.fn().mockResolvedValue(null),
  listWorkshopCards: vi.fn().mockResolvedValue([]),
  postWorkshopMessage: vi.fn().mockResolvedValue({ event_id: "ev-post-001" }),
  openWorkshopStream: vi.fn().mockReturnValue(() => undefined),
}));

import { StrategyWorkshopPage } from "./StrategyWorkshopPage";
import * as workshopsModule from "@/lib/bff-v1/agora/workshops";
import type { WorkshopCard, WorkshopStreamEvent } from "@/lib/bff-v1/agora/workshops";

afterEach(cleanup);

const baseWorkshop = {
  spec_version: "1.0",
  workshop_id: "ws-abc",
  operator_id: "op-001",
  status: "open",
  subject: {
    kind: "free_form",
    ref: "winner-branch",
    title: "贏家分點策略族",
  },
  created_at: "2026-06-01T00:00:00Z",
  metadata: { strategy_version: "V4" },
} as any;

const readyAssessment = {
  spec_version: "1.0",
  assessment_id: "ready_001",
  workshop_id: "ws-abc",
  strategy_id: "strat-001",
  workshop_version_id: "v-001",
  strategy_spec_registry_id: "reg-001",
  assessment_version: 1,
  gates: [
    { gate: "preliminary_research", state: "ready", requirements: [], blocking_requirement_ids: [] },
    { gate: "full_validation", state: "ready", requirements: [], blocking_requirement_ids: [] },
    { gate: "trading_room", state: "ready", requirements: [], blocking_requirement_ids: [] },
  ],
  highest_ready_gate: "trading_room",
  assessed_at: "2026-06-22T00:00:00Z",
} as any;

const notReadyAssessment = {
  ...readyAssessment,
  gates: [
    { gate: "preliminary_research", state: "conditional", requirements: [], blocking_requirement_ids: [] },
    { gate: "full_validation", state: "not_assessed", requirements: [], blocking_requirement_ids: [] },
    { gate: "trading_room", state: "not_assessed", requirements: [], blocking_requirement_ids: [] },
  ],
  highest_ready_gate: null,
} as any;

const completeness = {
  spec_version: "1.0",
  completeness_id: "comp-001",
  strategy_ref: "strat-001",
  assessed_by_persona_id: "persona-a",
  overall_grade: "mostly_complete",
  dimensions: [
    { dimension: "hypothesis", grade: "complete", gaps: [], required_actions: [] },
    { dimension: "data_dependencies", grade: "partial", gaps: [], required_actions: [] },
    { dimension: "market_scope", grade: "complete", gaps: [], required_actions: [] },
    { dimension: "evaluation_plan", grade: "complete", gaps: [], required_actions: [] },
    { dimension: "risk_constraints", grade: "partial", gaps: [], required_actions: [] },
    { dimension: "execution_profile", grade: "missing", gaps: [], required_actions: [] },
    { dimension: "governance", grade: "partial", gaps: [], required_actions: [] },
  ],
  blockers: [],
  research_ready: true,
  assessed_at: "2026-06-22T00:00:00Z",
} as any;

const longDescriptionCard: WorkshopCard = {
  spec_version: "1.0",
  card_id: "card-user-001",
  card_type: "user_strategy_description",
  workshop_id: "ws-abc",
  sequence_no: 1,
  status: "completed",
  title: "原始假說",
  payload: {
    owner_visible_content:
      "從每一檔股票的關係人持股開始，找出可能對應的交易分點，計算這些分點過去進出場是否賺錢、穩定性如何，建立贏家分點分數。\n還要掃描分點遷移、事件領先、部位與槓桿，並建立多個可比較策略。",
    message_event_id: "msg-001",
    created_at: "2026-06-22T00:00:00Z",
  },
  created_at: "2026-06-22T00:00:00Z",
};

const nextQuestionCard: WorkshopCard = {
  spec_version: "1.0",
  card_id: "card-next-001",
  card_type: "next_question",
  workshop_id: "ws-abc",
  sequence_no: 2,
  status: "action_required",
  title: "Next Question",
  payload: {
    question_id: "q-001",
    question: "你希望 Winner Branch Score 偏向哪種主要目標？",
    why_now: "This changes the research design.",
    score_total: 0.91,
  },
  created_at: "2026-06-22T00:00:00Z",
};

const reconstructionCard: WorkshopCard = {
  spec_version: "1.0",
  card_id: "card-recon-001",
  card_type: "servant_reconstruction",
  workshop_id: "ws-abc",
  sequence_no: 3,
  status: "completed",
  title: "Strategy Reconstruction",
  payload: {
    strategy_title: "贏家分點策略族",
    explicit_definitions: ["識別具有持續獲利能力或資訊領先特徵的券商分點群組。"],
    causal_chain: [
      {
        step_id: "s1",
        premise: "關係人持股與分點淨買賣",
        mechanism: "估計可能對應關係",
        expected_observation: "關係人與分點映射的概率證據",
        confidence: 0.74,
      },
    ],
    servant_inferences: [],
    uncertainties: [],
    contradictions: [],
    proposed_next_actions: ["裁示主要 Score 目標。"],
  },
  created_at: "2026-06-22T00:00:00Z",
};

function streamEvent(type: WorkshopStreamEvent["event_type"]): WorkshopStreamEvent {
  return {
    spec_version: "1.0",
    event_id: `ev-${type}`,
    event_type: type,
    aggregate_type: "strategy_workshop",
    aggregate_id: "ws-abc",
    sequence_no: 1,
    event_time: "2026-06-22T00:00:00Z",
    emitted_at: "2026-06-22T00:00:00Z",
    trace_id: "trace-001",
    idempotency_key: "idem-001",
    payload: {},
  };
}

describe("StrategyWorkshopPage", () => {
  beforeEach(() => {
    vi.mocked(workshopsModule.listWorkshops).mockResolvedValue([]);
    vi.mocked(workshopsModule.getWorkshop).mockResolvedValue(baseWorkshop);
    vi.mocked(workshopsModule.getWorkshopCompleteness).mockResolvedValue(null as any);
    vi.mocked(workshopsModule.getWorkshopReadiness).mockResolvedValue(null as any);
    vi.mocked(workshopsModule.listWorkshopCards).mockResolvedValue([]);
    vi.mocked(workshopsModule.postWorkshopMessage).mockResolvedValue({ event_id: "ev-post-001" });
    vi.mocked(workshopsModule.openWorkshopStream).mockReturnValue(() => undefined);
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders the list view when no workshopId is provided", () => {
    render(<StrategyWorkshopPage />);
    expect(screen.getByTestId("strategy-workshop-page-list")).toBeDefined();
  });

  it("shows loading state while fetching workshops", () => {
    vi.mocked(workshopsModule.listWorkshops).mockReturnValue(new Promise(() => {}));
    render(<StrategyWorkshopPage />);
    expect(screen.getByTestId("workshop-list-loading")).toBeDefined();
  });

  it("shows empty state after workshops resolve to empty", async () => {
    vi.mocked(workshopsModule.listWorkshops).mockResolvedValue([]);
    render(<StrategyWorkshopPage />);
    await screen.findByTestId("workshop-list-empty");
  });

  it("shows workshop list items when workshops are returned", async () => {
    vi.mocked(workshopsModule.listWorkshops).mockResolvedValue([
      {
        workshop_id: "ws-001",
        status: "open",
        created_at: "2026-06-01T00:00:00Z",
        updated_at: "2026-06-01T00:00:00Z",
        lock_version: 1,
        subject: { kind: "free_form", ref: "s", title: "Strategy" },
      } as any,
    ]);
    render(<StrategyWorkshopPage />);
    await screen.findByTestId("workshop-list");
    expect(screen.getByTestId("workshop-item-ws-001")).toBeDefined();
  });

  it("renders the session view when workshopId is provided", () => {
    vi.mocked(workshopsModule.getWorkshop).mockReturnValue(new Promise(() => {}));
    render(<StrategyWorkshopPage workshopId="ws-abc" />);
    expect(screen.getByTestId("strategy-workshop-page-session")).toBeDefined();
  });

  it("renders the conversation and completeness rail in session view", () => {
    render(<StrategyWorkshopPage workshopId="ws-abc" />);
    expect(screen.getByTestId("workshop-conversation")).toBeDefined();
    expect(screen.getByTestId("completeness-rail")).toBeDefined();
  });

  it("renders the V10 composer chips and posts via the workshop message route helper", async () => {
    render(<StrategyWorkshopPage workshopId="ws-abc" />);

    fireEvent.click(screen.getByTestId("servant-composer-chip-要求研究"));
    expect((screen.getByTestId("servant-composer-input") as HTMLTextAreaElement).value).toContain("補做研究");

    fireEvent.click(screen.getByTestId("servant-composer-submit"));

    await waitFor(() => {
      expect(workshopsModule.postWorkshopMessage).toHaveBeenCalledWith("ws-abc", {
        content: "請針對目前策略補做研究：",
      });
    });
  });

  it("calls listWorkshops through the BFF module (not direct fetch)", () => {
    render(<StrategyWorkshopPage />);
    expect(workshopsModule.listWorkshops).toHaveBeenCalled();
  });

  it("renders Strategy Reconstruction before next question for the first long description", async () => {
    vi.mocked(workshopsModule.listWorkshopCards).mockResolvedValue([
      longDescriptionCard,
      nextQuestionCard,
      reconstructionCard,
    ]);

    render(<StrategyWorkshopPage workshopId="ws-abc" />);

    const conversation = await screen.findByTestId("workshop-conversation");
    const text = conversation.textContent ?? "";
    expect(text.indexOf("策略重構卡")).toBeGreaterThan(-1);
    expect(text.indexOf("策略重構卡")).toBeLessThan(text.indexOf("你希望 Winner Branch Score"));
  });

  it("refreshes cards from stream-driven workshop events", async () => {
    let handler: ((event: WorkshopStreamEvent) => void) | undefined;
    vi.mocked(workshopsModule.openWorkshopStream).mockImplementation((_workshopId, onEvent) => {
      handler = onEvent;
      return () => undefined;
    });
    vi.mocked(workshopsModule.listWorkshopCards)
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([longDescriptionCard]);

    render(<StrategyWorkshopPage workshopId="ws-abc" />);

    await waitFor(() => expect(workshopsModule.listWorkshopCards).toHaveBeenCalledTimes(1));
    act(() => {
      handler?.(streamEvent("workshop.message.accepted"));
    });

    await waitFor(() => expect(workshopsModule.listWorkshopCards).toHaveBeenCalledTimes(2));
  });

  it("enables the Trading Room join action only when readiness is ready and a handler is provided", async () => {
    const onAddToTradingRoom = vi.fn();
    vi.mocked(workshopsModule.getWorkshopReadiness).mockResolvedValue(readyAssessment);
    vi.mocked(workshopsModule.getWorkshopCompleteness).mockResolvedValue(completeness);

    render(<StrategyWorkshopPage workshopId="ws-abc" onAddToTradingRoom={onAddToTradingRoom} />);

    await screen.findByTestId("workshop-readiness");
    const button = screen.getByTestId("add-to-trading-room-btn") as HTMLButtonElement;
    expect(button.disabled).toBe(false);

    fireEvent.click(button);
    expect(onAddToTradingRoom).toHaveBeenCalledTimes(1);
  });

  it("keeps the Trading Room join action disabled when the gate is not ready", async () => {
    vi.mocked(workshopsModule.getWorkshopReadiness).mockResolvedValue(notReadyAssessment);

    render(<StrategyWorkshopPage workshopId="ws-abc" onAddToTradingRoom={() => undefined} />);

    await screen.findByTestId("workshop-readiness");
    const button = screen.getByTestId("add-to-trading-room-btn") as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(screen.getByTestId("add-to-trading-room-reason").textContent).toContain("gate 尚未通過");
  });
});
