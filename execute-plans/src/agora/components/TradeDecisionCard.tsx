/**
 * TradeDecisionCard — standalone card for a single TradingDecisionEvent.
 *
 * Shows all D2 required fields: event kind, subject, trigger, confidence,
 * probability, EV breakdown, rationale, risk notes, evidence refs,
 * invalidation state, suggested action/size, position snapshot,
 * and trader decision buttons (approve/reject/defer/modify).
 *
 * Approve/modify creates a TradingIntent via AG-BE-TR-002; no order is placed.
 * Confidence and probability are displayed as distinct fields per D2 spec.
 */

import React, { useState } from "react";
import {
  decideOnEvent,
  type TradingDecisionEvent,
  type DecisionChoice,
} from "@/lib/bff-v1/agora/tradingRoom";

function newUUID(): string {
  return crypto.randomUUID();
}

// ── Constants ─────────────────────────────────────────────────────────────────

const EVENT_KIND_LABEL: Record<string, string> = {
  entry: "Entry",
  add: "Add",
  reduce: "Reduce",
  exit: "Exit",
  review: "Review",
};

const EVENT_KIND_COLOR: Record<string, { bg: string; text: string; border: string }> = {
  entry: { bg: "#f0fdf4", text: "#15803d", border: "#86efac" },
  add: { bg: "#eff6ff", text: "#1d4ed8", border: "#93c5fd" },
  reduce: { bg: "#fefce8", text: "#a16207", border: "#fde047" },
  exit: { bg: "#fef2f2", text: "#b91c1c", border: "#fca5a5" },
  review: { bg: "#faf5ff", text: "#7e22ce", border: "#d8b4fe" },
};

const STATE_LABEL: Record<string, string> = {
  approaching: "Approaching",
  triggered: "Triggered",
  pending_review: "Pending Review",
  decided: "Decided",
  expired: "Expired",
  invalidated: "Invalidated",
  superseded: "Superseded",
};

const SEVERITY_COLOR: Record<string, { bg: string; text: string }> = {
  info: { bg: "#f0f9ff", text: "#0369a1" },
  watch: { bg: "#fefce8", text: "#a16207" },
  warning: { bg: "#fff7ed", text: "#c2410c" },
  high: { bg: "#fef2f2", text: "#dc2626" },
  critical: { bg: "#fef2f2", text: "#991b1b" },
};

const INVALIDATION_COLOR: Record<string, string> = {
  valid: "#16a34a",
  watch: "#d97706",
  invalidated: "#dc2626",
};

// ── Sub-components ────────────────────────────────────────────────────────────

interface SectionHeadingProps {
  label: string;
}

function SectionHeading({ label }: SectionHeadingProps): JSX.Element {
  return (
    <div
      style={{
        fontSize: 11,
        fontWeight: 600,
        color: "#64748b",
        textTransform: "uppercase",
        letterSpacing: "0.04em",
        marginBottom: 4,
      }}
    >
      {label}
    </div>
  );
}

// ── TradeDecisionCard ─────────────────────────────────────────────────────────

type DecisionCallState = "idle" | "loading" | "success" | "error";

export interface TradeDecisionCardProps {
  event: TradingDecisionEvent;
  /**
   * ETag from the GET that loaded this event — forwarded as If-Match to decideOnEvent.
   * Required by AG-BE-TR-002; the route rejects writes that omit If-Match.
   */
  etag?: string;
  /** Called after a successful trader decision. */
  onDecisionRecorded?: (choice: DecisionChoice, eventId: string) => void;
}

export function TradeDecisionCard({
  event,
  etag,
  onDecisionRecorded,
}: TradeDecisionCardProps): JSX.Element {
  const [callState, setCallState] = useState<DecisionCallState>("idle");
  const [callError, setCallError] = useState<string | null>(null);
  const [decidedChoice, setDecidedChoice] = useState<DecisionChoice | null>(null);

  const kindStyle = EVENT_KIND_COLOR[event.event_kind] ?? {
    bg: "#f8fafc",
    text: "#475569",
    border: "#e2e8f0",
  };

  const canDecide =
    callState !== "loading" &&
    callState !== "success" &&
    (event.state === "pending_review" ||
      event.state === "triggered" ||
      event.state === "approaching");

  async function handleDecide(choice: DecisionChoice) {
    setCallState("loading");
    setCallError(null);
    try {
      await decideOnEvent(
        event.decision_event_id,
        { decision: choice },
        {
          ifMatch: etag,
          idempotencyKey: newUUID(),
          requestId: newUUID(),
        },
      );
      setDecidedChoice(choice);
      setCallState("success");
      onDecisionRecorded?.(choice, event.decision_event_id);
    } catch (err) {
      setCallError(err instanceof Error ? err.message : "Decision failed");
      setCallState("error");
    }
  }

  const ev = event;
  const invalidationColor =
    INVALIDATION_COLOR[ev.invalidation.current_state] ?? "#64748b";

  return (
    <div
      data-testid={`trade-decision-card-${ev.decision_event_id}`}
      style={{
        border: `1px solid ${kindStyle.border}`,
        borderRadius: 8,
        background: "#fff",
        display: "flex",
        flexDirection: "column",
        gap: 0,
        overflow: "hidden",
      }}
    >
      {/* Header row */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "10px 14px",
          background: kindStyle.bg,
          borderBottom: `1px solid ${kindStyle.border}`,
        }}
      >
        <span
          data-testid={`trade-decision-card-kind-${ev.decision_event_id}`}
          style={{
            fontSize: 11,
            fontWeight: 700,
            color: kindStyle.text,
            padding: "2px 8px",
            borderRadius: 4,
            border: `1px solid ${kindStyle.border}`,
            background: "#fff",
            textTransform: "uppercase",
          }}
        >
          {EVENT_KIND_LABEL[ev.event_kind] ?? ev.event_kind}
        </span>

        <span
          style={{ fontWeight: 700, fontSize: 15, color: "#0f172a" }}
          data-testid={`trade-decision-card-symbol-${ev.decision_event_id}`}
        >
          {ev.subject.symbol}
        </span>

        {ev.subject.asset_class && (
          <span style={{ fontSize: 12, color: "#64748b" }}>
            {ev.subject.asset_class}
          </span>
        )}

        {ev.subject.venue && (
          <span style={{ fontSize: 12, color: "#94a3b8" }}>{ev.subject.venue}</span>
        )}

        <div style={{ flex: 1 }} />

        <span
          data-testid={`trade-decision-card-state-${ev.decision_event_id}`}
          style={{ fontSize: 12, color: "#64748b" }}
        >
          {STATE_LABEL[ev.state] ?? ev.state}
        </span>
      </div>

      {/* Body */}
      <div style={{ padding: "12px 14px", display: "flex", flexDirection: "column", gap: 12 }}>

        {/* Trigger */}
        {ev.trigger && (
          <div data-testid={`trade-decision-card-trigger-${ev.decision_event_id}`}>
            <SectionHeading label="Trigger" />
            <div style={{ fontSize: 13, color: "#374151" }}>
              {ev.trigger.summary}
              {ev.trigger.distance_to_trigger != null && (
                <span style={{ marginLeft: 8, color: "#64748b", fontSize: 12 }}>
                  Distance: {ev.trigger.distance_to_trigger.toFixed(4)}
                </span>
              )}
            </div>
          </div>
        )}

        {/* Signal Quality: Confidence (≠ Probability) */}
        <div
          data-testid={`trade-decision-card-confidence-${ev.decision_event_id}`}
          style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}
        >
          <div>
            <SectionHeading label="Confidence (evidence quality)" />
            <div style={{ fontSize: 13, color: "#0f172a" }}>
              <strong>{(ev.confidence.value * 100).toFixed(0)}%</strong>
              <span style={{ fontSize: 12, color: "#64748b", marginLeft: 6 }}>
                {ev.confidence.basis}
              </span>
            </div>
            <div
              data-testid={`trade-decision-card-calibration-${ev.decision_event_id}`}
              style={{ fontSize: 12, color: "#64748b" }}
            >
              Calibration: {ev.confidence.calibration_state}
            </div>
            {ev.confidence.sample_size != null && (
              <div style={{ fontSize: 12, color: "#94a3b8" }}>
                n={ev.confidence.sample_size}
              </div>
            )}
          </div>

          {/* Probability (distinct from confidence per D4 spec) */}
          <div data-testid={`trade-decision-card-probability-${ev.decision_event_id}`}>
            <SectionHeading label="Probability (outcome forecast)" />
            <div style={{ fontSize: 13, color: "#0f172a" }}>
              <strong>{(ev.probability.value * 100).toFixed(0)}%</strong>
              {ev.probability.ci_lower != null && ev.probability.ci_upper != null && (
                <span
                  data-testid={`trade-decision-card-probability-ci-${ev.decision_event_id}`}
                  style={{ fontSize: 12, color: "#64748b", marginLeft: 6 }}
                >
                  [{(ev.probability.ci_lower * 100).toFixed(0)}%–{(ev.probability.ci_upper * 100).toFixed(0)}%]
                </span>
              )}
            </div>
            <div style={{ fontSize: 12, color: "#64748b" }}>
              {ev.probability.target_outcome} · {ev.probability.horizon}
            </div>
          </div>
        </div>

        {/* Expected Value — gross/cost/net/downside per D2 spec */}
        <div data-testid={`trade-decision-card-ev-${ev.decision_event_id}`}>
          <SectionHeading label={`Expected Value (${ev.expected_value.horizon}, ${ev.expected_value.unit})`} />
          <div style={{ display: "flex", gap: 20, fontSize: 13, flexWrap: "wrap" }}>
            <div>
              <span style={{ color: "#64748b", fontSize: 12 }}>Gross </span>
              <span style={{ fontWeight: 500 }}>
                {ev.expected_value.gross >= 0 ? "+" : ""}
                {ev.expected_value.gross.toFixed(4)}
              </span>
            </div>
            <div>
              <span style={{ color: "#64748b", fontSize: 12 }}>Cost </span>
              <span style={{ fontWeight: 500 }}>
                {ev.expected_value.cost.toFixed(4)}
              </span>
            </div>
            <div>
              <span style={{ color: "#64748b", fontSize: 12 }}>Net </span>
              <span
                data-testid={`trade-decision-card-ev-net-${ev.decision_event_id}`}
                style={{
                  fontWeight: 600,
                  color: ev.expected_value.net >= 0 ? "#16a34a" : "#dc2626",
                }}
              >
                {ev.expected_value.net >= 0 ? "+" : ""}
                {ev.expected_value.net.toFixed(4)}
              </span>
            </div>
            <div>
              <span style={{ color: "#64748b", fontSize: 12 }}>Downside </span>
              <span style={{ fontWeight: 500, color: "#dc2626" }}>
                {ev.expected_value.downside.toFixed(4)}
              </span>
            </div>
          </div>
        </div>

        {/* Suggested Action */}
        <div data-testid={`trade-decision-card-suggested-${ev.decision_event_id}`}>
          <SectionHeading label="Suggested Action (non-binding)" />
          <div style={{ display: "flex", alignItems: "center", gap: 12, fontSize: 13 }}>
            <span style={{ fontWeight: 600, textTransform: "capitalize" }}>
              {ev.suggested_action}
            </span>
            {ev.suggested_size && (
              <>
                {ev.suggested_size.size_hint && (
                  <span style={{ color: "#64748b" }}>
                    Size: {ev.suggested_size.size_hint}
                  </span>
                )}
                {ev.suggested_size.portfolio_pct != null && (
                  <span style={{ color: "#64748b" }}>
                    {(ev.suggested_size.portfolio_pct * 100).toFixed(1)}% portfolio
                  </span>
                )}
                <span style={{ fontSize: 11, color: "#94a3b8" }}>non-binding</span>
              </>
            )}
          </div>
        </div>

        {/* Rationale */}
        {ev.rationale.length > 0 && (
          <div data-testid={`trade-decision-card-rationale-${ev.decision_event_id}`}>
            <SectionHeading label="Rationale" />
            <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
              {ev.rationale.map((r, i) => (
                <div key={i} style={{ display: "flex", gap: 8, fontSize: 13 }}>
                  <span
                    style={{
                      minWidth: 36,
                      fontSize: 12,
                      color: "#94a3b8",
                      paddingTop: 1,
                    }}
                  >
                    {(r.confidence * 100).toFixed(0)}%
                  </span>
                  <span style={{ color: "#374151", lineHeight: 1.4 }}>{r.claim}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Risk Notes */}
        {ev.risk_notes.length > 0 && (
          <div data-testid={`trade-decision-card-risk-notes-${ev.decision_event_id}`}>
            <SectionHeading label="Risk Notes" />
            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              {ev.risk_notes.map((rn, i) => {
                const color = SEVERITY_COLOR[rn.severity] ?? SEVERITY_COLOR.info;
                return (
                  <div
                    key={i}
                    style={{
                      padding: "4px 10px",
                      borderRadius: 4,
                      background: color.bg,
                      fontSize: 12,
                      color: color.text,
                    }}
                  >
                    <span style={{ fontWeight: 600 }}>[{rn.severity}]</span>{" "}
                    <span style={{ fontWeight: 500 }}>{rn.domain}:</span> {rn.summary}
                    {rn.mitigation && (
                      <span style={{ color: "#64748b" }}> — {rn.mitigation}</span>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* Evidence Refs */}
        {ev.evidence_refs.length > 0 && (
          <div data-testid={`trade-decision-card-evidence-${ev.decision_event_id}`}>
            <SectionHeading label={`Evidence (${ev.evidence_refs.length})`} />
            <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
              {ev.evidence_refs.map((ref, i) => (
                <div key={i} style={{ fontSize: 12, color: "#475569" }}>
                  <span style={{ color: "#94a3b8" }}>{ref.ref_type}</span>{" "}
                  {ref.ref_id}
                  {ref.summary ? ` — ${ref.summary}` : null}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Invalidation */}
        <div data-testid={`trade-decision-card-invalidation-${ev.decision_event_id}`}>
          <SectionHeading label="Invalidation" />
          <div style={{ fontSize: 13 }}>
            State:{" "}
            <span style={{ fontWeight: 600, color: invalidationColor }}>
              {ev.invalidation.current_state}
            </span>
          </div>
          {ev.invalidation.conditions.length > 0 && (
            <ul style={{ margin: "4px 0 0 16px", padding: 0, fontSize: 12, color: "#64748b" }}>
              {ev.invalidation.conditions.map((c, i) => (
                <li key={i}>{c}</li>
              ))}
            </ul>
          )}
        </div>

        {/* Position snapshot (for add/reduce/exit/review events) */}
        {ev.position_snapshot && Object.keys(ev.position_snapshot).length > 0 && (
          <div data-testid={`trade-decision-card-position-${ev.decision_event_id}`}>
            <SectionHeading label="Position Snapshot" />
            <div style={{ fontSize: 12, color: "#475569", fontFamily: "monospace" }}>
              {JSON.stringify(ev.position_snapshot, null, 2)}
            </div>
          </div>
        )}

        {/* Data cutoff + no-order-route proof */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          {ev.data_cutoff && (
            <span style={{ fontSize: 11, color: "#94a3b8" }}>
              Data cutoff: {ev.data_cutoff}
            </span>
          )}
          <span
            data-testid={`trade-decision-card-no-order-route-${ev.decision_event_id}`}
            style={{ fontSize: 11, color: "#22c55e", fontWeight: 500 }}
          >
            {ev.no_order_route_proof}
          </span>
        </div>

        {/* Governed TradingIntent notice */}
        <div
          data-testid={`trade-decision-card-intent-notice-${ev.decision_event_id}`}
          style={{
            padding: "6px 10px",
            borderRadius: 4,
            background: "#f0f9ff",
            border: "1px solid #bae6fd",
            fontSize: 11,
            color: "#0369a1",
          }}
        >
          Approve or Modify creates a governed TradingIntent via AG-BE-TR-002.
          No order is placed. Canary/live promotion requires separate governance review.
        </div>

        {/* Trader Decision Buttons */}
        <div
          data-testid={`trade-decision-card-actions-${ev.decision_event_id}`}
          style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}
        >
          {callState === "success" ? (
            <span
              data-testid={`trade-decision-card-confirmed-${ev.decision_event_id}`}
              style={{ fontSize: 12, color: "#16a34a", fontWeight: 500 }}
            >
              Decision recorded: {decidedChoice}
            </span>
          ) : (
            <>
              <span style={{ fontSize: 12, color: "#64748b", marginRight: 2 }}>
                Trader decision:
              </span>
              {(["approve", "reject", "defer", "modify"] as DecisionChoice[]).map(
                (choice) => (
                  <button
                    key={choice}
                    data-testid={`trade-decision-card-decide-${choice}-${ev.decision_event_id}`}
                    disabled={!canDecide}
                    onClick={() => handleDecide(choice)}
                    style={{
                      padding: "4px 12px",
                      fontSize: 12,
                      border: "1px solid #e2e8f0",
                      borderRadius: 4,
                      cursor: canDecide ? "pointer" : "not-allowed",
                      background:
                        choice === "approve"
                          ? "#f0fdf4"
                          : choice === "reject"
                          ? "#fef2f2"
                          : "#fff",
                      color:
                        choice === "approve"
                          ? "#16a34a"
                          : choice === "reject"
                          ? "#dc2626"
                          : "#475569",
                      opacity: canDecide ? 1 : 0.5,
                      fontWeight: choice === "approve" || choice === "reject" ? 500 : 400,
                    }}
                  >
                    {choice.charAt(0).toUpperCase() + choice.slice(1)}
                  </button>
                ),
              )}
              {callState === "loading" && (
                <span
                  data-testid={`trade-decision-card-loading-${ev.decision_event_id}`}
                  style={{ fontSize: 12, color: "#94a3b8" }}
                >
                  Sending…
                </span>
              )}
              {callState === "error" && callError && (
                <span
                  data-testid={`trade-decision-card-error-${ev.decision_event_id}`}
                  style={{ fontSize: 12, color: "#dc2626" }}
                >
                  {callError}
                </span>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
