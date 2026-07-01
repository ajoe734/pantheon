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

type CompletenessDimension = StrategyCompleteness["dimensions"][number]["dimension"];
type V10BlockState = "confirmed" | "inferred" | "missing" | "weak" | "conflict";

interface V10BlockDefinition {
  id: string;
  label: string;
  dimension: CompletenessDimension;
  hints: string[];
}

interface V10Block {
  id: string;
  label: string;
  state: V10BlockState;
  reason: string;
}

const GATE_LABELS: Record<GateName, string> = {
  preliminary_research: "Preliminary Research",
  full_validation: "Full Validation",
  trading_room: "Trading Room",
};

const GATE_STATE_BADGE: Record<GateState, { label: string; color: string }> = {
  ready: { label: "Ready", color: "#62c58f" },
  conditional: { label: "Conditional", color: "#e0b65f" },
  blocked: { label: "Blocked", color: "#ef8585" },
  not_assessed: { label: "Not Assessed", color: "#8c96a6" },
  stale: { label: "Stale", color: "#d79559" },
};

const DIMENSION_GRADE_BADGE: Record<string, { label: string; color: string }> = {
  complete: { label: "Complete", color: "#62c58f" },
  partial: { label: "Partial", color: "#e0b65f" },
  missing: { label: "Missing", color: "#ef8585" },
};

const V10_BLOCKS: V10BlockDefinition[] = [
  {
    id: "market_scope",
    label: "研究對象與市場範圍",
    dimension: "market_scope",
    hints: ["market", "universe", "scope", "市場", "標的", "範圍"],
  },
  {
    id: "insider_branch_mapping",
    label: "關係人與分點映射",
    dimension: "data_dependencies",
    hints: ["relationship", "insider", "branch", "mapping", "關係人", "分點", "映射"],
  },
  {
    id: "winner_branch_scoring",
    label: "贏家分點評分",
    dimension: "hypothesis",
    hints: ["score", "winner", "勝率", "分數", "贏家"],
  },
  {
    id: "migration_reverse_flow",
    label: "分點遷移與反向流",
    dimension: "data_dependencies",
    hints: ["migration", "reverse", "flow", "cluster", "遷移", "反向", "出貨"],
  },
  {
    id: "event_lead",
    label: "事件領先關聯",
    dimension: "hypothesis",
    hints: ["event", "lead", "announcement", "事件", "領先", "公告"],
  },
  {
    id: "signal_formation",
    label: "信號形成",
    dimension: "hypothesis",
    hints: ["signal", "alpha", "entry condition", "信號", "條件"],
  },
  {
    id: "entry_holding",
    label: "進場與持有週期",
    dimension: "execution_profile",
    hints: ["entry", "holding", "horizon", "進場", "持有", "週期"],
  },
  {
    id: "add_reduce_exit",
    label: "加碼、減碼與出場",
    dimension: "execution_profile",
    hints: ["add", "reduce", "exit", "加碼", "減碼", "出場"],
  },
  {
    id: "sizing_leverage",
    label: "部位與槓桿",
    dimension: "risk_constraints",
    hints: ["sizing", "position", "leverage", "部位", "槓桿"],
  },
  {
    id: "cost_liquidity_capacity",
    label: "成本、流動性與容量",
    dimension: "risk_constraints",
    hints: ["cost", "liquidity", "capacity", "成本", "流動性", "容量"],
  },
  {
    id: "validation_backtest_refutation",
    label: "驗證、回測與反證",
    dimension: "evaluation_plan",
    hints: ["validation", "backtest", "oos", "refutation", "驗證", "回測", "反證"],
  },
  {
    id: "monitoring_update",
    label: "監控與策略更新",
    dimension: "governance",
    hints: ["monitor", "update", "version", "監控", "更新", "版本"],
  },
];

const V10_STATE_BADGE: Record<V10BlockState, { label: string; mark: string; color: string; bg: string }> = {
  confirmed: { label: "已確認", mark: "OK", color: "#62c58f", bg: "transparent" },
  inferred: { label: "僕人推定", mark: "~", color: "#e8b750", bg: "transparent" },
  missing: { label: "尚缺", mark: "!", color: "#ef8585", bg: "rgba(239,133,133,0.12)" },
  weak: { label: "薄弱", mark: "^", color: "#e0b65f", bg: "rgba(224,182,95,0.11)" },
  conflict: { label: "衝突", mark: "X", color: "#f08a8a", bg: "rgba(240,138,138,0.14)" },
};

function includesAny(text: string, hints: string[]): boolean {
  const lowered = text.toLowerCase();
  return hints.some((hint) => lowered.includes(hint.toLowerCase()));
}

function conflictText(text: string): boolean {
  return /conflict|contradict|look[- ]?ahead|leak|illegal|衝突|矛盾|公告後|事後|違法|內線|操縱|身份認定/.test(
    text.toLowerCase(),
  );
}

function notesForBlock(
  completeness: StrategyCompleteness | null,
  block: V10BlockDefinition,
): string[] {
  if (!completeness) return [];
  const dim = completeness.dimensions.find((item) => item.dimension === block.dimension);
  const dimensionNotes = [...(dim?.gaps ?? []), ...(dim?.required_actions ?? [])];
  const blockerNotes = completeness.blockers ?? [];
  const allNotes = [...dimensionNotes, ...blockerNotes];
  const hinted = allNotes.filter((note) => includesAny(note, block.hints));
  return hinted.length > 0 ? hinted : dimensionNotes;
}

function readinessNotesForBlock(
  readiness: StrategyReadinessAssessment | null,
  block: V10BlockDefinition,
): string[] {
  if (!readiness) return [];
  const relevantGates = readiness.gates.filter((gate) => {
    if (block.id === "validation_backtest_refutation") return gate.gate === "full_validation";
    if (block.id === "monitoring_update") return gate.gate === "trading_room";
    if (["entry_holding", "add_reduce_exit", "sizing_leverage", "cost_liquidity_capacity"].includes(block.id)) {
      return gate.gate === "trading_room";
    }
    return gate.gate === "preliminary_research";
  });
  return relevantGates.flatMap((gate) =>
    gate.requirements
      .filter((requirement) => includesAny(String((requirement as { title?: unknown }).title ?? ""), block.hints))
      .map((requirement) => {
        const req = requirement as { title?: string; state?: string; summary?: string };
        return `${req.title ?? ""} ${req.state ?? ""} ${req.summary ?? ""}`.trim();
      }),
  );
}

function stateForBlock(
  completeness: StrategyCompleteness | null,
  readiness: StrategyReadinessAssessment | null,
  block: V10BlockDefinition,
): V10Block {
  const dim = completeness?.dimensions.find((item) => item.dimension === block.dimension);
  const notes = [...notesForBlock(completeness, block), ...readinessNotesForBlock(readiness, block)];
  const joined = notes.join(" ");

  if (joined && conflictText(joined)) {
    return { id: block.id, label: block.label, state: "conflict", reason: notes[0] ?? "存在策略語意衝突。" };
  }
  if (!dim || dim.grade === "missing") {
    return { id: block.id, label: block.label, state: "missing", reason: notes[0] ?? "尚未由 completeness assessment 覆蓋。" };
  }
  if (dim.grade === "partial") {
    return {
      id: block.id,
      label: block.label,
      state: notes.length > 0 ? "weak" : "inferred",
      reason: notes[0] ?? "由僕人先行推定，仍待交易員裁示。",
    };
  }
  return { id: block.id, label: block.label, state: "confirmed", reason: notes[0] ?? "已由現有 completeness assessment 確認。" };
}

function deriveV10Blocks(
  completeness: StrategyCompleteness | null,
  readiness: StrategyReadinessAssessment | null,
): V10Block[] {
  return V10_BLOCKS.map((block) => stateForBlock(completeness, readiness, block));
}

function blockCompletenessPercent(blocks: V10Block[]): number {
  const weights: Record<V10BlockState, number> = {
    confirmed: 1,
    inferred: 0.72,
    weak: 0.48,
    missing: 0.12,
    conflict: 0,
  };
  if (blocks.length === 0) return 0;
  return Math.round((blocks.reduce((sum, block) => sum + weights[block.state], 0) / blocks.length) * 100);
}

export function StrategyCompletenessRail({
  completeness,
  readiness,
  nextQuestion,
}: StrategyCompletenessRailProps): JSX.Element {
  const nextQuestionPayload = nextQuestion?.card_type === "next_question"
    ? (nextQuestion.payload as unknown as PayloadNextQuestion)
    : null;
  const blocks = deriveV10Blocks(completeness, readiness);
  const percent = blockCompletenessPercent(blocks);

  return (
    <div
      data-testid="strategy-completeness-rail"
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 18,
        padding: 18,
        overflow: "auto",
        height: "100%",
        background: "#171b22",
        color: "#f3efe7",
      }}
    >
      <section>
        <div
          data-testid="completeness-rail-heading"
          style={{ color: "#8c96a6", fontWeight: 700, fontSize: 10.5, letterSpacing: "0.12em" }}
        >
          策略完整度 · 12 區塊
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 10, marginBottom: 10 }}>
          <div
            aria-hidden="true"
            style={{ flex: 1, height: 6, background: "#2a303b", borderRadius: 3, overflow: "hidden" }}
          >
            <div style={{ width: `${percent}%`, height: "100%", background: "#e8b750" }} />
          </div>
          <span style={{ color: "#c4cbd6", fontFamily: "IBM Plex Mono, monospace", fontSize: 11 }}>
            {percent}%
          </span>
        </div>
        <div data-testid="v10-strategy-blocks" style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          {blocks.map((block) => {
            const meta = V10_STATE_BADGE[block.state];
            return (
              <div
                key={block.id}
                data-testid={`v10-strategy-block-${block.id}`}
                title={block.reason}
                style={{
                  display: "grid",
                  gridTemplateColumns: "24px minmax(0, 1fr) auto",
                  alignItems: "center",
                  gap: 8,
                  padding: "7px 9px",
                  borderRadius: 8,
                  background: meta.bg,
                }}
              >
                <span
                  style={{
                    width: 20,
                    textAlign: "center",
                    color: meta.color,
                    fontFamily: "IBM Plex Mono, monospace",
                    fontSize: 10,
                  }}
                >
                  {meta.mark}
                </span>
                <span style={{ color: "#f3efe7", fontSize: 12, minWidth: 0 }}>{block.label}</span>
                <span
                  data-testid={`v10-strategy-block-${block.id}-state`}
                  style={{ color: meta.color, fontSize: 10.5, whiteSpace: "nowrap" }}
                >
                  {meta.label}
                </span>
              </div>
            );
          })}
        </div>
      </section>

      <section data-testid="completeness-overall" style={{ borderTop: "1px solid #2a303b", paddingTop: 14 }}>
        <div style={{ fontWeight: 700, fontSize: 12.5, marginBottom: 8 }}>Contract completeness</div>
        {completeness ? (
          <>
            <span
              data-testid="completeness-overall-grade"
              style={{
                display: "inline-block",
                padding: "2px 8px",
                borderRadius: 5,
                fontSize: 11,
                fontWeight: 700,
                background: "#202631",
                color:
                  completeness.overall_grade === "complete"
                    ? "#62c58f"
                    : completeness.overall_grade === "mostly_complete"
                    ? "#7ccf9f"
                    : completeness.overall_grade === "partial"
                    ? "#e0b65f"
                    : "#ef8585",
              }}
            >
              {completeness.overall_grade}
            </span>
            <div style={{ marginTop: 10, display: "flex", flexDirection: "column", gap: 6 }}>
              {completeness.dimensions.map((dim) => {
                const badge = DIMENSION_GRADE_BADGE[dim.grade] ?? {
                  label: dim.grade,
                  color: "#8c96a6",
                };
                return (
                  <div
                    key={dim.dimension}
                    data-testid={`completeness-dimension-${dim.dimension}`}
                    style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}
                  >
                    <span style={{ fontSize: 11.5, textTransform: "capitalize", color: "#c4cbd6" }}>
                      {dim.dimension.replace(/_/g, " ")}
                    </span>
                    <span
                      data-testid={`completeness-dimension-${dim.dimension}-grade`}
                      style={{
                        fontSize: 10.5,
                        fontWeight: 700,
                        color: badge.color,
                        padding: "1px 6px",
                        borderRadius: 4,
                        border: `1px solid ${badge.color}`,
                      }}
                    >
                      {badge.label}
                    </span>
                  </div>
                );
              })}
            </div>
          </>
        ) : (
          <div data-testid="completeness-empty" style={{ fontSize: 12, color: "#8c96a6", fontStyle: "italic" }}>
            No completeness data yet.
          </div>
        )}
      </section>

      <section data-testid="readiness-gates-section" style={{ borderTop: "1px solid #2a303b", paddingTop: 14 }}>
        <div style={{ fontWeight: 700, fontSize: 12.5, marginBottom: 8 }}>Readiness Gates</div>
        {readiness ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {readiness.gates.map((gate) => {
              const stateBadge = GATE_STATE_BADGE[gate.state] ?? {
                label: gate.state,
                color: "#8c96a6",
              };
              const gateLabel = GATE_LABELS[gate.gate as GateName] ?? gate.gate;
              return (
                <div
                  key={gate.gate}
                  data-testid={`readiness-gate-${gate.gate}`}
                  style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}
                >
                  <span style={{ fontSize: 11.5, color: "#c4cbd6" }}>{gateLabel}</span>
                  <span
                    data-testid={`readiness-gate-${gate.gate}-state`}
                    style={{
                      fontSize: 10.5,
                      fontWeight: 700,
                      color: stateBadge.color,
                      padding: "1px 6px",
                      borderRadius: 4,
                      border: `1px solid ${stateBadge.color}`,
                    }}
                  >
                    {stateBadge.label}
                  </span>
                </div>
              );
            })}
            {readiness.highest_ready_gate && (
              <div data-testid="readiness-highest-gate" style={{ fontSize: 11, color: "#8c96a6", marginTop: 2 }}>
                Highest ready: {GATE_LABELS[readiness.highest_ready_gate] ?? readiness.highest_ready_gate}
              </div>
            )}
          </div>
        ) : (
          <div data-testid="readiness-gates-empty" style={{ fontSize: 12, color: "#8c96a6", fontStyle: "italic" }}>
            Not yet assessed.
          </div>
        )}
      </section>

      {nextQuestionPayload && (
        <section
          data-testid="next-question-section"
          style={{
            background: "rgba(232,183,80,0.12)",
            border: "1px solid rgba(232,183,80,0.42)",
            borderRadius: 11,
            padding: 14,
          }}
        >
          <div style={{ fontWeight: 700, fontSize: 12.5, marginBottom: 6, color: "#e8b750" }}>
            下一個高價值決策
          </div>
          <div data-testid="next-question-text" style={{ fontSize: 12, color: "#f3efe7", lineHeight: 1.55 }}>
            {nextQuestionPayload.question}
          </div>
          {nextQuestionPayload.why_now && (
            <div style={{ fontSize: 11, color: "#aeb7c4", marginTop: 6, lineHeight: 1.45 }}>
              {nextQuestionPayload.why_now}
            </div>
          )}
          {nextQuestionPayload.score_total !== undefined && (
            <div data-testid="next-question-score" style={{ fontSize: 11, color: "#aeb7c4", marginTop: 5 }}>
              Score: {nextQuestionPayload.score_total.toFixed(2)}
            </div>
          )}
        </section>
      )}
    </div>
  );
}
