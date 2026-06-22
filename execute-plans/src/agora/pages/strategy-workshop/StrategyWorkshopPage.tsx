import React, { useEffect, useState } from "react";
import {
  listWorkshops,
  getWorkshop,
  getWorkshopCompleteness,
  getWorkshopReadiness,
  listWorkshopCards,
  type WorkshopCard,
  type WorkshopReadiness,
} from "@/lib/bff-v1/agora/workshops";
import type { StrategyWorkshop, StrategyCompleteness } from "@/lib/bff-v1/agora/workshops";

interface StrategyWorkshopPageProps {
  workshopId?: string;
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

interface SessionViewProps {
  workshopId: string;
}

function WorkshopSessionView({ workshopId }: SessionViewProps): JSX.Element {
  const [workshop, setWorkshop] = useState<StrategyWorkshop | null>(null);
  const [completeness, setCompleteness] = useState<StrategyCompleteness | null>(null);
  const [readiness, setReadiness] = useState<WorkshopReadiness | null>(null);
  const [cards, setCards] = useState<WorkshopCard[]>([]);

  useEffect(() => {
    let cancelled = false;
    getWorkshop(workshopId)
      .then((ws) => { if (!cancelled) setWorkshop(ws); })
      .catch(() => undefined);
    getWorkshopCompleteness(workshopId)
      .then((c) => { if (!cancelled) setCompleteness(c); })
      .catch(() => undefined);
    getWorkshopReadiness(workshopId)
      .then((r) => { if (!cancelled) setReadiness(r); })
      .catch(() => undefined);
    listWorkshopCards(workshopId)
      .then((items) => { if (!cancelled) setCards(items); })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [workshopId]);

  return (
    <div data-testid="strategy-workshop-page-session" style={{ display: "flex", height: "100%", overflow: "hidden" }}>
      <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
        <div
          data-testid="workshop-conversation"
          style={{ flex: 1, overflow: "auto", padding: 16 }}
        >
          {workshop ? (
            <div>{workshop.workshop_id}</div>
          ) : (
            <div data-testid="workshop-session-loading">Loading…</div>
          )}
          {cards.map((card) => (
            <div key={card.card_id} data-testid={`workshop-card-${card.card_id}`}>
              {card.kind}
            </div>
          ))}
        </div>

        <div data-testid="servant-composer" style={{ borderTop: "1px solid #e2e8f0", padding: 12 }}>
          <input
            type="text"
            placeholder="Message the workshop servant…"
            style={{ width: "100%", padding: 8 }}
          />
        </div>
      </div>

      <div
        data-testid="completeness-rail"
        style={{ width: 240, borderLeft: "1px solid #e2e8f0", overflow: "auto", padding: 12 }}
      >
        {completeness ? (
          <div data-testid="completeness-grade">{completeness.overall_grade}</div>
        ) : (
          <div>—</div>
        )}
        {readiness && (
          <div data-testid="workshop-readiness">{readiness.ready ? "Ready" : "Not ready"}</div>
        )}
      </div>
    </div>
  );
}

export function StrategyWorkshopPage({ workshopId }: StrategyWorkshopPageProps): JSX.Element {
  if (workshopId) {
    return <WorkshopSessionView workshopId={workshopId} />;
  }
  return <WorkshopListView />;
}
