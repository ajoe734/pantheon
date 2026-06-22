import React from "react";
import type { WorkshopCard } from "@/lib/bff-v1/agora/workshops";
import type { PayloadResearchPlanProposal } from "./workshop-card-types";

export interface ResearchPlanCardProps {
  card: WorkshopCard;
  onContinueDiscussion?: (cardId: string) => void;
}

const STATUS_COLOR: Record<string, string> = {
  informational: "#6b7280",
  action_required: "#d97706",
  running: "#2563eb",
  completed: "#16a34a",
  failed: "#dc2626",
  stale: "#9ca3af",
};

export function ResearchPlanCard({ card, onContinueDiscussion }: ResearchPlanCardProps): JSX.Element {
  const payload = card.payload as unknown as PayloadResearchPlanProposal;
  const statusColor = STATUS_COLOR[card.status] ?? "#6b7280";

  return (
    <div
      data-testid={`research-plan-card-${card.card_id}`}
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
        <div>
          <div style={{ fontWeight: 600, fontSize: 14 }}>{card.title}</div>
          {card.summary && (
            <div style={{ fontSize: 12, color: "#6b7280", marginTop: 2 }}>{card.summary}</div>
          )}
        </div>
        <span
          data-testid={`research-plan-card-${card.card_id}-status`}
          style={{
            fontSize: 11,
            fontWeight: 600,
            color: statusColor,
            padding: "2px 8px",
            borderRadius: 4,
            border: `1px solid ${statusColor}`,
            whiteSpace: "nowrap",
            flexShrink: 0,
            marginLeft: 8,
          }}
        >
          {card.status}
        </span>
      </div>

      {payload.objectives && payload.objectives.length > 0 && (
        <div data-testid={`research-plan-card-${card.card_id}-objectives`}>
          <div style={{ fontSize: 12, fontWeight: 600, color: "#374151", marginBottom: 4 }}>
            Objectives
          </div>
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {payload.objectives.map((obj, i) => (
              <li key={i} style={{ fontSize: 12, color: "#374151", lineHeight: 1.5 }}>
                {obj}
              </li>
            ))}
          </ul>
        </div>
      )}

      {payload.stages && payload.stages.length > 0 && (
        <div data-testid={`research-plan-card-${card.card_id}-stages`}>
          <div style={{ fontSize: 12, fontWeight: 600, color: "#374151", marginBottom: 4 }}>
            Stages
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {payload.stages.map((stage) => (
              <div
                key={stage.stage_id}
                data-testid={`research-plan-stage-${stage.stage_id}`}
                style={{
                  background: "#f9fafb",
                  borderRadius: 4,
                  padding: "6px 10px",
                  fontSize: 12,
                }}
              >
                <span style={{ fontWeight: 500 }}>{stage.stage_type}</span>
                {stage.preferred_backend && (
                  <span style={{ color: "#6b7280", marginLeft: 6 }}>
                    ({stage.preferred_backend})
                  </span>
                )}
                <div style={{ color: "#6b7280", marginTop: 2 }}>{stage.purpose}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {payload.evaluation_criteria && (
        <div data-testid={`research-plan-card-${card.card_id}-evaluation`}>
          <div style={{ fontSize: 12, fontWeight: 600, color: "#374151", marginBottom: 2 }}>
            Evaluation Criteria
          </div>
          <div style={{ fontSize: 12, color: "#6b7280" }}>{payload.evaluation_criteria}</div>
        </div>
      )}

      {payload.warnings && payload.warnings.length > 0 && (
        <div
          data-testid={`research-plan-card-${card.card_id}-warnings`}
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

      {payload.approval_requirement && (
        <div
          data-testid={`research-plan-card-${card.card_id}-approval`}
          style={{ fontSize: 12, color: "#6b7280" }}
        >
          <span style={{ fontWeight: 500 }}>Approval: </span>
          {payload.approval_requirement}
        </div>
      )}

      <div style={{ display: "flex", gap: 8, marginTop: 2 }}>
        {card.allowed_actions?.["approve"] && (
          <button
            data-testid={`research-plan-card-${card.card_id}-approve`}
            style={{
              fontSize: 12,
              padding: "4px 12px",
              borderRadius: 4,
              border: "1px solid #16a34a",
              background: "#f0fdf4",
              color: "#16a34a",
              cursor: "pointer",
            }}
          >
            Approve
          </button>
        )}
        {card.allowed_actions?.["reject"] && (
          <button
            data-testid={`research-plan-card-${card.card_id}-reject`}
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
            Reject
          </button>
        )}
        {onContinueDiscussion && (
          <button
            data-testid={`research-plan-card-${card.card_id}-discuss`}
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
