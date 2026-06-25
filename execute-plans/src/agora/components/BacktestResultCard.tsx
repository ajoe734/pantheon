import React from "react";
import type { WorkshopCard } from "@/lib/bff-v1/agora/workshops";
import type { PayloadResearchResult, ResearchMetric } from "./workshop-card-types";

export interface BacktestResultCardProps {
  card: WorkshopCard;
  onContinueDiscussion?: (cardId: string) => void;
}

const OUTCOME_STYLE: Record<string, { color: string; bg: string }> = {
  pass: { color: "#16a34a", bg: "#f0fdf4" },
  fail: { color: "#dc2626", bg: "#fef2f2" },
  inconclusive: { color: "#ca8a04", bg: "#fefce8" },
  pending: { color: "#6b7280", bg: "#f9fafb" },
};

const BACKEND_MODE_LABEL: Record<string, string> = {
  real: "Real",
  fixture: "Fixture",
  stub: "Stub",
};

const BACKEND_MODE_COLOR: Record<string, string> = {
  real: "#374151",
  fixture: "#d97706",
  stub: "#9ca3af",
};

const SEVERITY_COLOR: Record<string, string> = {
  info: "#6b7280",
  watch: "#2563eb",
  warning: "#d97706",
  high: "#dc2626",
  critical: "#991b1b",
};

const GATE_RESULT_COLOR: Record<string, string> = {
  pass: "#16a34a",
  fail: "#dc2626",
  not_applicable: "#9ca3af",
  not_evaluated: "#6b7280",
};

const METRIC_CATEGORY_LABEL: Record<string, string> = {
  performance: "Performance",
  risk: "Risk",
  cost: "Cost",
  capacity: "Capacity",
  robustness: "Robustness",
  calibration: "Calibration",
  data_quality: "Data Quality",
};

function groupMetricsByCategory(metrics: ResearchMetric[]): Map<string, ResearchMetric[]> {
  const groups = new Map<string, ResearchMetric[]>();
  for (const m of metrics) {
    const existing = groups.get(m.category);
    if (existing) {
      existing.push(m);
    } else {
      groups.set(m.category, [m]);
    }
  }
  return groups;
}

export function BacktestResultCard({
  card,
  onContinueDiscussion,
}: BacktestResultCardProps): JSX.Element {
  const payload = card.payload as unknown as PayloadResearchResult;
  const outcomeStyle = OUTCOME_STYLE[payload.outcome] ?? OUTCOME_STYLE.pending;
  const metricGroups = payload.metrics && payload.metrics.length > 0
    ? groupMetricsByCategory(payload.metrics)
    : null;

  return (
    <div
      data-testid={`backtest-result-card-${card.card_id}`}
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
        <div style={{ display: "flex", gap: 6, alignItems: "center", marginLeft: 8, flexShrink: 0 }}>
          <span
            data-testid={`backtest-result-card-${card.card_id}-outcome`}
            style={{
              fontSize: 12,
              fontWeight: 600,
              color: outcomeStyle.color,
              padding: "2px 8px",
              borderRadius: 4,
              border: `1px solid ${outcomeStyle.color}`,
              background: outcomeStyle.bg,
            }}
          >
            {payload.outcome}
          </span>
          {payload.backend && (
            <span
              data-testid={`backtest-result-card-${card.card_id}-backend-mode`}
              style={{
                fontSize: 11,
                fontWeight: 600,
                color: BACKEND_MODE_COLOR[payload.backend.mode] ?? "#6b7280",
                padding: "2px 6px",
                borderRadius: 3,
                border: `1px solid ${BACKEND_MODE_COLOR[payload.backend.mode] ?? "#e5e7eb"}`,
              }}
            >
              {payload.backend.effective} · {BACKEND_MODE_LABEL[payload.backend.mode] ?? payload.backend.mode}
            </span>
          )}
        </div>
      </div>

      {metricGroups && (
        <div data-testid={`backtest-result-card-${card.card_id}-metrics`}>
          {Array.from(metricGroups.entries()).map(([category, metrics]) => (
            <div key={category} style={{ marginBottom: 6 }}>
              <div
                style={{
                  fontSize: 11,
                  fontWeight: 600,
                  color: "#6b7280",
                  textTransform: "uppercase",
                  letterSpacing: "0.05em",
                  marginBottom: 3,
                }}
              >
                {METRIC_CATEGORY_LABEL[category] ?? category}
              </div>
              {metrics.map((m, i) => (
                <div
                  key={i}
                  data-testid={`backtest-result-card-${card.card_id}-metric-${m.name.replace(/\s+/g, "-").toLowerCase()}`}
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    fontSize: 12,
                    padding: "2px 0",
                  }}
                >
                  <span style={{ color: "#374151" }}>{m.name}</span>
                  <span
                    style={{
                      fontWeight: 500,
                      color: GATE_RESULT_COLOR[m.gate_result] ?? "#6b7280",
                    }}
                  >
                    {m.value}{m.unit ? ` ${m.unit}` : ""}
                    <span
                      style={{
                        marginLeft: 6,
                        fontSize: 11,
                        color: GATE_RESULT_COLOR[m.gate_result] ?? "#9ca3af",
                      }}
                    >
                      {m.gate_result}
                    </span>
                  </span>
                </div>
              ))}
            </div>
          ))}
        </div>
      )}

      {payload.findings && payload.findings.length > 0 && (
        <div data-testid={`backtest-result-card-${card.card_id}-findings`}>
          <div style={{ fontSize: 12, fontWeight: 600, color: "#374151", marginBottom: 4 }}>
            Findings
          </div>
          {payload.findings.map((f) => (
            <div
              key={f.finding_id}
              style={{
                fontSize: 12,
                padding: "3px 8px",
                borderLeft: `3px solid ${SEVERITY_COLOR[f.severity] ?? "#e5e7eb"}`,
                marginBottom: 3,
                color: "#374151",
              }}
            >
              <span
                style={{
                  fontWeight: 600,
                  color: SEVERITY_COLOR[f.severity] ?? "#6b7280",
                  marginRight: 6,
                  fontSize: 11,
                  textTransform: "uppercase",
                }}
              >
                {f.severity}
              </span>
              {f.summary}
            </div>
          ))}
        </div>
      )}

      {payload.warnings && payload.warnings.length > 0 && (
        <div
          data-testid={`backtest-result-card-${card.card_id}-warnings`}
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
          data-testid={`backtest-result-card-${card.card_id}-blocking`}
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

      {payload.gate_impacts && payload.gate_impacts.length > 0 && (
        <div
          data-testid={`backtest-result-card-${card.card_id}-gate-impacts`}
          style={{ fontSize: 12, color: "#374151" }}
        >
          <span style={{ fontWeight: 500 }}>Gate impacts: </span>
          {payload.gate_impacts.join(", ")}
        </div>
      )}

      {payload.data_cutoff && (
        <div
          data-testid={`backtest-result-card-${card.card_id}-data-cutoff`}
          style={{ fontSize: 11, color: "#9ca3af" }}
        >
          Data cutoff: {payload.data_cutoff}
        </div>
      )}

      <div style={{ display: "flex", gap: 8, marginTop: 2 }}>
        {onContinueDiscussion && (
          <button
            data-testid={`backtest-result-card-${card.card_id}-discuss`}
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
