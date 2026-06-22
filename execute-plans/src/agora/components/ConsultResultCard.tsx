import React from "react";
import type { WorkshopCard } from "@/lib/bff-v1/agora/workshops";
import type { PayloadConsultResult } from "./workshop-card-types";

export interface ConsultResultCardProps {
  card: WorkshopCard;
  onContinueDiscussion?: (cardId: string) => void;
}

const STATUS_COLOR: Record<string, { bg: string; border: string; text: string }> = {
  pending: { bg: "#f9fafb", border: "#e5e7eb", text: "#6b7280" },
  in_progress: { bg: "#eff6ff", border: "#bfdbfe", text: "#2563eb" },
  completed: { bg: "#f0fdf4", border: "#bbf7d0", text: "#16a34a" },
  inconclusive: { bg: "#fffbeb", border: "#fde68a", text: "#92400e" },
  cancelled: { bg: "#fef2f2", border: "#fecaca", text: "#dc2626" },
};

const FRESHNESS_BADGE: Record<string, { label: string; color: string }> = {
  fresh: { label: "Fresh", color: "#16a34a" },
  stale: { label: "Stale", color: "#d97706" },
  expired: { label: "Expired", color: "#dc2626" },
};

export function ConsultResultCard({ card, onContinueDiscussion }: ConsultResultCardProps): JSX.Element {
  const payload = card.payload as unknown as PayloadConsultResult;
  const statusStyle = STATUS_COLOR[payload.status] ?? STATUS_COLOR.pending;
  const freshnessBadge = payload.freshness ? FRESHNESS_BADGE[payload.freshness] : null;

  return (
    <div
      data-testid={`consult-result-card-${card.card_id}`}
      style={{
        border: `1px solid ${statusStyle.border}`,
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
        <div style={{ display: "flex", gap: 6, alignItems: "center", marginLeft: 8, flexShrink: 0 }}>
          <span
            data-testid={`consult-result-card-${card.card_id}-status`}
            style={{
              fontSize: 11,
              fontWeight: 600,
              color: statusStyle.text,
              padding: "2px 8px",
              borderRadius: 4,
              border: `1px solid ${statusStyle.border}`,
              background: statusStyle.bg,
            }}
          >
            {payload.status}
          </span>
          {freshnessBadge && (
            <span
              data-testid={`consult-result-card-${card.card_id}-freshness`}
              style={{
                fontSize: 11,
                fontWeight: 600,
                color: freshnessBadge.color,
                padding: "2px 8px",
                borderRadius: 4,
                border: `1px solid ${freshnessBadge.color}`,
              }}
            >
              {freshnessBadge.label}
            </span>
          )}
        </div>
      </div>

      <div style={{ fontSize: 12, color: "#6b7280" }}>
        <span style={{ fontWeight: 500 }}>Type: </span>
        {payload.consultation_type}
        {payload.participant_persona_refs && payload.participant_persona_refs.length > 0 && (
          <span style={{ marginLeft: 8 }}>
            · {payload.participant_persona_refs.length} participant
            {payload.participant_persona_refs.length !== 1 ? "s" : ""}
          </span>
        )}
      </div>

      {payload.consensus_summary && (
        <div data-testid={`consult-result-card-${card.card_id}-consensus`}>
          <div style={{ fontSize: 12, fontWeight: 600, color: "#374151", marginBottom: 3 }}>
            Consensus
          </div>
          <div style={{ fontSize: 12, color: "#374151", lineHeight: 1.5 }}>
            {payload.consensus_summary}
          </div>
        </div>
      )}

      {payload.disagreements && payload.disagreements.length > 0 && (
        <div data-testid={`consult-result-card-${card.card_id}-disagreements`}>
          <div style={{ fontSize: 12, fontWeight: 600, color: "#92400e", marginBottom: 3 }}>
            Disagreements
          </div>
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {payload.disagreements.map((d, i) => (
              <li key={i} style={{ fontSize: 12, color: "#92400e", lineHeight: 1.5 }}>
                {d}
              </li>
            ))}
          </ul>
        </div>
      )}

      {payload.risk_notes && payload.risk_notes.length > 0 && (
        <div
          data-testid={`consult-result-card-${card.card_id}-risk-notes`}
          style={{
            background: "#fef2f2",
            border: "1px solid #fecaca",
            borderRadius: 4,
            padding: "6px 10px",
          }}
        >
          <div style={{ fontSize: 12, fontWeight: 600, color: "#991b1b", marginBottom: 2 }}>
            Risk Notes
          </div>
          {payload.risk_notes.map((note, i) => (
            <div key={i} style={{ fontSize: 12, color: "#991b1b" }}>
              {note}
            </div>
          ))}
        </div>
      )}

      {payload.conditions && payload.conditions.length > 0 && (
        <div data-testid={`consult-result-card-${card.card_id}-conditions`}>
          <div style={{ fontSize: 12, fontWeight: 600, color: "#374151", marginBottom: 3 }}>
            Conditions
          </div>
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {payload.conditions.map((c, i) => (
              <li key={i} style={{ fontSize: 12, color: "#374151", lineHeight: 1.5 }}>
                {c}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div style={{ display: "flex", gap: 8, marginTop: 2 }}>
        {onContinueDiscussion && (
          <button
            data-testid={`consult-result-card-${card.card_id}-discuss`}
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
