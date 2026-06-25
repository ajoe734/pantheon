import React, { useEffect, useReducer, useRef, useCallback, useState } from "react";
import {
  listWorkshops,
  getWorkshop,
  getWorkshopCompleteness,
  getWorkshopReadiness,
  listWorkshopCards,
  openWorkshopStream,
  type WorkshopCard,
  type StrategyReadinessAssessment,
  type WorkshopStreamEvent,
} from "@/lib/bff-v1/agora/workshops";
import type { StrategyWorkshop, StrategyCompleteness } from "@/lib/bff-v1/agora/workshops";
import { WorkshopCardRenderer } from "@/agora/components/WorkshopCardRenderer";
import { StrategyCompletenessRail } from "@/agora/components/StrategyCompletenessRail";

// ---------------------------------------------------------------------------
// Card list reducer
// ---------------------------------------------------------------------------

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
      if (idx === -1) {
        return { ...state, cards: [...state.cards, action.card] };
      }
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

// ---------------------------------------------------------------------------
// Workshop list view
// ---------------------------------------------------------------------------

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
    <div data-testid="strategy-workshop-page-list">
      {state === "loading" && (
        <div data-testid="workshop-list-loading">Loading workshops…</div>
      )}
      {state === "empty" && (
        <div data-testid="workshop-list-empty">No workshops found.</div>
      )}
      {state === "loaded" && (
        <ul data-testid="workshop-list">
          {workshops.map((ws) => (
            <li key={ws.workshop_id} data-testid={`workshop-item-${ws.workshop_id}`}>
              {ws.workshop_id}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Workshop session view
// ---------------------------------------------------------------------------

interface SessionViewProps {
  workshopId: string;
  onAddToTradingRoom?: () => void;
}

function WorkshopSessionView({ workshopId, onAddToTradingRoom }: SessionViewProps): JSX.Element {
  const [workshop, setWorkshop] = useState<StrategyWorkshop | null>(null);
  const [completeness, setCompleteness] = useState<StrategyCompleteness | null>(null);
  const [readiness, setReadiness] = useState<StrategyReadinessAssessment | null>(null);
  const [composerValue, setComposerValue] = useState("");

  const [cardState, dispatch] = useReducer(cardReducer, {
    cards: [],
    lastEventId: null,
  });

  // Initial data load
  useEffect(() => {
    let cancelled = false;
    getWorkshop(workshopId)
      .then((ws) => { if (!cancelled) setWorkshop(ws); })
      .catch(() => undefined);
    getWorkshopCompleteness(workshopId)
      .then((c) => { if (!cancelled && c) setCompleteness(c); })
      .catch(() => undefined);
    getWorkshopReadiness(workshopId)
      .then((r) => { if (!cancelled && r) setReadiness(r); })
      .catch(() => undefined);
    listWorkshopCards(workshopId)
      .then((items) => { if (!cancelled) dispatch({ type: "RESET", cards: items }); })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [workshopId]);

  // SSE stream subscription — refreshes completeness/readiness on relevant events
  const refreshCompleteness = useCallback(() => {
    getWorkshopCompleteness(workshopId)
      .then((c) => { if (c) setCompleteness(c); })
      .catch(() => undefined);
  }, [workshopId]);

  const refreshReadiness = useCallback(() => {
    getWorkshopReadiness(workshopId)
      .then((r) => { if (r) setReadiness(r); })
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
        case "workshop.completeness.updated":
          refreshCompleteness();
          break;
        case "workshop.readiness.updated":
          refreshReadiness();
          break;
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
          refreshCards();
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

  // Derive the most recent next_question card for the rail
  const nextQuestion =
    cardState.cards
      .filter((c) => c.card_type === "next_question")
      .sort((a, b) => b.sequence_no - a.sequence_no)[0] ?? null;

  const handleContinueDiscussion = useCallback((cardId: string) => {
    setComposerValue((prev) => (prev ? prev : `Re: card ${cardId} — `));
  }, []);

  return (
    <div
      data-testid="strategy-workshop-page-session"
      style={{ display: "flex", height: "100%", overflow: "hidden" }}
    >
      {/* Left: conversation + composer */}
      <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
        <div
          data-testid="workshop-conversation"
          style={{ flex: 1, overflow: "auto", padding: 16, display: "flex", flexDirection: "column", gap: 10 }}
        >
          {!workshop && (
            <div data-testid="workshop-session-loading">Loading…</div>
          )}
          {cardState.cards
            .slice()
            .sort((a, b) => a.sequence_no - b.sequence_no)
            .map((card) => (
              <WorkshopCardRenderer
                key={card.card_id}
                card={card}
                onContinueDiscussion={handleContinueDiscussion}
              />
            ))}
        </div>

        <div
          data-testid="servant-composer"
          style={{ borderTop: "1px solid #e2e8f0", padding: 12 }}
        >
          <input
            type="text"
            value={composerValue}
            onChange={(e) => setComposerValue(e.target.value)}
            placeholder="Message the workshop servant…"
            style={{ width: "100%", padding: 8, boxSizing: "border-box" }}
          />
        </div>
      </div>

      {/* Right: completeness rail + trading room CTA */}
      <div
        data-testid="completeness-rail"
        style={{
          width: 240,
          borderLeft: "1px solid #e2e8f0",
          overflow: "hidden",
          display: "flex",
          flexDirection: "column",
        }}
      >
        <div style={{ flex: 1, overflow: "auto" }}>
          <StrategyCompletenessRail
            completeness={completeness}
            readiness={readiness}
            nextQuestion={nextQuestion}
          />
        </div>

        {/* Add to Trading Room — enabled only when trading_room gate ready AND handler provided */}
        <div
          style={{
            padding: "10px 12px",
            borderTop: "1px solid #e2e8f0",
            flexShrink: 0,
          }}
        >
          {(() => {
            const tradingRoomReady = readiness?.highest_ready_gate === "trading_room";
            const isActive = tradingRoomReady && !!onAddToTradingRoom;
            const disabledReason = readiness
              ? tradingRoomReady
                ? null
                : `Trading Room gate not yet ready (highest: ${readiness.highest_ready_gate ?? "none"})`
              : "Readiness not yet assessed";
            return (
              <>
                <button
                  data-testid="add-to-trading-room-btn"
                  disabled={!isActive}
                  aria-disabled={!isActive}
                  title={disabledReason ?? undefined}
                  onClick={isActive ? onAddToTradingRoom : undefined}
                  style={{
                    width: "100%",
                    padding: "7px 12px",
                    borderRadius: 6,
                    border: "none",
                    fontSize: 12,
                    fontWeight: 600,
                    cursor: isActive ? "pointer" : "not-allowed",
                    background: isActive ? "#1d4ed8" : "#e5e7eb",
                    color: isActive ? "#fff" : "#9ca3af",
                    transition: "background 0.15s",
                  }}
                >
                  Add to Trading Room
                </button>
                {disabledReason && (
                  <div
                    data-testid="add-to-trading-room-reason"
                    style={{ fontSize: 10, color: "#9ca3af", marginTop: 4, textAlign: "center" }}
                  >
                    {disabledReason}
                  </div>
                )}
              </>
            );
          })()}
        </div>

        {/* Legacy test-id shims for existing tests */}
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
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page root
// ---------------------------------------------------------------------------

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
