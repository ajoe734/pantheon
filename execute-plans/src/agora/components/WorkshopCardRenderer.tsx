import React from "react";
import type { WorkshopCard } from "@/lib/bff-v1/agora/workshops";
import { ResearchPlanCard } from "./ResearchPlanCard";
import { ConsultResultCard } from "./ConsultResultCard";
import { ResearchRunCard } from "./ResearchRunCard";
import { BacktestResultCard } from "./BacktestResultCard";
import type {
  PayloadUserStrategyDescription,
  PayloadServantReconstruction,
  PayloadCompletenessUpdate,
  PayloadMissingDefinition,
  PayloadNextQuestion,
  PayloadVersionPatchProposal,
  PayloadVersionCompare,
  PayloadReadinessGate,
  GateState,
  GateName,
} from "./workshop-card-types";

export interface WorkshopCardRendererProps {
  card: WorkshopCard;
  onContinueDiscussion?: (cardId: string) => void;
}

const CARD_BORDER: Record<string, string> = {
  informational: "#e5e7eb",
  action_required: "#fde68a",
  running: "#bfdbfe",
  completed: "#bbf7d0",
  failed: "#fecaca",
  stale: "#e5e7eb",
};

function CardShell({
  card,
  testId,
  children,
  onContinueDiscussion,
}: {
  card: WorkshopCard;
  testId: string;
  children: React.ReactNode;
  onContinueDiscussion?: (cardId: string) => void;
}): JSX.Element {
  const border = CARD_BORDER[card.status] ?? "#e5e7eb";
  return (
    <div
      data-testid={testId}
      style={{
        border: `1px solid ${border}`,
        borderRadius: 8,
        padding: 14,
        background: "#fff",
        display: "flex",
        flexDirection: "column",
        gap: 8,
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <div style={{ fontWeight: 600, fontSize: 13 }}>{card.title}</div>
        <span style={{ fontSize: 11, color: "#9ca3af", marginLeft: 8 }}>#{card.sequence_no}</span>
      </div>
      {card.summary && (
        <div style={{ fontSize: 12, color: "#6b7280" }}>{card.summary}</div>
      )}
      {children}
      {onContinueDiscussion && (
        <div>
          <button
            data-testid={`card-${card.card_id}-discuss`}
            onClick={() => onContinueDiscussion(card.card_id)}
            style={{
              fontSize: 11,
              padding: "3px 10px",
              borderRadius: 4,
              border: "1px solid #e5e7eb",
              background: "#fff",
              color: "#6b7280",
              cursor: "pointer",
            }}
          >
            Ask Servant
          </button>
        </div>
      )}
    </div>
  );
}

function UserStrategyDescriptionCard({ card, onContinueDiscussion }: WorkshopCardRendererProps): JSX.Element {
  const p = card.payload as unknown as PayloadUserStrategyDescription;
  return (
    <CardShell card={card} testId={`workshop-card-user-desc-${card.card_id}`} onContinueDiscussion={onContinueDiscussion}>
      <div
        data-testid={`workshop-card-user-desc-${card.card_id}-content`}
        style={{ fontSize: 13, color: "#374151", lineHeight: 1.6, whiteSpace: "pre-wrap" }}
      >
        {p.owner_visible_content}
      </div>
    </CardShell>
  );
}

function ServantReconstructionCard({ card, onContinueDiscussion }: WorkshopCardRendererProps): JSX.Element {
  const p = card.payload as unknown as PayloadServantReconstruction;
  return (
    <CardShell card={card} testId={`workshop-card-servant-${card.card_id}`} onContinueDiscussion={onContinueDiscussion}>
      {p.causal_chain && p.causal_chain.length > 0 && (
        <div data-testid={`workshop-card-servant-${card.card_id}-chain`}>
          <div style={{ fontSize: 12, fontWeight: 600, color: "#374151", marginBottom: 4 }}>
            Causal Chain
          </div>
          {p.causal_chain.map((step) => (
            <div
              key={step.step_id}
              style={{
                fontSize: 12,
                padding: "4px 8px",
                borderLeft: "2px solid #e5e7eb",
                marginBottom: 4,
                color: "#374151",
              }}
            >
              <span style={{ fontWeight: 500 }}>{step.premise}</span>
              {" → "}
              {step.mechanism}
              <span style={{ color: "#9ca3af", marginLeft: 6 }}>
                ({Math.round(step.confidence * 100)}%)
              </span>
            </div>
          ))}
        </div>
      )}
      {p.servant_inferences && p.servant_inferences.length > 0 && (
        <div data-testid={`workshop-card-servant-${card.card_id}-inferences`}>
          <div style={{ fontSize: 12, fontWeight: 600, color: "#92400e", marginBottom: 3 }}>
            Inferences
          </div>
          {p.servant_inferences.map((inf, i) => (
            <div key={i} style={{ fontSize: 12, color: "#92400e" }}>
              {inf.needs_confirmation && <span style={{ marginRight: 4 }}>⚠</span>}
              {inf.statement}
            </div>
          ))}
        </div>
      )}
      {p.contradictions && p.contradictions.length > 0 && (
        <div
          data-testid={`workshop-card-servant-${card.card_id}-contradictions`}
          style={{ fontSize: 12, color: "#dc2626" }}
        >
          <span style={{ fontWeight: 600 }}>Contradictions: </span>
          {p.contradictions.join("; ")}
        </div>
      )}
    </CardShell>
  );
}

function CompletenessUpdateCard({ card, onContinueDiscussion }: WorkshopCardRendererProps): JSX.Element {
  const p = card.payload as unknown as PayloadCompletenessUpdate;
  return (
    <CardShell card={card} testId={`workshop-card-completeness-${card.card_id}`} onContinueDiscussion={onContinueDiscussion}>
      <div style={{ fontSize: 12 }}>
        <span style={{ fontWeight: 500 }}>Overall: </span>
        <span>{p.overall_grade}</span>
        {p.research_ready && (
          <span
            data-testid={`workshop-card-completeness-${card.card_id}-ready`}
            style={{ marginLeft: 8, color: "#16a34a", fontWeight: 500 }}
          >
            ✓ Research Ready
          </span>
        )}
      </div>
      {p.dimension_updates && p.dimension_updates.length > 0 && (
        <div data-testid={`workshop-card-completeness-${card.card_id}-dimensions`}>
          {p.dimension_updates.map((dim) => (
            <div
              key={dim.dimension}
              style={{
                display: "flex",
                justifyContent: "space-between",
                fontSize: 12,
                padding: "2px 0",
              }}
            >
              <span style={{ textTransform: "capitalize" }}>{dim.dimension.replace(/_/g, " ")}</span>
              <span style={{ color: "#6b7280" }}>
                {dim.prior_grade} → {dim.current_grade}
              </span>
            </div>
          ))}
        </div>
      )}
      {p.blockers && p.blockers.length > 0 && (
        <div style={{ fontSize: 12, color: "#dc2626" }}>
          <span style={{ fontWeight: 600 }}>Blockers: </span>
          {p.blockers.join(", ")}
        </div>
      )}
    </CardShell>
  );
}

function MissingDefinitionCard({ card, onContinueDiscussion }: WorkshopCardRendererProps): JSX.Element {
  const p = card.payload as unknown as PayloadMissingDefinition;
  const severityColor: Record<string, string> = {
    low: "#6b7280",
    medium: "#ca8a04",
    high: "#d97706",
    critical: "#dc2626",
  };
  return (
    <CardShell card={card} testId={`workshop-card-missing-${card.card_id}`} onContinueDiscussion={onContinueDiscussion}>
      <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
        <span
          style={{
            fontSize: 11,
            fontWeight: 600,
            color: severityColor[p.severity] ?? "#6b7280",
            padding: "1px 6px",
            borderRadius: 3,
            border: `1px solid ${severityColor[p.severity] ?? "#e5e7eb"}`,
          }}
        >
          {p.severity}
        </span>
        <span style={{ fontSize: 12, color: "#374151", fontWeight: 500 }}>{p.category}</span>
      </div>
      <div style={{ fontSize: 13, color: "#374151" }}>{p.missing_definition}</div>
      <div style={{ fontSize: 12, color: "#6b7280", fontStyle: "italic" }}>{p.why_it_matters}</div>
      {p.answer_options && p.answer_options.length > 0 && (
        <div
          data-testid={`workshop-card-missing-${card.card_id}-options`}
          style={{ display: "flex", flexWrap: "wrap", gap: 4 }}
        >
          {p.answer_options.map((opt, i) => (
            <button
              key={i}
              style={{
                fontSize: 11,
                padding: "2px 8px",
                borderRadius: 4,
                border: "1px solid #e5e7eb",
                background: "#f9fafb",
                cursor: "pointer",
              }}
            >
              {opt}
            </button>
          ))}
        </div>
      )}
    </CardShell>
  );
}

function NextQuestionCard({ card, onContinueDiscussion }: WorkshopCardRendererProps): JSX.Element {
  const p = card.payload as unknown as PayloadNextQuestion;
  return (
    <CardShell card={card} testId={`workshop-card-next-q-${card.card_id}`} onContinueDiscussion={onContinueDiscussion}>
      <div style={{ fontSize: 14, color: "#111827", fontWeight: 500 }}>{p.question}</div>
      {p.why_now && (
        <div style={{ fontSize: 12, color: "#6b7280", fontStyle: "italic" }}>{p.why_now}</div>
      )}
      {p.answer_options && p.answer_options.length > 0 && (
        <div
          data-testid={`workshop-card-next-q-${card.card_id}-options`}
          style={{ display: "flex", flexWrap: "wrap", gap: 4 }}
        >
          {p.answer_options.map((opt, i) => (
            <button
              key={i}
              style={{
                fontSize: 11,
                padding: "3px 10px",
                borderRadius: 4,
                border: "1px solid #93c5fd",
                background: "#eff6ff",
                color: "#1d4ed8",
                cursor: "pointer",
              }}
            >
              {opt}
            </button>
          ))}
        </div>
      )}
    </CardShell>
  );
}

function VersionPatchProposalCard({ card, onContinueDiscussion }: WorkshopCardRendererProps): JSX.Element {
  const p = card.payload as unknown as PayloadVersionPatchProposal;
  const statusColor: Record<string, string> = {
    draft: "#6b7280",
    validating: "#2563eb",
    validated: "#16a34a",
    invalid: "#dc2626",
    accepted: "#16a34a",
    rejected: "#dc2626",
    superseded: "#9ca3af",
  };
  return (
    <CardShell card={card} testId={`workshop-card-patch-${card.card_id}`} onContinueDiscussion={onContinueDiscussion}>
      <div style={{ display: "flex", gap: 8 }}>
        <span
          style={{
            fontSize: 11,
            fontWeight: 600,
            color: statusColor[p.status] ?? "#6b7280",
            padding: "1px 6px",
            borderRadius: 3,
            border: `1px solid ${statusColor[p.status] ?? "#e5e7eb"}`,
          }}
        >
          {p.status}
        </span>
        <span style={{ fontSize: 12, color: "#6b7280" }}>base: {p.base_version}</span>
      </div>
      <div style={{ fontSize: 12, color: "#374151" }}>{p.rationale}</div>
      {p.change_summary && p.change_summary.length > 0 && (
        <div data-testid={`workshop-card-patch-${card.card_id}-changes`}>
          {p.change_summary.map((ch, i) => (
            <div key={i} style={{ fontSize: 12, padding: "2px 0", fontFamily: "monospace", color: "#374151" }}>
              <span style={{ color: "#6b7280" }}>{ch.path}: </span>
              {ch.summary}
            </div>
          ))}
        </div>
      )}
      {p.validation?.conflicts && p.validation.conflicts.length > 0 && (
        <div style={{ fontSize: 12, color: "#dc2626" }}>
          Conflicts: {p.validation.conflicts.join("; ")}
        </div>
      )}
    </CardShell>
  );
}

function VersionCompareCard({ card, onContinueDiscussion }: WorkshopCardRendererProps): JSX.Element {
  const p = card.payload as unknown as PayloadVersionCompare;
  return (
    <CardShell card={card} testId={`workshop-card-compare-${card.card_id}`} onContinueDiscussion={onContinueDiscussion}>
      <div style={{ fontSize: 12, color: "#6b7280" }}>
        Base: {p.base_version.label} vs {p.candidate_versions.map((v) => v.label).join(", ")}
      </div>
      {p.field_diffs.length > 0 && (
        <div data-testid={`workshop-card-compare-${card.card_id}-diffs`}>
          <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 3 }}>
            {p.field_diffs.length} field change{p.field_diffs.length !== 1 ? "s" : ""}
          </div>
          {p.field_diffs.slice(0, 5).map((d, i) => (
            <div key={i} style={{ fontSize: 12, fontFamily: "monospace", color: "#374151", padding: "1px 0" }}>
              <span
                style={{
                  color:
                    d.change_kind === "added"
                      ? "#16a34a"
                      : d.change_kind === "removed"
                      ? "#dc2626"
                      : "#374151",
                }}
              >
                {d.change_kind === "added" ? "+" : d.change_kind === "removed" ? "-" : "~"}{" "}
              </span>
              {d.path}
            </div>
          ))}
          {p.field_diffs.length > 5 && (
            <div style={{ fontSize: 11, color: "#9ca3af" }}>+{p.field_diffs.length - 5} more</div>
          )}
        </div>
      )}
      {p.recommendation?.rationale && (
        <div style={{ fontSize: 12, color: "#374151", fontStyle: "italic" }}>
          Recommendation: {p.recommendation.rationale}
        </div>
      )}
    </CardShell>
  );
}

const GATE_LABELS: Record<GateName, string> = {
  preliminary_research: "Preliminary Research",
  full_validation: "Full Validation",
  trading_room: "Trading Room",
};

const GATE_STATE_COLOR: Record<GateState, string> = {
  ready: "#16a34a",
  conditional: "#ca8a04",
  blocked: "#dc2626",
  not_assessed: "#6b7280",
  stale: "#d97706",
};

function ReadinessGateCard({ card, onContinueDiscussion }: WorkshopCardRendererProps): JSX.Element {
  const p = card.payload as unknown as PayloadReadinessGate;
  return (
    <CardShell card={card} testId={`workshop-card-readiness-${card.card_id}`} onContinueDiscussion={onContinueDiscussion}>
      <div data-testid={`workshop-card-readiness-${card.card_id}-gates`}>
        {p.gates.map((gate) => {
          const color = GATE_STATE_COLOR[gate.state] ?? "#6b7280";
          const label = GATE_LABELS[gate.gate as GateName] ?? gate.gate;
          return (
            <div
              key={gate.gate}
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                padding: "4px 0",
                borderBottom: "1px solid #f3f4f6",
                fontSize: 12,
              }}
            >
              <span>{label}</span>
              <span style={{ fontWeight: 600, color }}>{gate.state}</span>
            </div>
          );
        })}
      </div>
      {p.hard_blockers && p.hard_blockers.length > 0 && (
        <div style={{ fontSize: 12, color: "#dc2626" }}>
          <span style={{ fontWeight: 600 }}>Blockers: </span>
          {p.hard_blockers.join("; ")}
        </div>
      )}
      {p.highest_ready_gate && (
        <div style={{ fontSize: 12, color: "#6b7280" }}>
          Highest ready: {GATE_LABELS[p.highest_ready_gate] ?? p.highest_ready_gate}
        </div>
      )}
    </CardShell>
  );
}

function FallbackCard({ card }: { card: WorkshopCard }): JSX.Element {
  return (
    <div
      data-testid={`workshop-card-unknown-${card.card_id}`}
      style={{
        border: "1px solid #e5e7eb",
        borderRadius: 8,
        padding: 12,
        background: "#f9fafb",
        fontSize: 12,
        color: "#6b7280",
      }}
    >
      <span style={{ fontWeight: 500 }}>{card.card_type}</span>
      {card.title && <span style={{ marginLeft: 6 }}>{card.title}</span>}
    </div>
  );
}

export function WorkshopCardRenderer({ card, onContinueDiscussion }: WorkshopCardRendererProps): JSX.Element {
  switch (card.card_type) {
    case "user_strategy_description":
      return <UserStrategyDescriptionCard card={card} onContinueDiscussion={onContinueDiscussion} />;
    case "servant_reconstruction":
      return <ServantReconstructionCard card={card} onContinueDiscussion={onContinueDiscussion} />;
    case "completeness_update":
      return <CompletenessUpdateCard card={card} onContinueDiscussion={onContinueDiscussion} />;
    case "missing_definition":
      return <MissingDefinitionCard card={card} onContinueDiscussion={onContinueDiscussion} />;
    case "next_question":
      return <NextQuestionCard card={card} onContinueDiscussion={onContinueDiscussion} />;
    case "research_plan_proposal":
      return <ResearchPlanCard card={card} onContinueDiscussion={onContinueDiscussion} />;
    case "research_progress":
      return <ResearchRunCard card={card} onContinueDiscussion={onContinueDiscussion} />;
    case "research_result":
      return <BacktestResultCard card={card} onContinueDiscussion={onContinueDiscussion} />;
    case "consult_result":
      return <ConsultResultCard card={card} onContinueDiscussion={onContinueDiscussion} />;
    case "version_patch_proposal":
      return <VersionPatchProposalCard card={card} onContinueDiscussion={onContinueDiscussion} />;
    case "version_compare":
      return <VersionCompareCard card={card} onContinueDiscussion={onContinueDiscussion} />;
    case "readiness_gate":
      return <ReadinessGateCard card={card} onContinueDiscussion={onContinueDiscussion} />;
    default:
      return <FallbackCard card={card} />;
  }
}
