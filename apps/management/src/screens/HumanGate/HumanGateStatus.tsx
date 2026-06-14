import React, { useMemo } from "react";

// ---------- Types ----------

export interface HumanGateEvidenceReviewed {
  key: string;
  evidence_hash: string;
  source_ref: string;
  status: string;
  reviewed_at?: string | null;
}

export interface HumanGateCanProceedInput {
  readiness_packet_ref?: string | null;
  readiness_packet_can_proceed: boolean;
  required_evidence: string[];
  missing_evidence: string[];
  blocking_reasons: string[];
  unsafe_true_flags: string[];
  gate_results_blocking: string[];
}

export type HumanGateSignatureMeaning =
  | "approved"
  | "approved_with_conditions"
  | "rejected"
  | "revoked";

export interface HumanGateSignature {
  signature_id: string;
  role: string;
  actor_id: string;
  signed_at: string;
  evidence_hash: string;
  evidence_reviewed: string[];
  meaning: HumanGateSignatureMeaning;
  conditions: string[];
  source_ref?: string | null;
  revoked_at?: string | null;
}

export type HumanGateDecisionStatus =
  | "pending"
  | "approved"
  | "blocked"
  | "rejected"
  | "revoked";

export interface HumanGateDecision {
  decision_id: string;
  schema_version?: string;
  target_type: string;
  target_id: string;
  target_environment: string;
  required_roles: string[];
  evidence_reviewed: HumanGateEvidenceReviewed[];
  evidence_hash?: string | null;
  can_proceed_input: HumanGateCanProceedInput;
  signatures: HumanGateSignature[];
  status: HumanGateDecisionStatus;
  can_proceed: boolean;
  reason?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  expires_at?: string | null;
}

// ---------- Helpers ----------

function formatTimestamp(value: string | null | undefined): string {
  if (!value) return "-";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString();
}

function computeExpiryLabel(expiresAt: string | null | undefined): string | null {
  if (!expiresAt) return null;
  const exp = new Date(expiresAt).getTime();
  if (Number.isNaN(exp)) return null;
  const diff = exp - Date.now();
  if (diff <= 0) return "Expired";
  const totalMins = Math.floor(diff / 60000);
  const hours = Math.floor(totalMins / 60);
  const mins = totalMins % 60;
  return hours > 0 ? `${hours}h ${mins}m remaining` : `${mins}m remaining`;
}

// ---------- Component ----------

export interface HumanGateStatusProps {
  decision?: HumanGateDecision | null;
  loading?: boolean;
  error?: string | null;
  onRetry?: () => void;
}

export function HumanGateStatus({
  decision,
  loading = false,
  error,
  onRetry,
}: HumanGateStatusProps) {
  const activeSignaturesByRole = useMemo(() => {
    if (!decision) return new Map<string, HumanGateSignature>();
    const map = new Map<string, HumanGateSignature>();
    for (const sig of decision.signatures) {
      if (
        !sig.revoked_at &&
        (sig.meaning === "approved" || sig.meaning === "approved_with_conditions")
      ) {
        map.set(sig.role, sig);
      }
    }
    return map;
  }, [decision]);

  const pendingRoles = useMemo(
    () =>
      decision
        ? decision.required_roles.filter((role) => !activeSignaturesByRole.has(role))
        : [],
    [decision, activeSignaturesByRole],
  );

  const rejectedSignatures = useMemo(
    () =>
      decision
        ? decision.signatures.filter(
            (sig) => !sig.revoked_at && sig.meaning === "rejected",
          )
        : [],
    [decision],
  );

  const blockingReasons = useMemo(() => {
    if (!decision) return [];
    const cpi = decision.can_proceed_input;
    const reasons: string[] = [];
    if (!cpi.readiness_packet_can_proceed) {
      reasons.push("readiness_packet_can_proceed=false");
    }
    for (const item of cpi.missing_evidence) {
      reasons.push(`missing_evidence: ${item}`);
    }
    for (const item of cpi.blocking_reasons) {
      reasons.push(`blocking_reason: ${item}`);
    }
    for (const item of cpi.unsafe_true_flags) {
      reasons.push(`unsafe_true_flag: ${item}`);
    }
    for (const item of cpi.gate_results_blocking) {
      reasons.push(`gate_result_blocking: ${item}`);
    }
    for (const role of pendingRoles) {
      reasons.push(`missing_signature: ${role}`);
    }
    for (const sig of rejectedSignatures) {
      reasons.push(`rejected_signature: ${sig.role}`);
    }
    return reasons;
  }, [decision, pendingRoles, rejectedSignatures]);

  const expiryLabel = useMemo(
    () => computeExpiryLabel(decision?.expires_at),
    [decision?.expires_at],
  );

  if (loading) {
    return (
      <div role="status" data-testid="human-gate-loading">
        Loading human gate status…
      </div>
    );
  }

  if (error) {
    return (
      <div role="alert" data-testid="human-gate-error">
        <span>{error}</span>
        {onRetry && (
          <button type="button" onClick={onRetry} data-testid="retry-button">
            Retry
          </button>
        )}
      </div>
    );
  }

  if (!decision) {
    return (
      <div data-testid="human-gate-empty">No human gate decision selected.</div>
    );
  }

  return (
    <div data-testid="human-gate-status">
      {/* Header */}
      <section aria-label="Decision header">
        <dl>
          <div>
            <dt>Decision ID</dt>
            <dd data-testid="decision-id">{decision.decision_id}</dd>
          </div>
          <div>
            <dt>Status</dt>
            <dd data-testid="decision-status" data-status={decision.status}>
              {decision.status}
            </dd>
          </div>
          <div>
            <dt>Target</dt>
            <dd data-testid="target-info">
              {decision.target_type} / {decision.target_id} ({decision.target_environment})
            </dd>
          </div>
          {decision.can_proceed && (
            <div>
              <dt>Proceed</dt>
              <dd data-testid="can-proceed-badge">Can Proceed</dd>
            </div>
          )}
          {expiryLabel && (
            <div>
              <dt>Expiry</dt>
              <dd data-testid="expiry-countdown">{expiryLabel}</dd>
            </div>
          )}
          {decision.updated_at && (
            <div>
              <dt>Last updated</dt>
              <dd data-testid="updated-at">{formatTimestamp(decision.updated_at)}</dd>
            </div>
          )}
        </dl>
      </section>

      {/* Signatures */}
      <section aria-label="Signatures">
        <h3>Signatures</h3>
        <ul data-testid="signatures-list">
          {decision.required_roles.map((role) => {
            const sig = activeSignaturesByRole.get(role);
            return (
              <li key={role} data-testid={`role-row-${role}`}>
                <span data-testid={`role-name-${role}`}>{role}</span>
                {sig ? (
                  <span data-testid={`role-signed-${role}`}>
                    Signed by {sig.actor_id} at {formatTimestamp(sig.signed_at)}
                    {sig.meaning !== "approved" ? ` (${sig.meaning})` : ""}
                    {sig.conditions.length > 0
                      ? `; conditions: ${sig.conditions.join(", ")}`
                      : ""}
                  </span>
                ) : (
                  <span data-testid={`role-pending-${role}`}>Pending</span>
                )}
              </li>
            );
          })}
        </ul>
        {pendingRoles.length > 0 && (
          <p data-testid="pending-count">
            {pendingRoles.length} signature{pendingRoles.length === 1 ? "" : "s"} pending
          </p>
        )}
      </section>

      {/* Blocking Reasons */}
      {blockingReasons.length > 0 && (
        <section aria-label="Blocking reasons">
          <h3>Blocking Reasons</h3>
          <ul data-testid="blocking-reasons">
            {blockingReasons.map((reason, i) => (
              <li key={`${reason}-${i}`}>{reason}</li>
            ))}
          </ul>
        </section>
      )}

      {/* Rejections */}
      {rejectedSignatures.length > 0 && (
        <section aria-label="Rejected signatures">
          <h3>Rejected Signatures</h3>
          <ul data-testid="rejected-signatures">
            {rejectedSignatures.map((sig) => (
              <li key={sig.signature_id} data-testid={`rejected-signature-${sig.role}`}>
                <span>{sig.role}</span>
                <span>
                  Rejected by {sig.actor_id} at {formatTimestamp(sig.signed_at)}
                </span>
                <span>{sig.evidence_hash}</span>
                {sig.source_ref && <span>{sig.source_ref}</span>}
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* Evidence */}
      <section aria-label="Evidence reviewed">
        <h3>Evidence Reviewed</h3>
        <ul data-testid="evidence-list">
          {decision.evidence_reviewed.map((item) => (
            <li key={item.key} data-testid={`evidence-row-${item.key}`}>
              <span data-testid={`evidence-key-${item.key}`}>{item.key}</span>
              <span data-testid={`evidence-status-${item.key}`}>{item.status}</span>
              <span data-testid={`evidence-hash-${item.key}`}>{item.evidence_hash}</span>
              <span data-testid={`evidence-ref-${item.key}`}>{item.source_ref}</span>
            </li>
          ))}
        </ul>
      </section>

      {/* Reason */}
      {decision.reason && (
        <section aria-label="Reason">
          <h3>Reason</h3>
          <p data-testid="decision-reason">{decision.reason}</p>
        </section>
      )}
    </div>
  );
}
