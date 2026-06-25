/**
 * CandidateReviewDrawer — drawer panel for reviewing candidates in a pool.
 *
 * Displays candidates with A2 score decomposition per spec §10:
 *   Rank · Candidate title · Effective Score · Confidence · Top 3 positive
 *   drivers · Top 2 penalties · Data quality badge · Status/band
 *
 * Clicking a candidate's score row opens the full decomposition panel showing:
 *   raw_score, penalty_score, evidence_confidence, effective_score, recipe
 *   version, and each component's normalized value, weight, contribution,
 *   evidence refs, and missing/cap reason.
 *
 * Candidate decisions use the AG-BE-CP-001 canonical route; the Trading Room
 * does not create a duplicate candidate state machine.
 * Rejected candidates are retained as negative examples (not deleted).
 *
 * No order routing, no capital binding — review/score surface only.
 */

import React, { useEffect, useState } from "react";
import {
  getCandidatePoolScore,
  listCandidatePoolMembers,
  reviewCandidateMember,
  type CandidateScoreResult,
  type CandidateScoreComponent,
  type CandidatePoolMember,
  type CandidateReviewDecision,
} from "@/lib/bff-v1/agora/candidatePool";

function newUUID(): string {
  return crypto.randomUUID();
}

// ── Constants ─────────────────────────────────────────────────────────────────

const BAND_STYLE: Record<string, { bg: string; text: string; border: string }> = {
  priority_review: { bg: "#f0fdf4", text: "#15803d", border: "#86efac" },
  discuss: { bg: "#eff6ff", text: "#1d4ed8", border: "#93c5fd" },
  needs_research: { bg: "#fefce8", text: "#a16207", border: "#fde047" },
  park: { bg: "#f8fafc", text: "#64748b", border: "#cbd5e1" },
  suppressed: { bg: "#fef2f2", text: "#b91c1c", border: "#fca5a5" },
};

const CATEGORY_COLOR: Record<string, string> = {
  alpha: "#1d4ed8",
  confidence: "#0369a1",
  liquidity: "#0891b2",
  risk: "#dc2626",
  execution: "#7c3aed",
  data_quality: "#d97706",
  custom: "#374151",
};

const REVIEW_DECISION_LABEL: Record<CandidateReviewDecision, string> = {
  approve_for_monitoring: "Add to Monitoring",
  send_to_shadow: "Send to Shadow",
  needs_more_research: "Needs Research",
  park: "Park",
  reject: "Reject",
};

const REVIEW_DECISION_COLOR: Record<
  CandidateReviewDecision,
  { bg: string; text: string; border: string }
> = {
  approve_for_monitoring: { bg: "#f0fdf4", text: "#16a34a", border: "#86efac" },
  send_to_shadow: { bg: "#eff6ff", text: "#1d4ed8", border: "#93c5fd" },
  needs_more_research: { bg: "#fefce8", text: "#a16207", border: "#fde047" },
  park: { bg: "#f8fafc", text: "#64748b", border: "#cbd5e1" },
  reject: { bg: "#fef2f2", text: "#b91c1c", border: "#fca5a5" },
};

// ── Score Decomposition Panel ─────────────────────────────────────────────────

interface ScoreDecompositionProps {
  score: CandidateScoreResult;
  candidateTitle: string;
  onClose: () => void;
}

function ScoreDecompositionPanel({
  score,
  candidateTitle,
  onClose,
}: ScoreDecompositionProps): JSX.Element {
  const positiveComponents = score.components.filter(
    (c) => c.direction === "higher_better",
  );
  const penaltyComponents = score.components.filter(
    (c) => c.direction === "lower_better",
  );

  return (
    <div
      data-testid={`score-decomposition-${score.candidate_id}`}
      style={{
        position: "fixed",
        top: 0,
        right: 0,
        width: 480,
        height: "100%",
        background: "#fff",
        boxShadow: "-4px 0 24px rgba(0,0,0,0.12)",
        zIndex: 1100,
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
      }}
    >
      {/* Header */}
      <div
        style={{
          padding: "14px 16px",
          borderBottom: "1px solid #e2e8f0",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          flexShrink: 0,
        }}
      >
        <div>
          <div style={{ fontWeight: 700, fontSize: 15, color: "#0f172a" }}>
            Score Decomposition
          </div>
          <div style={{ fontSize: 12, color: "#64748b", marginTop: 2 }}>
            {candidateTitle}
          </div>
          <div style={{ fontSize: 11, color: "#94a3b8", marginTop: 1 }}>
            Recipe: {score.recipe_id} v{score.recipe_version}
          </div>
        </div>
        <button
          data-testid={`score-decomposition-close-${score.candidate_id}`}
          onClick={onClose}
          style={{
            background: "none",
            border: "none",
            cursor: "pointer",
            fontSize: 18,
            color: "#64748b",
            lineHeight: 1,
            padding: 4,
          }}
          aria-label="Close decomposition"
        >
          ×
        </button>
      </div>

      {/* Score summary */}
      <div
        data-testid={`score-summary-${score.candidate_id}`}
        style={{
          padding: "12px 16px",
          borderBottom: "1px solid #f1f5f9",
          display: "grid",
          gridTemplateColumns: "repeat(4, 1fr)",
          gap: 8,
          flexShrink: 0,
        }}
      >
        {[
          { label: "Raw Score", value: score.raw_score.toFixed(1), testId: "raw-score" },
          { label: "Penalty", value: score.penalty_score.toFixed(1), testId: "penalty-score" },
          {
            label: "Confidence",
            value: `${(score.evidence_confidence * 100).toFixed(0)}%`,
            testId: "evidence-confidence",
          },
          {
            label: "Effective Score",
            value: score.effective_score.toFixed(1),
            testId: "effective-score",
          },
        ].map(({ label, value, testId }) => (
          <div key={testId} style={{ textAlign: "center" }}>
            <div style={{ fontSize: 11, color: "#64748b" }}>{label}</div>
            <div
              data-testid={`score-summary-${testId}-${score.candidate_id}`}
              style={{ fontSize: 18, fontWeight: 700, color: "#0f172a" }}
            >
              {value}
            </div>
          </div>
        ))}
      </div>

      {/* Components */}
      <div style={{ flex: 1, overflowY: "auto", padding: "12px 16px" }}>
        {positiveComponents.length > 0 && (
          <div style={{ marginBottom: 16 }}>
            <div
              style={{
                fontSize: 11,
                fontWeight: 700,
                color: "#64748b",
                textTransform: "uppercase",
                letterSpacing: "0.04em",
                marginBottom: 8,
              }}
            >
              Positive Components
            </div>
            {positiveComponents.map((c) => (
              <ComponentRow key={c.component_id} component={c} candidateId={score.candidate_id} />
            ))}
          </div>
        )}

        {penaltyComponents.length > 0 && (
          <div style={{ marginBottom: 16 }}>
            <div
              style={{
                fontSize: 11,
                fontWeight: 700,
                color: "#64748b",
                textTransform: "uppercase",
                letterSpacing: "0.04em",
                marginBottom: 8,
              }}
            >
              Penalty Components
            </div>
            {penaltyComponents.map((c) => (
              <ComponentRow key={c.component_id} component={c} candidateId={score.candidate_id} />
            ))}
          </div>
        )}

        {score.blockers.length > 0 && (
          <div
            data-testid={`score-blockers-${score.candidate_id}`}
            style={{
              padding: "8px 12px",
              borderRadius: 4,
              background: "#fef2f2",
              border: "1px solid #fecaca",
              marginBottom: 12,
            }}
          >
            <div
              style={{
                fontSize: 11,
                fontWeight: 700,
                color: "#991b1b",
                marginBottom: 4,
                textTransform: "uppercase",
              }}
            >
              Score Blockers / Caps
            </div>
            {score.blockers.map((b, i) => (
              <div key={i} style={{ fontSize: 12, color: "#991b1b" }}>
                {b}
              </div>
            ))}
          </div>
        )}

        <div style={{ fontSize: 11, color: "#94a3b8", marginTop: 8 }}>
          Data cutoff: {score.data_cutoff}
        </div>
      </div>
    </div>
  );
}

interface ComponentRowProps {
  component: CandidateScoreComponent;
  candidateId: string;
}

function ComponentRow({ component: c, candidateId }: ComponentRowProps): JSX.Element {
  const catColor = CATEGORY_COLOR[c.category] ?? "#374151";
  const isMissing = c.normalized_value === null;

  return (
    <div
      data-testid={`component-row-${candidateId}-${c.component_id}`}
      style={{
        padding: "8px 10px",
        borderRadius: 6,
        background: "#f8fafc",
        marginBottom: 6,
        border: isMissing ? "1px solid #fde68a" : "1px solid #f1f5f9",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <span style={{ fontWeight: 600, fontSize: 12, color: catColor }}>{c.label}</span>
        <span
          data-testid={`component-contribution-${candidateId}-${c.component_id}`}
          style={{ fontWeight: 700, fontSize: 13, color: "#0f172a" }}
        >
          {c.contribution >= 0 ? "+" : ""}
          {c.contribution.toFixed(2)}
        </span>
      </div>

      <div
        style={{
          display: "flex",
          gap: 16,
          marginTop: 4,
          fontSize: 11,
          color: "#64748b",
          flexWrap: "wrap",
        }}
      >
        <span>
          Raw:{" "}
          <strong>{c.raw_value != null ? c.raw_value.toFixed(4) : "—"}</strong>
        </span>
        <span>
          Norm:{" "}
          <strong
            data-testid={`component-normalized-${candidateId}-${c.component_id}`}
          >
            {c.normalized_value != null ? c.normalized_value.toFixed(4) : "—"}
          </strong>
        </span>
        <span>
          Weight: <strong>{(c.weight * 100).toFixed(0)}%</strong>
        </span>
        <span>
          Transform: <strong>{c.transform}</strong>
        </span>
      </div>

      {(isMissing || c.missing_policy !== "score_zero") && (
        <div
          data-testid={`component-missing-${candidateId}-${c.component_id}`}
          style={{ fontSize: 11, color: "#d97706", marginTop: 2 }}
        >
          Policy: {c.missing_policy}
          {isMissing ? " (missing)" : ""}
        </div>
      )}

      {c.evidence_refs.length > 0 && (
        <div style={{ fontSize: 11, color: "#94a3b8", marginTop: 2 }}>
          Evidence: {c.evidence_refs.slice(0, 2).join(", ")}
          {c.evidence_refs.length > 2 ? ` +${c.evidence_refs.length - 2}` : ""}
        </div>
      )}
    </div>
  );
}

// ── Candidate Decision Buttons ────────────────────────────────────────────────

type DecisionCallState = "idle" | "loading" | "success" | "error";

interface CandidateDecisionRowProps {
  poolId: string;
  artifactId: string;
  candidateTitle: string;
  lifecycleState: string;
  /** ETag from the most recent listCandidatePoolMembers response — forwarded as If-Match. */
  poolEtag: string | null;
  onDecisionRecorded?: (artifactId: string, decision: CandidateReviewDecision) => void;
}

function CandidateDecisionRow({
  poolId,
  artifactId,
  candidateTitle,
  lifecycleState,
  poolEtag,
  onDecisionRecorded,
}: CandidateDecisionRowProps): JSX.Element {
  const [callState, setCallState] = useState<DecisionCallState>("idle");
  const [callError, setCallError] = useState<string | null>(null);
  const [confirmedDecision, setConfirmedDecision] = useState<CandidateReviewDecision | null>(null);
  const [rationale, setRationale] = useState("");
  const [pendingDecision, setPendingDecision] = useState<CandidateReviewDecision | null>(null);

  const canDecide =
    callState !== "loading" &&
    callState !== "success" &&
    lifecycleState !== "rejected";

  const requiresRationale =
    pendingDecision === "park" || pendingDecision === "reject";

  async function handleSubmit() {
    if (!pendingDecision) return;
    if (requiresRationale && !rationale.trim()) return;
    setCallState("loading");
    setCallError(null);
    try {
      await reviewCandidateMember(
        poolId,
        artifactId,
        {
          decision: pendingDecision,
          rationale: rationale.trim() || undefined,
          reviewed_by: "operator",
        },
        {
          ifMatch: poolEtag ?? undefined,
          idempotencyKey: newUUID(),
          requestId: newUUID(),
        },
      );
      setConfirmedDecision(pendingDecision);
      setCallState("success");
      setPendingDecision(null);
      setRationale("");
      onDecisionRecorded?.(artifactId, pendingDecision);
    } catch (err) {
      setCallError(err instanceof Error ? err.message : "Decision failed");
      setCallState("error");
    }
  }

  function handleDecisionClick(decision: CandidateReviewDecision) {
    if (!canDecide) return;
    setPendingDecision(decision);
    setRationale("");
    setCallError(null);
  }

  if (callState === "success" && confirmedDecision) {
    return (
      <span
        data-testid={`candidate-decision-confirmed-${artifactId}`}
        style={{ fontSize: 12, color: "#16a34a", fontWeight: 500 }}
      >
        {REVIEW_DECISION_LABEL[confirmedDecision]} recorded for {candidateTitle}
      </span>
    );
  }

  const DECISIONS: CandidateReviewDecision[] = [
    "approve_for_monitoring",
    "send_to_shadow",
    "needs_more_research",
    "park",
    "reject",
  ];

  return (
    <div
      data-testid={`candidate-decision-row-${artifactId}`}
      style={{ display: "flex", flexDirection: "column", gap: 6 }}
    >
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        {DECISIONS.map((decision) => {
          const style = REVIEW_DECISION_COLOR[decision];
          const isSelected = pendingDecision === decision;
          return (
            <button
              key={decision}
              data-testid={`candidate-decide-${decision}-${artifactId}`}
              disabled={!canDecide}
              onClick={() => handleDecisionClick(decision)}
              style={{
                padding: "3px 10px",
                fontSize: 11,
                borderRadius: 4,
                border: `1px solid ${isSelected ? style.border : "#e2e8f0"}`,
                background: isSelected ? style.bg : "#fff",
                color: isSelected ? style.text : "#475569",
                cursor: canDecide ? "pointer" : "not-allowed",
                opacity: canDecide ? 1 : 0.5,
                fontWeight: isSelected ? 600 : 400,
              }}
            >
              {REVIEW_DECISION_LABEL[decision]}
            </button>
          );
        })}
      </div>

      {pendingDecision && (
        <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
          {requiresRationale && (
            <input
              data-testid={`candidate-rationale-${artifactId}`}
              type="text"
              placeholder="Rationale required for park/reject"
              value={rationale}
              onChange={(e) => setRationale(e.target.value)}
              style={{
                flex: 1,
                padding: "4px 8px",
                fontSize: 12,
                border: "1px solid #cbd5e1",
                borderRadius: 4,
              }}
            />
          )}
          <button
            data-testid={`candidate-confirm-${artifactId}`}
            disabled={callState === "loading" || (requiresRationale && !rationale.trim())}
            onClick={handleSubmit}
            style={{
              padding: "4px 12px",
              fontSize: 12,
              borderRadius: 4,
              border: "1px solid #2563eb",
              background: "#eff6ff",
              color: "#1d4ed8",
              cursor: "pointer",
              fontWeight: 500,
            }}
          >
            {callState === "loading" ? "Saving…" : "Confirm"}
          </button>
          <button
            data-testid={`candidate-cancel-${artifactId}`}
            onClick={() => {
              setPendingDecision(null);
              setRationale("");
              setCallError(null);
            }}
            style={{
              padding: "4px 8px",
              fontSize: 12,
              border: "none",
              background: "none",
              cursor: "pointer",
              color: "#64748b",
            }}
          >
            Cancel
          </button>
        </div>
      )}

      {callState === "error" && callError && (
        <span
          data-testid={`candidate-decision-error-${artifactId}`}
          style={{ fontSize: 12, color: "#dc2626" }}
        >
          {callError}
        </span>
      )}
    </div>
  );
}

// ── CandidateReviewDrawer ─────────────────────────────────────────────────────

export interface CandidateReviewDrawerProps {
  poolId: string;
  /** Drawer open/close control */
  open: boolean;
  onClose: () => void;
  onDecisionRecorded?: (artifactId: string, decision: CandidateReviewDecision) => void;
}

type LoadState = "loading" | "loaded" | "error";

export function CandidateReviewDrawer({
  poolId,
  open,
  onClose,
  onDecisionRecorded,
}: CandidateReviewDrawerProps): JSX.Element | null {
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [scores, setScores] = useState<CandidateScoreResult[]>([]);
  const [members, setMembers] = useState<CandidatePoolMember[]>([]);
  const [poolEtag, setPoolEtag] = useState<string | null>(null);
  const [decompositionTarget, setDecompositionTarget] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;

    let cancelled = false;
    setLoadState("loading");
    setScores([]);
    setMembers([]);
    setPoolEtag(null);
    setDecompositionTarget(null);

    Promise.all([getCandidatePoolScore(poolId), listCandidatePoolMembers(poolId)])
      .then(([scoreResults, membersResult]) => {
        if (cancelled) return;
        setScores(scoreResults);
        setMembers(membersResult.items);
        setPoolEtag(membersResult.etag);
        setLoadState("loaded");
      })
      .catch(() => {
        if (!cancelled) setLoadState("error");
      });

    return () => {
      cancelled = true;
    };
  }, [open, poolId]);

  if (!open) return null;

  const memberByArtifactId = Object.fromEntries(
    members.map((m) => [m.artifact_id, m]),
  );

  function getCandidateTitle(candidateId: string): string {
    return memberByArtifactId[candidateId]?.title ?? candidateId;
  }

  function getCandidateLifecycle(candidateId: string): string {
    return memberByArtifactId[candidateId]?.lifecycle_state ?? "candidate";
  }

  const decompositionScore =
    decompositionTarget != null
      ? scores.find((s) => s.candidate_id === decompositionTarget) ?? null
      : null;

  // A2 §10: top-3 positive and top-2 penalty drivers
  function topPositiveDrivers(score: CandidateScoreResult, n: number): CandidateScoreComponent[] {
    return score.components
      .filter((c) => c.direction === "higher_better" && c.contribution > 0)
      .sort((a, b) => b.contribution - a.contribution)
      .slice(0, n);
  }

  function topPenaltyDrivers(score: CandidateScoreResult, n: number): CandidateScoreComponent[] {
    return score.components
      .filter((c) => c.direction === "lower_better" && c.contribution > 0)
      .sort((a, b) => b.contribution - a.contribution)
      .slice(0, n);
  }

  function dataQualityNormalizedValue(score: CandidateScoreResult): number | null {
    const dq = score.components.find((c) => c.component_id === "data_quality");
    return dq?.normalized_value ?? null;
  }

  return (
    <>
      {/* Backdrop */}
      <div
        data-testid="candidate-review-drawer-backdrop"
        onClick={onClose}
        style={{
          position: "fixed",
          inset: 0,
          background: "rgba(0,0,0,0.25)",
          zIndex: 900,
        }}
      />

      {/* Drawer */}
      <div
        data-testid={`candidate-review-drawer-${poolId}`}
        style={{
          position: "fixed",
          top: 0,
          right: 0,
          width: decompositionTarget ? "calc(100% - 100px)" : 720,
          maxWidth: "95vw",
          height: "100%",
          background: "#fff",
          boxShadow: "-4px 0 32px rgba(0,0,0,0.16)",
          zIndex: 1000,
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
        }}
      >
        {/* Drawer header */}
        <div
          style={{
            padding: "14px 20px",
            borderBottom: "1px solid #e2e8f0",
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            flexShrink: 0,
          }}
        >
          <div>
            <div style={{ fontWeight: 700, fontSize: 16, color: "#0f172a" }}>
              Candidate Review
            </div>
            <div style={{ fontSize: 12, color: "#64748b", marginTop: 1 }}>
              Pool: {poolId}
            </div>
          </div>
          <button
            data-testid="candidate-review-drawer-close"
            onClick={onClose}
            style={{
              background: "none",
              border: "none",
              cursor: "pointer",
              fontSize: 20,
              color: "#64748b",
              lineHeight: 1,
              padding: 4,
            }}
            aria-label="Close candidate review"
          >
            ×
          </button>
        </div>

        {/* Content */}
        <div style={{ flex: 1, overflow: "auto" }}>
          {loadState === "loading" && (
            <div
              data-testid="candidate-review-drawer-loading"
              style={{ padding: 24, fontSize: 13, color: "#94a3b8", textAlign: "center" }}
            >
              Loading candidate scores…
            </div>
          )}

          {loadState === "error" && (
            <div
              data-testid="candidate-review-drawer-error"
              style={{ padding: 24, fontSize: 13, color: "#dc2626", textAlign: "center" }}
            >
              Failed to load candidate pool data.
            </div>
          )}

          {loadState === "loaded" && scores.length === 0 && (
            <div
              data-testid="candidate-review-drawer-empty"
              style={{ padding: 24, fontSize: 13, color: "#94a3b8", textAlign: "center" }}
            >
              No scored candidates in this pool.
            </div>
          )}

          {loadState === "loaded" && scores.length > 0 && (
            <table
              data-testid="candidate-review-table"
              style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}
            >
              <thead>
                <tr
                  style={{
                    borderBottom: "2px solid #e2e8f0",
                    background: "#f8fafc",
                    position: "sticky",
                    top: 0,
                    zIndex: 1,
                  }}
                >
                  <th style={{ textAlign: "right", padding: "8px 12px", fontWeight: 600, color: "#64748b", width: 40 }}>
                    #
                  </th>
                  <th style={{ textAlign: "left", padding: "8px 12px", fontWeight: 600, color: "#64748b" }}>
                    Candidate
                  </th>
                  <th style={{ textAlign: "right", padding: "8px 12px", fontWeight: 600, color: "#64748b", width: 100 }}>
                    Eff. Score
                  </th>
                  <th style={{ textAlign: "right", padding: "8px 12px", fontWeight: 600, color: "#64748b", width: 80 }}>
                    Confidence
                  </th>
                  <th style={{ textAlign: "left", padding: "8px 12px", fontWeight: 600, color: "#64748b" }}>
                    Top Drivers
                  </th>
                  <th style={{ textAlign: "left", padding: "8px 12px", fontWeight: 600, color: "#64748b" }}>
                    Top Penalties
                  </th>
                  <th style={{ textAlign: "center", padding: "8px 12px", fontWeight: 600, color: "#64748b", width: 60 }}>
                    DQ
                  </th>
                  <th style={{ textAlign: "left", padding: "8px 12px", fontWeight: 600, color: "#64748b", width: 100 }}>
                    Band
                  </th>
                  <th style={{ textAlign: "left", padding: "8px 12px", fontWeight: 600, color: "#64748b" }}>
                    Decision
                  </th>
                </tr>
              </thead>
              <tbody>
                {scores.map((score, idx) => {
                  const title = getCandidateTitle(score.candidate_id);
                  const lifecycle = getCandidateLifecycle(score.candidate_id);
                  const bandStyle = BAND_STYLE[score.band] ?? BAND_STYLE.park;
                  const topPos = topPositiveDrivers(score, 3);
                  const topPen = topPenaltyDrivers(score, 2);
                  const dq = dataQualityNormalizedValue(score);
                  const isSelected = decompositionTarget === score.candidate_id;

                  return (
                    <tr
                      key={score.candidate_id}
                      data-testid={`candidate-row-${score.candidate_id}`}
                      style={{
                        borderBottom: "1px solid #f1f5f9",
                        background: isSelected ? "#f0f9ff" : idx % 2 === 0 ? "#fff" : "#fafafa",
                      }}
                    >
                      {/* Rank */}
                      <td
                        style={{ padding: "10px 12px", textAlign: "right", color: "#94a3b8", fontWeight: 600 }}
                      >
                        {score.rank ?? "—"}
                      </td>

                      {/* Candidate title */}
                      <td style={{ padding: "10px 12px" }}>
                        <div style={{ fontWeight: 600, color: "#0f172a" }}>{title}</div>
                        <div style={{ fontSize: 11, color: "#94a3b8" }}>{score.candidate_id}</div>
                      </td>

                      {/* Effective Score — clicking opens decomposition */}
                      <td style={{ padding: "10px 12px", textAlign: "right" }}>
                        <button
                          data-testid={`candidate-score-btn-${score.candidate_id}`}
                          onClick={() =>
                            setDecompositionTarget(
                              isSelected ? null : score.candidate_id,
                            )
                          }
                          title="Click to view score decomposition"
                          style={{
                            background: "none",
                            border: "none",
                            cursor: "pointer",
                            fontWeight: 700,
                            fontSize: 15,
                            color: "#0f172a",
                            padding: 0,
                            textDecoration: "underline",
                            textDecorationColor: "#cbd5e1",
                          }}
                        >
                          {score.effective_score.toFixed(1)}
                        </button>
                      </td>

                      {/* Evidence confidence */}
                      <td
                        data-testid={`candidate-confidence-${score.candidate_id}`}
                        style={{ padding: "10px 12px", textAlign: "right", color: "#374151" }}
                      >
                        {(score.evidence_confidence * 100).toFixed(0)}%
                      </td>

                      {/* Top 3 positive drivers */}
                      <td style={{ padding: "10px 12px" }}>
                        <div
                          data-testid={`candidate-top-drivers-${score.candidate_id}`}
                          style={{ display: "flex", flexDirection: "column", gap: 2 }}
                        >
                          {topPos.length === 0 ? (
                            <span style={{ fontSize: 11, color: "#94a3b8" }}>—</span>
                          ) : (
                            topPos.map((c) => (
                              <span key={c.component_id} style={{ fontSize: 11 }}>
                                <span
                                  style={{
                                    color: CATEGORY_COLOR[c.category] ?? "#374151",
                                    fontWeight: 600,
                                  }}
                                >
                                  {c.label}
                                </span>
                                <span style={{ color: "#94a3b8", marginLeft: 4 }}>
                                  +{c.contribution.toFixed(1)}
                                </span>
                              </span>
                            ))
                          )}
                        </div>
                      </td>

                      {/* Top 2 penalties */}
                      <td style={{ padding: "10px 12px" }}>
                        <div
                          data-testid={`candidate-top-penalties-${score.candidate_id}`}
                          style={{ display: "flex", flexDirection: "column", gap: 2 }}
                        >
                          {topPen.length === 0 ? (
                            <span style={{ fontSize: 11, color: "#94a3b8" }}>—</span>
                          ) : (
                            topPen.map((c) => (
                              <span key={c.component_id} style={{ fontSize: 11 }}>
                                <span style={{ color: "#dc2626", fontWeight: 600 }}>
                                  {c.label}
                                </span>
                                <span style={{ color: "#94a3b8", marginLeft: 4 }}>
                                  -{c.contribution.toFixed(1)}
                                </span>
                              </span>
                            ))
                          )}
                        </div>
                      </td>

                      {/* Data quality badge */}
                      <td style={{ padding: "10px 12px", textAlign: "center" }}>
                        <span
                          data-testid={`candidate-dq-badge-${score.candidate_id}`}
                          style={{
                            fontSize: 11,
                            fontWeight: 600,
                            color:
                              dq == null
                                ? "#94a3b8"
                                : dq >= 0.75
                                ? "#16a34a"
                                : dq >= 0.5
                                ? "#d97706"
                                : "#dc2626",
                          }}
                        >
                          {dq != null ? `${(dq * 100).toFixed(0)}%` : "—"}
                        </span>
                      </td>

                      {/* Band */}
                      <td style={{ padding: "10px 12px" }}>
                        <span
                          data-testid={`candidate-band-${score.candidate_id}`}
                          style={{
                            fontSize: 11,
                            fontWeight: 600,
                            color: bandStyle.text,
                            padding: "2px 8px",
                            borderRadius: 4,
                            border: `1px solid ${bandStyle.border}`,
                            background: bandStyle.bg,
                            whiteSpace: "nowrap",
                          }}
                        >
                          {score.band.replace("_", " ")}
                        </span>
                      </td>

                      {/* Decision buttons */}
                      <td style={{ padding: "10px 12px" }}>
                        <CandidateDecisionRow
                          poolId={poolId}
                          artifactId={score.candidate_id}
                          candidateTitle={title}
                          lifecycleState={lifecycle}
                          poolEtag={poolEtag}
                          onDecisionRecorded={onDecisionRecorded}
                        />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>

        {/* Footer: no-order-route proof */}
        <div
          style={{
            padding: "8px 20px",
            borderTop: "1px solid #f1f5f9",
            fontSize: 11,
            color: "#94a3b8",
            display: "flex",
            justifyContent: "space-between",
            flexShrink: 0,
          }}
        >
          <span>Candidate decisions use the AG-BE-CP-001 canonical route. No order routing.</span>
          <span style={{ color: "#22c55e", fontWeight: 500 }}>
            candidate_pool_bff_request_only_no_order_route
          </span>
        </div>
      </div>

      {/* Score decomposition panel (stacked on top of drawer) */}
      {decompositionScore && (
        <ScoreDecompositionPanel
          score={decompositionScore}
          candidateTitle={getCandidateTitle(decompositionScore.candidate_id)}
          onClose={() => setDecompositionTarget(null)}
        />
      )}
    </>
  );
}
