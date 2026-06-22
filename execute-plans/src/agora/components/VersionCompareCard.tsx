import React from "react";
import type { WorkshopCard } from "@/lib/bff-v1/agora/workshops";
import type {
  PayloadVersionCompare,
  FieldDiff,
  MetricDiff,
  RiskDiff,
  ReadinessDiff,
  GateName,
} from "./workshop-card-types";

export interface VersionCompareCardProps {
  card: WorkshopCard;
  onContinueDiscussion?: (cardId: string) => void;
}

// Evidence class display config.
// RULE: predicted must never be visually treated as an observed metric.
const EVIDENCE_CLASS_CONFIG: Record<
  string,
  { label: string; color: string; background: string; border: string; italic: boolean }
> = {
  predicted: {
    label: "Predicted",
    color: "#92400e",
    background: "#fffbeb",
    border: "1px dashed #fbbf24",
    italic: true,
  },
  backtested_in_sample: {
    label: "Backtest (IS)",
    color: "#1d4ed8",
    background: "#eff6ff",
    border: "1px solid #bfdbfe",
    italic: false,
  },
  backtested_oos: {
    label: "Backtest (OOS)",
    color: "#166534",
    background: "#f0fdf4",
    border: "1px solid #bbf7d0",
    italic: false,
  },
  paper_observed: {
    label: "Paper Observed",
    color: "#6b21a8",
    background: "#faf5ff",
    border: "1px solid #e9d5ff",
    italic: false,
  },
};

const CHANGE_KIND_COLOR: Record<string, string> = {
  added: "#16a34a",
  removed: "#dc2626",
  changed: "#d97706",
  unchanged: "#9ca3af",
};

const CHANGE_KIND_PREFIX: Record<string, string> = {
  added: "+",
  removed: "-",
  changed: "~",
  unchanged: " ",
};

const MATERIALITY_COLOR: Record<string, string> = {
  critical: "#dc2626",
  high: "#d97706",
  medium: "#ca8a04",
  low: "#9ca3af",
};

const GATE_LABELS: Record<GateName, string> = {
  preliminary_research: "Preliminary Research",
  full_validation: "Full Validation",
  trading_room: "Trading Room",
};

const GATE_STATE_COLOR: Record<string, string> = {
  ready: "#16a34a",
  conditional: "#ca8a04",
  blocked: "#dc2626",
  not_assessed: "#6b7280",
  stale: "#d97706",
};

const RISK_CHANGE_COLOR: Record<string, string> = {
  improved: "#16a34a",
  worsened: "#dc2626",
  unchanged: "#6b7280",
  uncertain: "#d97706",
};

function groupByCandidate<T extends { candidate_version_id: string }>(
  items: T[]
): Array<{ candidateId: string; rows: T[] }> {
  const order: string[] = [];
  const map: Record<string, T[]> = {};
  for (const item of items) {
    const id = item.candidate_version_id;
    if (!map[id]) { order.push(id); map[id] = []; }
    map[id].push(item);
  }
  return order.map((id) => ({ candidateId: id, rows: map[id] }));
}

function SectionHeader({ children }: { children: React.ReactNode }): JSX.Element {
  return (
    <div
      style={{
        fontSize: 11,
        fontWeight: 700,
        textTransform: "uppercase",
        letterSpacing: "0.05em",
        color: "#6b7280",
        marginBottom: 4,
      }}
    >
      {children}
    </div>
  );
}

function CandidateGroupHeader({ label }: { label: string }): JSX.Element {
  return (
    <div
      style={{
        fontSize: 10,
        fontWeight: 700,
        color: "#1d4ed8",
        padding: "1px 6px",
        background: "#eff6ff",
        borderRadius: 3,
        marginTop: 4,
        marginBottom: 4,
        display: "inline-block",
      }}
    >
      {label}
    </div>
  );
}

function FieldDiffRow({ diff }: { diff: FieldDiff }): JSX.Element {
  const color = CHANGE_KIND_COLOR[diff.change_kind] ?? "#374151";
  const prefix = CHANGE_KIND_PREFIX[diff.change_kind] ?? " ";
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 6,
        fontSize: 12,
        fontFamily: "monospace",
        padding: "2px 0",
        color,
      }}
    >
      <span style={{ width: 12, flexShrink: 0 }}>{prefix}</span>
      <span style={{ flex: 1, color: "#374151" }}>{diff.path}</span>
      {diff.materiality && (
        <span
          style={{
            fontSize: 10,
            padding: "0 4px",
            borderRadius: 3,
            border: `1px solid ${MATERIALITY_COLOR[diff.materiality] ?? "#e5e7eb"}`,
            color: MATERIALITY_COLOR[diff.materiality] ?? "#9ca3af",
          }}
        >
          {diff.materiality}
        </span>
      )}
    </div>
  );
}

function MetricDiffRow({ diff }: { diff: MetricDiff }): JSX.Element {
  const cfg = EVIDENCE_CLASS_CONFIG[diff.evidence_class] ?? {
    label: diff.evidence_class,
    color: "#6b7280",
    background: "#f9fafb",
    border: "1px solid #e5e7eb",
    italic: false,
  };

  const deltaSign =
    diff.absolute_delta != null
      ? diff.absolute_delta > 0
        ? "+"
        : diff.absolute_delta < 0
        ? ""
        : "±"
      : "";

  return (
    <div
      data-testid={`metric-diff-${diff.metric}`}
      style={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
        fontSize: 12,
        padding: "4px 8px",
        background: cfg.background,
        border: cfg.border,
        borderRadius: 4,
        marginBottom: 4,
        fontStyle: cfg.italic ? "italic" : "normal",
      }}
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 1 }}>
        <span style={{ fontWeight: 500, color: "#374151" }}>{diff.metric}</span>
        <span
          style={{
            fontSize: 10,
            color: cfg.color,
            fontWeight: 600,
          }}
        >
          {cfg.label}
        </span>
      </div>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 1 }}>
        {diff.base_value != null && (
          <span style={{ fontSize: 11, color: "#9ca3af" }}>
            base: {diff.base_value}
          </span>
        )}
        {diff.candidate_value != null && (
          <span style={{ fontSize: 11, color: "#374151", fontWeight: 500 }}>
            {diff.candidate_value}
          </span>
        )}
        {diff.absolute_delta != null && (
          <span
            style={{
              fontSize: 11,
              fontWeight: 600,
              color:
                diff.absolute_delta > 0 ? "#16a34a" : diff.absolute_delta < 0 ? "#dc2626" : "#6b7280",
            }}
          >
            {deltaSign}{diff.absolute_delta}
          </span>
        )}
      </div>
    </div>
  );
}

function RiskDiffRow({ diff }: { diff: RiskDiff }): JSX.Element {
  const color = RISK_CHANGE_COLOR[diff.change] ?? "#6b7280";
  return (
    <div
      style={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
        fontSize: 12,
        padding: "2px 0",
        borderBottom: "1px solid #f3f4f6",
      }}
    >
      <span style={{ color: "#374151" }}>{diff.risk_domain}</span>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end" }}>
        <span style={{ fontWeight: 600, color }}>{diff.change}</span>
        {diff.summary && (
          <span style={{ fontSize: 11, color: "#9ca3af" }}>{diff.summary}</span>
        )}
      </div>
    </div>
  );
}

function ReadinessDiffRow({ diff }: { diff: ReadinessDiff }): JSX.Element {
  const gateLabel = GATE_LABELS[diff.gate as GateName] ?? diff.gate;
  const baseColor = GATE_STATE_COLOR[diff.base_state] ?? "#6b7280";
  const candidateColor = GATE_STATE_COLOR[diff.candidate_state] ?? "#6b7280";
  return (
    <div
      style={{
        fontSize: 12,
        padding: "3px 0",
        borderBottom: "1px solid #f3f4f6",
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
      }}
    >
      <span style={{ color: "#374151" }}>{gateLabel}</span>
      <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
        <span style={{ color: baseColor }}>{diff.base_state}</span>
        <span style={{ color: "#9ca3af" }}>→</span>
        <span style={{ color: candidateColor, fontWeight: 600 }}>{diff.candidate_state}</span>
      </span>
    </div>
  );
}

const PREDICTED_SEPARATOR_STYLE = {
  fontSize: 10,
  color: "#92400e",
  fontWeight: 600,
  marginTop: 4,
  marginBottom: 2,
  fontStyle: "italic",
} as const;

function MetricDiffSection({ diffs }: { diffs: MetricDiff[] }): JSX.Element {
  const observed = diffs.filter((m) => m.evidence_class !== "predicted");
  const predicted = diffs.filter((m) => m.evidence_class === "predicted");
  return (
    <>
      {observed.map((diff, i) => (
        <MetricDiffRow key={`obs-${i}`} diff={diff} />
      ))}
      {predicted.length > 0 && (
        <>
          {observed.length > 0 && (
            <div style={PREDICTED_SEPARATOR_STYLE}>
              Predicted effects (not observed — subject to uncertainty)
            </div>
          )}
          {predicted.map((diff, i) => (
            <MetricDiffRow key={`pred-${i}`} diff={diff} />
          ))}
        </>
      )}
    </>
  );
}

export function VersionCompareCard({
  card,
  onContinueDiscussion,
}: VersionCompareCardProps): JSX.Element {
  const p = card.payload as unknown as PayloadVersionCompare;

  const candidateMap = Object.fromEntries(
    p.candidate_versions.map((v) => [v.workshop_version_id, v.label])
  );
  const multiCandidate = p.candidate_versions.length > 1;
  const candidateLabels = p.candidate_versions.map((v) => v.label).join(", ");

  return (
    <div
      data-testid={`version-compare-card-${card.card_id}`}
      style={{
        border: "1px solid #e5e7eb",
        borderRadius: 8,
        padding: 14,
        background: "#fff",
        display: "flex",
        flexDirection: "column",
        gap: 12,
      }}
    >
      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <div style={{ fontWeight: 600, fontSize: 13 }}>{card.title}</div>
        <span style={{ fontSize: 11, color: "#9ca3af", marginLeft: 8 }}>#{card.sequence_no}</span>
      </div>

      {/* Summary — matches CardShell behavior for other card types */}
      {card.summary && (
        <div style={{ fontSize: 12, color: "#6b7280" }}>{card.summary}</div>
      )}

      {/* Version breadcrumb */}
      <div
        data-testid={`version-compare-card-${card.card_id}-versions`}
        style={{
          display: "flex",
          gap: 6,
          alignItems: "center",
          fontSize: 12,
          flexWrap: "wrap",
        }}
      >
        <span
          style={{
            padding: "1px 8px",
            borderRadius: 4,
            background: "#f3f4f6",
            fontWeight: 500,
            color: "#374151",
          }}
        >
          Base: {p.base_version.label}
        </span>
        <span style={{ color: "#9ca3af" }}>vs</span>
        <span
          style={{
            padding: "1px 8px",
            borderRadius: 4,
            background: "#eff6ff",
            fontWeight: 500,
            color: "#1d4ed8",
          }}
        >
          {candidateLabels}
        </span>
      </div>

      {/* Field diffs */}
      {p.field_diffs.length > 0 && (
        <div data-testid={`version-compare-card-${card.card_id}-field-diffs`}>
          <SectionHeader>
            Field Changes ({p.field_diffs.length})
          </SectionHeader>
          {multiCandidate
            ? groupByCandidate(p.field_diffs).map(({ candidateId, rows }) => (
                <div key={candidateId}>
                  <CandidateGroupHeader label={candidateMap[candidateId] ?? candidateId} />
                  {rows.map((diff, i) => (
                    <FieldDiffRow key={`${diff.path}-${i}`} diff={diff} />
                  ))}
                </div>
              ))
            : p.field_diffs.map((diff, i) => (
                <FieldDiffRow key={`${diff.path}-${i}`} diff={diff} />
              ))}
        </div>
      )}

      {/* Metric diffs — grouped by evidence class (predicted last, visually distinct) */}
      {p.metric_diffs.length > 0 && (
        <div data-testid={`version-compare-card-${card.card_id}-metric-diffs`}>
          <SectionHeader>Metric Differences</SectionHeader>
          {multiCandidate
            ? groupByCandidate(p.metric_diffs).map(({ candidateId, rows }) => (
                <div key={candidateId}>
                  <CandidateGroupHeader label={candidateMap[candidateId] ?? candidateId} />
                  <MetricDiffSection diffs={rows} />
                </div>
              ))
            : <MetricDiffSection diffs={p.metric_diffs} />}
        </div>
      )}

      {/* Risk diffs */}
      {p.risk_diffs && p.risk_diffs.length > 0 && (
        <div data-testid={`version-compare-card-${card.card_id}-risk-diffs`}>
          <SectionHeader>Risk Changes</SectionHeader>
          {multiCandidate
            ? groupByCandidate(p.risk_diffs).map(({ candidateId, rows }) => (
                <div key={candidateId}>
                  <CandidateGroupHeader label={candidateMap[candidateId] ?? candidateId} />
                  {rows.map((diff, i) => (
                    <RiskDiffRow key={i} diff={diff} />
                  ))}
                </div>
              ))
            : p.risk_diffs.map((diff, i) => (
                <RiskDiffRow key={i} diff={diff} />
              ))}
        </div>
      )}

      {/* Readiness diffs */}
      {p.readiness_diffs.length > 0 && (
        <div data-testid={`version-compare-card-${card.card_id}-readiness-diffs`}>
          <SectionHeader>Readiness Gate Changes</SectionHeader>
          {multiCandidate
            ? groupByCandidate(p.readiness_diffs).map(({ candidateId, rows }) => (
                <div key={candidateId}>
                  <CandidateGroupHeader label={candidateMap[candidateId] ?? candidateId} />
                  {rows.map((diff, i) => (
                    <ReadinessDiffRow key={i} diff={diff} />
                  ))}
                </div>
              ))
            : p.readiness_diffs.map((diff, i) => (
                <ReadinessDiffRow key={i} diff={diff} />
              ))}
        </div>
      )}

      {/* Servant recommendation — always with trader-decides attribution */}
      {p.recommendation?.rationale && (
        <div
          data-testid={`version-compare-card-${card.card_id}-recommendation`}
          style={{
            background: "#f8fafc",
            border: "1px solid #e2e8f0",
            borderRadius: 6,
            padding: "8px 10px",
            display: "flex",
            flexDirection: "column",
            gap: 4,
          }}
        >
          <div style={{ fontSize: 11, fontWeight: 700, color: "#6b7280", textTransform: "uppercase" }}>
            Servant Recommendation
          </div>
          <div style={{ fontSize: 12, color: "#374151" }}>{p.recommendation.rationale}</div>
          {p.recommendation.confidence != null && (
            <div style={{ fontSize: 11, color: "#9ca3af" }}>
              Confidence: {Math.round(p.recommendation.confidence * 100)}%
            </div>
          )}
          {p.recommendation.limitations && p.recommendation.limitations.length > 0 && (
            <div style={{ fontSize: 11, color: "#d97706" }}>
              Limitations: {p.recommendation.limitations.join("; ")}
            </div>
          )}
          <div
            data-testid={`version-compare-card-${card.card_id}-decision-authority`}
            style={{
              fontSize: 11,
              color: "#6b7280",
              fontStyle: "italic",
              marginTop: 2,
            }}
          >
            Decision authority: Trader
          </div>
        </div>
      )}

      {/* Ask Servant button */}
      {onContinueDiscussion && (
        <div>
          <button
            data-testid={`version-compare-card-${card.card_id}-discuss`}
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
