import React, { useEffect, useReducer, useCallback, useState } from "react";
import {
  listWorkshops,
  getWorkshop,
  getWorkshopCompleteness,
  getWorkshopReadiness,
  listWorkshopCards,
  postWorkshopMessage,
  openWorkshopStream,
  type WorkshopCard,
  type StrategyReadinessAssessment,
  type WorkshopStreamEvent,
} from "@/lib/bff-v1/agora/workshops";
import type { StrategyWorkshop, StrategyCompleteness } from "@/lib/bff-v1/agora/workshops";
import { WorkshopCardRenderer } from "@/agora/components/WorkshopCardRenderer";
import { StrategyCompletenessRail } from "@/agora/components/StrategyCompletenessRail";

interface CardState {
  cards: WorkshopCard[];
  lastEventId: string | null;
}

type CardAction =
  | { type: "RESET"; cards: WorkshopCard[] }
  | { type: "UPSERT"; card: WorkshopCard }
  | { type: "SET_LAST_EVENT_ID"; id: string };

function cardReducer(state: CardState, action: CardAction): CardState {
  switch (action.type) {
    case "RESET":
      return { ...state, cards: action.cards };
    case "UPSERT": {
      const idx = state.cards.findIndex((c) => c.card_id === action.card.card_id);
      if (idx === -1) return { ...state, cards: [...state.cards, action.card] };
      const updated = [...state.cards];
      updated[idx] = action.card;
      return { ...state, cards: updated };
    }
    case "SET_LAST_EVENT_ID":
      return { ...state, lastEventId: action.id };
    default:
      return state;
  }
}

const COMPOSER_CHIPS = [
  { label: "交代策略", prompt: "我想設計一個策略：" },
  { label: "修改規則", prompt: "把目前策略規則改成：" },
  { label: "要求研究", prompt: "請針對目前策略補做研究：" },
  { label: "質疑結果", prompt: "我質疑目前結論，請重新檢查：" },
  { label: "指定版本", prompt: "請以目前策略版本為基礎：" },
  { label: "要求重跑", prompt: "請重跑這些版本並比較：" },
];

const GRADE_PERCENT: Record<string, number> = {
  complete: 100,
  mostly_complete: 82,
  partial: 56,
  incomplete: 28,
};

function isLongStrategyDescription(card: WorkshopCard): boolean {
  if (card.card_type !== "user_strategy_description") return false;
  const content = String(card.payload.owner_visible_content ?? "");
  return content.length >= 180 || content.includes("\n");
}

function isServantResult(card: WorkshopCard): boolean {
  return card.card_type !== "user_strategy_description";
}

function orderWorkshopCardsForV10(cards: WorkshopCard[]): WorkshopCard[] {
  const ordered = cards.slice().sort((a, b) => a.sequence_no - b.sequence_no);
  const firstLongDescriptionIndex = ordered.findIndex(isLongStrategyDescription);
  if (firstLongDescriptionIndex < 0) return ordered;

  const firstServantIndex = ordered.findIndex((card, index) => index > firstLongDescriptionIndex && isServantResult(card));
  const reconstructionIndex = ordered.findIndex(
    (card, index) => index > firstLongDescriptionIndex && card.card_type === "servant_reconstruction",
  );

  if (firstServantIndex < 0 || reconstructionIndex < 0 || reconstructionIndex <= firstServantIndex) return ordered;

  const [reconstruction] = ordered.splice(reconstructionIndex, 1);
  ordered.splice(firstLongDescriptionIndex + 1, 0, reconstruction);
  return ordered;
}

function metadataString(workshop: StrategyWorkshop | null, key: string): string | null {
  const value = workshop?.metadata?.[key];
  return typeof value === "string" && value.trim() ? value : null;
}

function versionLabel(workshop: StrategyWorkshop | null, cards: WorkshopCard[]): string {
  return (
    metadataString(workshop, "strategy_version") ??
    cards.find((card) => card.workshop_version_id)?.workshop_version_id ??
    "V10"
  );
}

function workshopTitle(workshop: StrategyWorkshop | null): string {
  return metadataString(workshop, "strategy_name") ?? workshop?.subject?.title ?? "策略工坊";
}

function researchState(workshop: StrategyWorkshop | null, readiness: StrategyReadinessAssessment | null): string {
  if (readiness?.highest_ready_gate === "trading_room") return "已達操盤室門檻";
  if (readiness?.highest_ready_gate === "full_validation") return "完成驗證 · 待裁示";
  if (readiness?.highest_ready_gate === "preliminary_research") return "初步研究可行";
  if (!workshop) return "讀取中";
  if (workshop.status === "concluded") return "已結案";
  if (workshop.status === "in_review") return "待審視";
  return "共同建構中";
}

function backtestState(cards: WorkshopCard[]): string {
  const completedResults = cards.filter((card) => card.card_type === "research_result" && card.status === "completed").length;
  if (completedResults > 0) return `${completedResults} 個結果卡`;
  const running = cards.some((card) => card.card_type === "research_progress");
  return running ? "研究執行中" : "等待回測";
}

function completenessPercent(completeness: StrategyCompleteness | null): number {
  if (!completeness) return 0;
  return GRADE_PERCENT[completeness.overall_grade] ?? 0;
}

function tradingRoomReady(readiness: StrategyReadinessAssessment | null): boolean {
  return readiness?.highest_ready_gate === "trading_room" || readiness?.gates.some((gate) => gate.gate === "trading_room" && gate.state === "ready") === true;
}

function tradingRoomDisabledReason(
  readiness: StrategyReadinessAssessment | null,
  hasHandler: boolean,
): string | null {
  if (!readiness) return "尚未完成加入操盤室 readiness 評估";
  if (!tradingRoomReady(readiness)) {
    const highest = readiness.highest_ready_gate ?? "none";
    return `交易操盤室 gate 尚未通過；目前最高門檻：${highest}`;
  }
  if (!hasHandler) return "已達門檻；等待操盤室提案建立流程接入";
  return null;
}

type ListState = "loading" | "empty" | "loaded" | "error";

function WorkshopListView(): JSX.Element {
  const [state, setState] = useState<ListState>("loading");
  const [workshops, setWorkshops] = useState<StrategyWorkshop[]>([]);

  useEffect(() => {
    let cancelled = false;
    listWorkshops()
      .then((items) => {
        if (cancelled) return;
        setWorkshops(items);
        setState(items.length === 0 ? "empty" : "loaded");
      })
      .catch(() => {
        if (!cancelled) setState("error");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div
      data-testid="strategy-workshop-page-list"
      style={{ minHeight: "100%", background: "#11151c", color: "#f3efe7", padding: 24 }}
    >
      {state === "loading" && <div data-testid="workshop-list-loading">Loading workshops...</div>}
      {state === "empty" && <div data-testid="workshop-list-empty">No workshops found.</div>}
      {state === "error" && <div data-testid="workshop-list-error">Unable to load workshops.</div>}
      {state === "loaded" && (
        <ul data-testid="workshop-list" style={{ display: "flex", flexDirection: "column", gap: 10, padding: 0 }}>
          {workshops.map((ws) => (
            <li
              key={ws.workshop_id}
              data-testid={`workshop-item-${ws.workshop_id}`}
              style={{ listStyle: "none", background: "#171b22", border: "1px solid #2a303b", borderRadius: 8, padding: 12 }}
            >
              {ws.subject?.title ?? ws.workshop_id}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

interface SessionViewProps {
  workshopId: string;
  onAddToTradingRoom?: () => void;
}

function WorkshopSessionView({ workshopId, onAddToTradingRoom }: SessionViewProps): JSX.Element {
  const [workshop, setWorkshop] = useState<StrategyWorkshop | null>(null);
  const [completeness, setCompleteness] = useState<StrategyCompleteness | null>(null);
  const [readiness, setReadiness] = useState<StrategyReadinessAssessment | null>(null);
  const [composerValue, setComposerValue] = useState("");
  const [submitState, setSubmitState] = useState<"idle" | "submitting" | "error">("idle");
  const [submitError, setSubmitError] = useState<string | null>(null);

  const [cardState, dispatch] = useReducer(cardReducer, {
    cards: [],
    lastEventId: null,
  });

  useEffect(() => {
    let cancelled = false;
    getWorkshop(workshopId)
      .then((ws) => {
        if (!cancelled) setWorkshop(ws);
      })
      .catch(() => undefined);
    getWorkshopCompleteness(workshopId)
      .then((c) => {
        if (!cancelled && c) setCompleteness(c);
      })
      .catch(() => undefined);
    getWorkshopReadiness(workshopId)
      .then((r) => {
        if (!cancelled && r) setReadiness(r);
      })
      .catch(() => undefined);
    listWorkshopCards(workshopId)
      .then((items) => {
        if (!cancelled) dispatch({ type: "RESET", cards: items });
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [workshopId]);

  const refreshCompleteness = useCallback(() => {
    getWorkshopCompleteness(workshopId)
      .then((c) => {
        if (c) setCompleteness(c);
      })
      .catch(() => undefined);
  }, [workshopId]);

  const refreshReadiness = useCallback(() => {
    getWorkshopReadiness(workshopId)
      .then((r) => {
        if (r) setReadiness(r);
      })
      .catch(() => undefined);
  }, [workshopId]);

  const refreshCards = useCallback(() => {
    listWorkshopCards(workshopId)
      .then((items) => dispatch({ type: "RESET", cards: items }))
      .catch(() => undefined);
  }, [workshopId]);

  useEffect(() => {
    const teardown = openWorkshopStream(workshopId, (event: WorkshopStreamEvent) => {
      dispatch({ type: "SET_LAST_EVENT_ID", id: event.event_id });
      switch (event.event_type) {
        case "workshop.message.accepted":
        case "workshop.next_question.updated":
        case "workshop.servant.response.completed":
        case "research.plan.created":
        case "research.plan.approved":
        case "research.run.queued":
        case "research.run.progress":
        case "research.run.completed":
        case "research.run.failed":
        case "consultation.started":
        case "consultation.completed":
        case "workshop.patch.proposed":
        case "workshop.patch.validated":
        case "workshop.version.created":
        case "workshop.version.selected":
          refreshCards();
          break;
        case "workshop.completeness.updated":
          refreshCompleteness();
          break;
        case "workshop.readiness.updated":
          refreshReadiness();
          break;
        case "workshop.snapshot":
          refreshCards();
          refreshCompleteness();
          refreshReadiness();
          break;
        default:
          break;
      }
    });
    return teardown;
  }, [workshopId, refreshCards, refreshCompleteness, refreshReadiness]);

  const orderedCards = orderWorkshopCardsForV10(cardState.cards);
  const nextQuestion =
    orderedCards
      .filter((c) => c.card_type === "next_question")
      .sort((a, b) => b.sequence_no - a.sequence_no)[0] ?? null;

  const handleContinueDiscussion = useCallback((cardId: string) => {
    setComposerValue((prev) => (prev ? prev : `針對卡片 ${cardId}，請交易僕人補充：`));
  }, []);

  const handleSubmit = useCallback(async () => {
    const content = composerValue.trim();
    if (!content || submitState === "submitting") return;
    setSubmitState("submitting");
    setSubmitError(null);
    try {
      await postWorkshopMessage(workshopId, { content });
      setComposerValue("");
      refreshCards();
      refreshCompleteness();
      refreshReadiness();
      setSubmitState("idle");
    } catch (error) {
      setSubmitState("error");
      setSubmitError(error instanceof Error ? error.message : "訊息送出失敗");
    }
  }, [composerValue, refreshCards, refreshCompleteness, refreshReadiness, submitState, workshopId]);

  const readinessReason = tradingRoomDisabledReason(readiness, Boolean(onAddToTradingRoom));
  const addToTradingRoomActive = !readinessReason && Boolean(onAddToTradingRoom);
  const pct = completenessPercent(completeness);

  return (
    <div
      data-testid="strategy-workshop-page-session"
      style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden", background: "#11151c", color: "#f3efe7" }}
    >
      <header
        data-testid="strategy-workshop-runtime-header"
        style={{
          display: "flex",
          alignItems: "center",
          gap: 12,
          padding: "11px 22px",
          borderBottom: "1px solid #2a303b",
          background: "#171b22",
          flexWrap: "wrap",
        }}
      >
        <div style={{ fontSize: 14, fontWeight: 800 }}>{workshopTitle(workshop)}</div>
        <span
          style={{
            color: "#e8b750",
            background: "rgba(232,183,80,0.12)",
            border: "1px solid rgba(232,183,80,0.35)",
            borderRadius: 6,
            fontFamily: "IBM Plex Mono, monospace",
            fontSize: 11,
            padding: "2px 9px",
          }}
        >
          {versionLabel(workshop, orderedCards)}
        </span>
        <span style={{ color: "#aeb7c4", fontSize: 11 }}>研究狀態 · {researchState(workshop, readiness)}</span>
        <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
          <span style={{ color: "#737d8e", fontSize: 11 }}>完整度</span>
          <div aria-hidden="true" style={{ width: 86, height: 6, background: "#2a303b", borderRadius: 3, overflow: "hidden" }}>
            <div style={{ width: `${pct}%`, height: "100%", background: "#e8b750" }} />
          </div>
          <span style={{ color: "#aeb7c4", fontFamily: "IBM Plex Mono, monospace", fontSize: 11 }}>{pct}%</span>
        </div>
        <span style={{ color: "#aeb7c4", fontSize: 11 }}>回測 · {backtestState(orderedCards)}</span>
        {cardState.lastEventId && (
          <span style={{ color: "#737d8e", fontSize: 10.5, fontFamily: "IBM Plex Mono, monospace" }}>
            event {cardState.lastEventId}
          </span>
        )}
        <button
          data-testid="add-to-trading-room-btn"
          disabled={!addToTradingRoomActive}
          aria-disabled={!addToTradingRoomActive}
          title={readinessReason ?? undefined}
          onClick={addToTradingRoomActive ? onAddToTradingRoom : undefined}
          style={{
            marginLeft: "auto",
            border: "none",
            borderRadius: 9,
            padding: "8px 15px",
            fontSize: 12,
            fontWeight: 800,
            cursor: addToTradingRoomActive ? "pointer" : "not-allowed",
            background: addToTradingRoomActive ? "#e8b750" : "#2a303b",
            color: addToTradingRoomActive ? "#1a1410" : "#737d8e",
          }}
        >
          加入交易操盤室
        </button>
        {readinessReason && (
          <div data-testid="add-to-trading-room-reason" style={{ width: "100%", textAlign: "right", color: "#8c96a6", fontSize: 10.5 }}>
            {readinessReason}
          </div>
        )}
      </header>

      <div style={{ flex: 1, display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(300px, 28vw)", minHeight: 0 }}>
        <main style={{ display: "flex", flexDirection: "column", minHeight: 0, borderRight: "1px solid #2a303b" }}>
          <div
            data-testid="workshop-conversation"
            style={{
              flex: 1,
              overflow: "auto",
              padding: "20px 24px",
              display: "flex",
              flexDirection: "column",
              gap: 16,
              background: "#11151c",
            }}
          >
            {!workshop && orderedCards.length === 0 && <div data-testid="workshop-session-loading">Loading...</div>}
            {workshop && orderedCards.length === 0 && (
              <div data-testid="workshop-cards-empty" style={{ color: "#8c96a6", fontSize: 12 }}>
                等待交易員描述或 stream event 產生策略卡。
              </div>
            )}
            {orderedCards.map((card) => (
              <WorkshopCardRenderer
                key={card.card_id}
                card={card}
                onContinueDiscussion={handleContinueDiscussion}
              />
            ))}
          </div>

          <div data-testid="servant-composer" style={{ borderTop: "1px solid #2a303b", padding: "13px 18px", background: "#171b22" }}>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 7, marginBottom: 10 }}>
              {COMPOSER_CHIPS.map((chip) => (
                <button
                  key={chip.label}
                  type="button"
                  data-testid={`servant-composer-chip-${chip.label}`}
                  onClick={() => setComposerValue(chip.prompt)}
                  style={{
                    border: "1px solid #303846",
                    background: "#202631",
                    color: "#aeb7c4",
                    borderRadius: 999,
                    padding: "5px 11px",
                    fontSize: 11,
                    cursor: "pointer",
                  }}
                >
                  {chip.label}
                </button>
              ))}
            </div>
            <div style={{ display: "flex", alignItems: "flex-end", gap: 9, background: "#202631", border: "1px solid #303846", borderRadius: 12, padding: "10px 10px 10px 14px" }}>
              <textarea
                data-testid="servant-composer-input"
                value={composerValue}
                onChange={(e) => setComposerValue(e.target.value)}
                placeholder="交代策略、修改規則、要求研究、質疑結果、指定版本或要求重跑……"
                style={{
                  flex: 1,
                  minWidth: 0,
                  resize: "none",
                  height: 42,
                  background: "transparent",
                  border: "none",
                  outline: "none",
                  color: "#f3efe7",
                  fontFamily: "Noto Sans TC, sans-serif",
                  fontSize: 12.5,
                  lineHeight: 1.5,
                }}
              />
              <button
                type="button"
                data-testid="servant-composer-submit"
                disabled={!composerValue.trim() || submitState === "submitting"}
                onClick={handleSubmit}
                style={{
                  border: "none",
                  borderRadius: 9,
                  padding: "9px 16px",
                  fontSize: 12,
                  fontWeight: 800,
                  background: composerValue.trim() && submitState !== "submitting" ? "#e8b750" : "#303846",
                  color: composerValue.trim() && submitState !== "submitting" ? "#1a1410" : "#737d8e",
                  cursor: composerValue.trim() && submitState !== "submitting" ? "pointer" : "not-allowed",
                }}
              >
                {submitState === "submitting" ? "送出中" : "交代"}
              </button>
            </div>
            {submitState === "error" && submitError && (
              <div data-testid="servant-composer-error" style={{ color: "#ef8585", fontSize: 11, marginTop: 6 }}>
                {submitError}
              </div>
            )}
          </div>
        </main>

        <aside data-testid="completeness-rail" style={{ minHeight: 0, overflow: "hidden", display: "flex", flexDirection: "column" }}>
          <StrategyCompletenessRail
            completeness={completeness}
            readiness={readiness}
            nextQuestion={nextQuestion}
          />
          {completeness && (
            <div data-testid="completeness-grade" style={{ display: "none" }}>
              {completeness.overall_grade}
            </div>
          )}
          {readiness && (
            <div data-testid="workshop-readiness" style={{ display: "none" }}>
              {readiness.highest_ready_gate ?? "Not ready"}
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}

interface StrategyWorkshopPageProps {
  workshopId?: string;
  onAddToTradingRoom?: () => void;
}

export function StrategyWorkshopPage({ workshopId, onAddToTradingRoom }: StrategyWorkshopPageProps): JSX.Element {
  if (workshopId) {
    return <WorkshopSessionView workshopId={workshopId} onAddToTradingRoom={onAddToTradingRoom} />;
  }
  return <WorkshopListView />;
}
