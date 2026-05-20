import { useMemo } from "react";

export type CapitalBindingGoNoGoReadinessState = "go" | "no_go";
export type CapitalBindingGoNoGoGateStatus = "ready" | "blocked";

export interface CapitalBindingGoNoGoGate {
  id: string;
  label: string;
  status: CapitalBindingGoNoGoGateStatus;
  passed: boolean;
  ready_items: number;
  blocked_items: number;
  total_items: number;
  progress_percent: number;
  blocking_reasons: string[];
  evidence_refs: string[];
  source?: string | null;
}

export interface CapitalBindingGoNoGoProgress {
  ready_gates: number;
  blocked_gates: number;
  total_gates: number;
  ready_items: number;
  blocked_items: number;
  total_items: number;
  progress_percent: number;
}

export interface CapitalBindingReadinessStatus {
  readiness_id?: string | null;
  binding_id?: string | null;
  persona_id?: string | null;
  capital_pool_id?: string | null;
  can_bind_live: boolean;
  approval: Record<string, string>;
  ttl_hours?: number | null;
  blocking_reasons: string[];
  evidence_refs: string[];
}

export interface CapitalBindingSponsorMandateStatus {
  responsibility_id?: string | null;
  sponsor_persona_id?: string | null;
  live_owner?: string | null;
  status: string;
  escalation_levels: number;
  passed: boolean;
  blocking_reasons: string[];
  evidence_refs: string[];
}

export interface CapitalBindingConflictLogStatus {
  conflict_resolution_log_id?: string | null;
  capital_pool_id?: string | null;
  passed: boolean;
  open_conflict_ids: string[];
  blocking_reasons: string[];
  evidence_refs: string[];
}

export interface CapitalBindingTtlStatus {
  binding_id?: string | null;
  status: string;
  admissible: boolean;
  ttl_hours?: number | null;
  evaluated_at?: string | null;
  expires_at?: string | null;
  blocking_reasons: string[];
}

export interface CapitalBindingGoNoGoDashboardReadModel {
  version: string;
  source: string;
  readiness_state: CapitalBindingGoNoGoReadinessState;
  can_bind_live: boolean;
  passed: boolean;
  progress: CapitalBindingGoNoGoProgress;
  blocking_reasons: string[];
  gates: CapitalBindingGoNoGoGate[];
  readiness: CapitalBindingReadinessStatus;
  sponsor_mandate: CapitalBindingSponsorMandateStatus;
  conflict_log_status: CapitalBindingConflictLogStatus;
  ttl_status: CapitalBindingTtlStatus;
}

export interface CapitalBindingGoNoGoDashboardProps {
  dashboard?: CapitalBindingGoNoGoDashboardReadModel | null;
  loading?: boolean;
  error?: string | null;
  onRetry?: () => void;
}

export function CapitalBindingGoNoGoDashboard({
  dashboard,
  loading = false,
  error,
  onRetry,
}: CapitalBindingGoNoGoDashboardProps) {
  const blockingReasons = useMemo(() => {
    if (!dashboard) return [];
    const ordered = new Map<string, true>();
    for (const reason of dashboard.blocking_reasons) {
      ordered.set(reason, true);
    }
    for (const gate of dashboard.gates) {
      for (const reason of gate.blocking_reasons) {
        ordered.set(reason, true);
      }
    }
    for (const reason of dashboard.readiness.blocking_reasons) {
      ordered.set(reason, true);
    }
    for (const reason of dashboard.sponsor_mandate.blocking_reasons) {
      ordered.set(reason, true);
    }
    for (const reason of dashboard.conflict_log_status.blocking_reasons) {
      ordered.set(reason, true);
    }
    for (const reason of dashboard.ttl_status.blocking_reasons) {
      ordered.set(reason, true);
    }
    return Array.from(ordered.keys());
  }, [dashboard]);

  if (loading) {
    return (
      <div role="status" data-testid="capital-binding-go-no-go-loading">
        Loading capital binding go/no-go dashboard...
      </div>
    );
  }

  if (error) {
    return (
      <div role="alert" data-testid="capital-binding-go-no-go-error">
        <span>{error}</span>
        {onRetry && (
          <button
            type="button"
            onClick={onRetry}
            data-testid="capital-binding-go-no-go-retry"
          >
            Retry
          </button>
        )}
      </div>
    );
  }

  if (!dashboard) {
    return (
      <div data-testid="capital-binding-go-no-go-empty">
        No capital binding go/no-go dashboard selected.
      </div>
    );
  }

  const stateLabel = dashboard.readiness_state === "go" ? "Go" : "No-Go";

  return (
    <div data-testid="capital-binding-go-no-go-dashboard">
      <section aria-label="Capital binding go/no-go summary">
        <h2>Capital Binding Go/No-Go</h2>
        <dl>
          <div>
            <dt>Status</dt>
            <dd
              data-testid="capital-binding-readiness-state"
              data-state={dashboard.readiness_state}
            >
              {stateLabel}
            </dd>
          </div>
          <div>
            <dt>Live Binding</dt>
            <dd data-testid="capital-binding-can-bind-live">
              {dashboard.can_bind_live ? "Can Bind Live" : "Blocked"}
            </dd>
          </div>
          <div>
            <dt>Binding</dt>
            <dd data-testid="capital-binding-id">
              {dashboard.readiness.binding_id || "-"}
            </dd>
          </div>
          <div>
            <dt>Capital Pool</dt>
            <dd data-testid="capital-binding-pool">
              {dashboard.readiness.capital_pool_id || "-"}
            </dd>
          </div>
          <div>
            <dt>Progress</dt>
            <dd data-testid="capital-binding-progress">
              {dashboard.progress.ready_items}/{dashboard.progress.total_items} checks ready{" "}
              ({dashboard.progress.progress_percent}%)
            </dd>
          </div>
          <div>
            <dt>Gates</dt>
            <dd data-testid="capital-binding-gate-counts">
              {dashboard.progress.ready_gates}/{dashboard.progress.total_gates} ready
            </dd>
          </div>
          <div>
            <dt>Source</dt>
            <dd data-testid="capital-binding-source">{dashboard.source}</dd>
          </div>
        </dl>
      </section>

      <section aria-label="Readiness gates">
        <h3>Readiness Gates</h3>
        <ul data-testid="capital-binding-gates">
          {dashboard.gates.map((gate) => (
            <li key={gate.id} data-testid={`capital-binding-gate-${gate.id}`}>
              <span data-testid={`capital-binding-gate-label-${gate.id}`}>
                {gate.label}
              </span>
              <span data-testid={`capital-binding-gate-status-${gate.id}`}>
                {gate.status}
              </span>
              <span data-testid={`capital-binding-gate-progress-${gate.id}`}>
                {gate.ready_items}/{gate.total_items}
              </span>
            </li>
          ))}
        </ul>
      </section>

      <section aria-label="Sponsor mandate">
        <h3>Sponsor Mandate</h3>
        <dl>
          <div>
            <dt>Sponsor</dt>
            <dd data-testid="capital-binding-sponsor">
              {dashboard.sponsor_mandate.sponsor_persona_id || "-"}
            </dd>
          </div>
          <div>
            <dt>Live Owner</dt>
            <dd data-testid="capital-binding-live-owner">
              {dashboard.sponsor_mandate.live_owner || "-"}
            </dd>
          </div>
          <div>
            <dt>Status</dt>
            <dd data-testid="capital-binding-sponsor-status">
              {dashboard.sponsor_mandate.status}
            </dd>
          </div>
          <div>
            <dt>Escalation</dt>
            <dd data-testid="capital-binding-escalation-levels">
              {dashboard.sponsor_mandate.escalation_levels} levels
            </dd>
          </div>
        </dl>
      </section>

      <section aria-label="Conflict log status">
        <h3>Conflict Log</h3>
        <dl>
          <div>
            <dt>Log</dt>
            <dd data-testid="capital-binding-conflict-log-id">
              {dashboard.conflict_log_status.conflict_resolution_log_id || "-"}
            </dd>
          </div>
          <div>
            <dt>Status</dt>
            <dd data-testid="capital-binding-conflict-log-status">
              {dashboard.conflict_log_status.passed ? "ready" : "blocked"}
            </dd>
          </div>
          <div>
            <dt>Open Conflicts</dt>
            <dd data-testid="capital-binding-open-conflict-count">
              {dashboard.conflict_log_status.open_conflict_ids.length}
            </dd>
          </div>
        </dl>
        {dashboard.conflict_log_status.open_conflict_ids.length > 0 && (
          <ul data-testid="capital-binding-open-conflicts">
            {dashboard.conflict_log_status.open_conflict_ids.map((conflictId) => (
              <li key={conflictId}>{conflictId}</li>
            ))}
          </ul>
        )}
      </section>

      <section aria-label="TTL status">
        <h3>TTL</h3>
        <dl>
          <div>
            <dt>Status</dt>
            <dd data-testid="capital-binding-ttl-status">
              {dashboard.ttl_status.status}
            </dd>
          </div>
          <div>
            <dt>Window</dt>
            <dd data-testid="capital-binding-ttl-hours">
              {dashboard.ttl_status.ttl_hours ?? "-"} hours
            </dd>
          </div>
          <div>
            <dt>Expires</dt>
            <dd data-testid="capital-binding-ttl-expires-at">
              {formatTimestamp(dashboard.ttl_status.expires_at)}
            </dd>
          </div>
          <div>
            <dt>Evaluated</dt>
            <dd data-testid="capital-binding-ttl-evaluated-at">
              {formatTimestamp(dashboard.ttl_status.evaluated_at)}
            </dd>
          </div>
        </dl>
      </section>

      {blockingReasons.length > 0 && (
        <section aria-label="Blocking reasons">
          <h3>Blocking Reasons</h3>
          <ul data-testid="capital-binding-blocking-reasons">
            {blockingReasons.map((reason, index) => (
              <li key={`${reason}-${index}`}>{reason}</li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

function formatTimestamp(value: string | null | undefined): string {
  if (!value) return "-";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString();
}
