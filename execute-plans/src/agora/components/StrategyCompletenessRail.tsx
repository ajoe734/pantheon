import React from "react";
import type { StrategyCompleteness } from "@/lib/bff-v1/agora/workshops";
import type { StrategyReadinessAssessment } from "@/lib/bff-v1/agora/workshops";
import type { WorkshopCard } from "@/lib/bff-v1/agora/workshops";
import type { PayloadNextQuestion, GateState, GateName } from "./workshop-card-types";

export interface StrategyCompletenessRailProps {
  completeness: StrategyCompleteness | null;
  readiness: StrategyReadinessAssessment | null;
  nextQuestion: WorkshopCard | null;
}

const GATE_LABELS: Record<GateName, string> = {
  preliminary_research: "Preliminary Research",
  full_validation: "Full Validation",
  trading_room: "Trading Room",
};

const GATE_STATE_BADGE: Record<GateState, { label: string; color: string }> = {
  ready: { label: "Ready", color: "#16a34a" },
  conditional: { label: "Conditional", color: "#ca8a04" },
  blocked: { label: "Blocked", color: "#dc2626" },
  not_assessed: { label: "Not Assessed", color: "#6b7280" },
  stale: { label: "Stale", color: "#d97706" },
};

const DIMENSION_GRADE_BADGE: Record<string, { label: string; color: string }> = {
  complete: { label: "Complete", color: "#16a34a" },
  partial: { label: "Partial", color: "#ca8a04" },
  missing: { label: "Missing", color: "#dc2626" },
};

export function StrategyCompletenessRail({
  completeness,
  readiness,
  nextQuestion,
}: StrategyCompletenessRailProps): JSX.Element {
  const nextQuestionPayload = nextQuestion?.card_type === "next_question"
    ? (nextQuestion.payload as unknown as PayloadNextQuestion)
    : null;

  return (
    <div
      data-testid="strategy-completeness-rail"
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 16,
        padding: 12,
        overflow: "auto",
        height: "100%",
      }}
    >
      <div data-testid="completeness-rail-heading" style={{ fontWeight: 600, fontSize: 13 }}>
        Strategy Completeness
      </div>

      {completeness ? (
        <div data-testid="completeness-overall">
          <div style={{ fontSize: 12, color: "#6b7280", marginBottom: 4 }}>Overall</div>
          <span
            data-testid="completeness-overall-grade"
            style={{
              display: "inline-block",
              padding: "1px 8px",
              borderRadius: 4,
              fontSize: 12,
              fontWeight: 600,
              background:
                completeness.overall_grade === "complete"
                  ? "#dcfce7"
                  : completeness.overall_grade === "mostly_complete"
                  ? "#d1fae5"
                  : completeness.overall_grade === "partial"
                  ? "#fef9c3"
                  : "#fee2e2",
              color:
                completeness.overall_grade === "complete"
                  ? "#16a34a"
                  : completeness.overall_grade === "mostly_complete"
                  ? "#059669"
                  : completeness.overall_grade === "partial"
                  ? "#92400e"
                  : "#991b1b",
            }}
          >
            {completeness.overall_grade}
          </span>

          <div style={{ marginTop: 10, display: "flex", flexDirection: "column", gap: 6 }}>
            {completeness.dimensions.map((dim) => {
              const badge = DIMENSION_GRADE_BADGE[dim.grade] ?? {
                label: dim.grade,
                color: "#6b7280",
              };
              return (
                <div
                  key={dim.dimension}
                  data-testid={`completeness-dimension-${dim.dimension}`}
                  style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}
                >
                  <span style={{ fontSize: 12, textTransform: "capitalize" }}>
                    {dim.dimension.replace(/_/g, " ")}
                  </span>
                  <span
                    data-testid={`completeness-dimension-${dim.dimension}-grade`}
                    style={{
                      fontSize: 11,
                      fontWeight: 600,
                      color: badge.color,
                      padding: "1px 6px",
                      borderRadius: 3,
                      border: `1px solid ${badge.color}`,
                    }}
                  >
                    {badge.label}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      ) : (
        <div
          data-testid="completeness-empty"
          style={{ fontSize: 12, color: "#9ca3af", fontStyle: "italic" }}
        >
          No completeness data yet.
        </div>
      )}

      <hr style={{ border: "none", borderTop: "1px solid #e5e7eb", margin: 0 }} />

      <div data-testid="readiness-gates-section">
        <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 8 }}>Readiness Gates</div>
        {readiness ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {readiness.gates.map((gate) => {
              const stateBadge = GATE_STATE_BADGE[gate.state] ?? {
                label: gate.state,
                color: "#6b7280",
              };
              const gateLabel = GATE_LABELS[gate.gate as GateName] ?? gate.gate;
              return (
                <div
                  key={gate.gate}
                  data-testid={`readiness-gate-${gate.gate}`}
                  style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}
                >
                  <span style={{ fontSize: 12 }}>{gateLabel}</span>
                  <span
                    data-testid={`readiness-gate-${gate.gate}-state`}
                    style={{
                      fontSize: 11,
                      fontWeight: 600,
                      color: stateBadge.color,
                      padding: "1px 6px",
                      borderRadius: 3,
                      border: `1px solid ${stateBadge.color}`,
                    }}
                  >
                    {stateBadge.label}
                  </span>
                </div>
              );
            })}
            {readiness.highest_ready_gate && (
              <div
                data-testid="readiness-highest-gate"
                style={{ fontSize: 11, color: "#6b7280", marginTop: 2 }}
              >
                Highest ready: {GATE_LABELS[readiness.highest_ready_gate] ?? readiness.highest_ready_gate}
              </div>
            )}
          </div>
        ) : (
          <div
            data-testid="readiness-gates-empty"
            style={{ fontSize: 12, color: "#9ca3af", fontStyle: "italic" }}
          >
            Not yet assessed.
          </div>
        )}
      </div>

      {nextQuestionPayload && (
        <>
          <hr style={{ border: "none", borderTop: "1px solid #e5e7eb", margin: 0 }} />
          <div data-testid="next-question-section">
            <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 6 }}>
              Next High-Value Question
            </div>
            <div
              data-testid="next-question-text"
              style={{ fontSize: 12, color: "#374151", lineHeight: 1.5 }}
            >
              {nextQuestionPayload.question}
            </div>
            {nextQuestionPayload.why_now && (
              <div style={{ fontSize: 11, color: "#6b7280", marginTop: 4, fontStyle: "italic" }}>
                {nextQuestionPayload.why_now}
              </div>
            )}
            {nextQuestionPayload.score_total !== undefined && (
              <div
                data-testid="next-question-score"
                style={{ fontSize: 11, color: "#6b7280", marginTop: 4 }}
              >
                Score: {nextQuestionPayload.score_total.toFixed(2)}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
