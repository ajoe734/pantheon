import React from "react";
import type { WorkshopCard } from "@/lib/bff-v1/agora/workshops";
import type { PayloadResearchProgress, ResearchExecutionStatus } from "./workshop-card-types";

export interface ResearchRunCardProps {
  card: WorkshopCard;
  onContinueDiscussion?: (cardId: string) => void;
}

const EXECUTION_STATUS_COLOR: Record<ResearchExecutionStatus, string> = {
  queued: "#6b7280",
  dispatching: "#2563eb",
  running: "#2563eb",
  succeeded: "#16a34a",
  failed: "#dc2626",
  cancelled: "#9ca3af",
  timed_out: "#d97706",
};

const CARD_STATUS_COLOR: Record<string, string> = {
  informational: "#6b7280",
  action_required: "#d97706",
  running: "#2563eb",
  completed: "#16a34a",
  failed: "#dc2626",
  stale: "#9ca3af",
};

export function ResearchRunCard({ card, onContinueDiscussion }: ResearchRunCardProps): JSX.Element {
  const payload = card.payload as unknown as PayloadResearchProgress;
  const progressPct = Math.min(100, Math.max(0, payload.progress));
  const statusColor = EXECUTION_STATUS_COLOR[payload.execution_status] ?? "#6b7280";
  const cardStatusColor = CARD_STATUS_COLOR[card.status] ?? "#6b7280";

  return (
    <div
      data-testid={`research-run-card-${card.card_id}`}
      style={{
        border: "1px solid #e5e7eb",
        borderRadius: 8,
        padding: 14,
        background: "#fff",
        display: "flex",
        flexDirection: "column",
        gap: 10,
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <div style={{ flex: 1 }}>
          <div style={{ fontWeight: 600, fontSize: 14 }}>{card.title}</div>
          {card.summary && (
            <div style={{ fontSize: 12, color: "#6b7280", marginTop: 2 }}>{card.summary}</div>
          )}
        </div>
        <span
          data-testid={`research-run-card-${card.card_id}-card-status`}
          style={{
            fontSize: 11,
            fontWeight: 600,
            color: cardStatusColor,
            padding: "2px 8px",
            borderRadius: 4,
            border: `1px solid ${cardStatusColor}`,
            whiteSpace: "nowrap",
            flexShrink: 0,
            marginLeft: 8,
          }}
        >
          {card.status}
        </span>
      </div>

      <div
        data-testid={`research-run-card-${card.card_id}-meta`}
        style={{ display: "flex", justifyContent: "space-between", fontSize: 12 }}
      >
        <span style={{ color: "#6b7280" }}>
          {payload.stage_type}
          {payload.backend && (
            <span style={{ marginLeft: 6, color: "#9ca3af" }}>· {payload.backend}</span>
          )}
        </span>
        <span
          data-testid={`research-run-card-${card.card_id}-execution-status`}
          style={{ fontWeight: 600, color: statusColor }}
        >
          {payload.execution_status}
        </span>
      </div>

      <div
        data-testid={`research-run-card-${card.card_id}-progress-bar`}
        style={{ height: 6, background: "#e5e7eb", borderRadius: 3, overflow: "hidden" }}
      >
        <div
          style={{
            height: "100%",
            width: `${progressPct}%`,
            background: payload.execution_status === "succeeded" ? "#16a34a" : statusColor,
            borderRadius: 3,
            transition: "width 0.3s",
          }}
        />
      </div>
      <div
        data-testid={`research-run-card-${card.card_id}-progress-pct`}
        style={{ fontSize: 11, color: "#9ca3af", textAlign: "right", marginTop: -6 }}
      >
        {Math.round(progressPct)}%
      </div>

      {payload.latest_progress_message && (
        <div
          data-testid={`research-run-card-${card.card_id}-message`}
          style={{ fontSize: 12, color: "#6b7280" }}
        >
          {payload.latest_progress_message}
        </div>
      )}

      {payload.warnings && payload.warnings.length > 0 && (
        <div
          data-testid={`research-run-card-${card.card_id}-warnings`}
          style={{
            background: "#fffbeb",
            border: "1px solid #fde68a",
            borderRadius: 4,
            padding: "6px 10px",
          }}
        >
          <div style={{ fontSize: 12, fontWeight: 600, color: "#92400e", marginBottom: 2 }}>
            Warnings
          </div>
          {payload.warnings.map((w, i) => (
            <div key={i} style={{ fontSize: 12, color: "#92400e" }}>
              {w}
            </div>
          ))}
        </div>
      )}

      {payload.blocking_reasons && payload.blocking_reasons.length > 0 && (
        <div
          data-testid={`research-run-card-${card.card_id}-blocking`}
          style={{
            background: "#fef2f2",
            border: "1px solid #fecaca",
            borderRadius: 4,
            padding: "6px 10px",
          }}
        >
          <div style={{ fontSize: 12, fontWeight: 600, color: "#991b1b", marginBottom: 2 }}>
            Blocking
          </div>
          {payload.blocking_reasons.map((r, i) => (
            <div key={i} style={{ fontSize: 12, color: "#991b1b" }}>
              {r}
            </div>
          ))}
        </div>
      )}

      <div style={{ display: "flex", gap: 8, marginTop: 2 }}>
        {card.allowed_actions?.["cancel"] && (
          <button
            data-testid={`research-run-card-${card.card_id}-cancel`}
            style={{
              fontSize: 12,
              padding: "4px 12px",
              borderRadius: 4,
              border: "1px solid #dc2626",
              background: "#fef2f2",
              color: "#dc2626",
              cursor: "pointer",
            }}
          >
            Cancel
          </button>
        )}
        {onContinueDiscussion && (
          <button
            data-testid={`research-run-card-${card.card_id}-discuss`}
            onClick={() => onContinueDiscussion(card.card_id)}
            style={{
              fontSize: 12,
              padding: "4px 12px",
              borderRadius: 4,
              border: "1px solid #e5e7eb",
              background: "#fff",
              color: "#374151",
              cursor: "pointer",
            }}
          >
            Ask Servant
          </button>
        )}
      </div>
    </div>
  );
}
