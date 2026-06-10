import { useMemo } from "react";

export type BrokerGoNoGoReadinessState = "go" | "no_go";
export type BrokerGoNoGoGateStatus = "ready" | "blocked";

export interface BrokerGoNoGoIssue {
  code: string;
  path: string;
  message: string;
}

export interface BrokerGoNoGoChecklistItem {
  id: string;
  order: number;
  text: string;
  status: BrokerGoNoGoGateStatus;
  required: boolean;
  evidence_refs: string[];
  blocking_reasons: string[];
  source: string;
}

export interface BrokerGoNoGoChecklist {
  version: string;
  source: string;
  can_sign_off: boolean;
  passed: boolean;
  blocking_reasons: string[];
  items: BrokerGoNoGoChecklistItem[];
}

export interface BrokerGoNoGoValidationResult {
  passed: boolean;
  can_activate: boolean;
  blocking_reasons: string[];
  errors: BrokerGoNoGoIssue[];
  warnings: BrokerGoNoGoIssue[];
}

export interface BrokerGoNoGoGate {
  id: string;
  label: string;
  status: BrokerGoNoGoGateStatus;
  passed: boolean;
  ready_items: number;
  blocked_items: number;
  total_items: number;
  progress_percent: number;
  blocking_reasons: string[];
  evidence_refs: string[];
  source?: string | null;
}

export interface BrokerGoNoGoProgress {
  ready_gates: number;
  blocked_gates: number;
  total_gates: number;
  ready_items: number;
  blocked_items: number;
  total_items: number;
  progress_percent: number;
}

export interface BrokerGoNoGoDashboardReadModel {
  version: string;
  source: string;
  readiness_state: BrokerGoNoGoReadinessState;
  can_activate: boolean;
  passed: boolean;
  progress: BrokerGoNoGoProgress;
  blocking_reasons: string[];
  gates: BrokerGoNoGoGate[];
  activation_criteria: BrokerGoNoGoValidationResult;
  risk_owner_checklist: BrokerGoNoGoChecklist;
  operator_checklist: BrokerGoNoGoChecklist;
}

export interface BrokerGoNoGoDashboardProps {
  dashboard?: BrokerGoNoGoDashboardReadModel | null;
  loading?: boolean;
  error?: string | null;
  onRetry?: () => void;
}

export function BrokerGoNoGoDashboard({
  dashboard,
  loading = false,
  error,
  onRetry,
}: BrokerGoNoGoDashboardProps) {
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
    return Array.from(ordered.keys());
  }, [dashboard]);

  if (loading) {
    return (
      <div role="status" data-testid="broker-go-no-go-loading">
        Loading broker go/no-go dashboard...
      </div>
    );
  }

  if (error) {
    return (
      <div role="alert" data-testid="broker-go-no-go-error">
        <span>{error}</span>
        {onRetry && (
          <button type="button" onClick={onRetry} data-testid="broker-go-no-go-retry">
            Retry
          </button>
        )}
      </div>
    );
  }

  if (!dashboard) {
    return (
      <div data-testid="broker-go-no-go-empty">No broker go/no-go dashboard selected.</div>
    );
  }

  const stateLabel = dashboard.readiness_state === "go" ? "Go" : "No-Go";

  return (
    <div data-testid="broker-go-no-go-dashboard">
      <section aria-label="Broker go/no-go summary">
        <h2>Broker Go/No-Go</h2>
        <dl>
          <div>
            <dt>Status</dt>
            <dd data-testid="broker-readiness-state" data-state={dashboard.readiness_state}>
              {stateLabel}
            </dd>
          </div>
          <div>
            <dt>Activation</dt>
            <dd data-testid="broker-can-activate">
              {dashboard.can_activate ? "Can Activate" : "Blocked"}
            </dd>
          </div>
          <div>
            <dt>Progress</dt>
            <dd data-testid="broker-progress">
              {dashboard.progress.ready_items}/{dashboard.progress.total_items} checks ready
              {" "}
              ({dashboard.progress.progress_percent}%)
            </dd>
          </div>
          <div>
            <dt>Gates</dt>
            <dd data-testid="broker-gate-counts">
              {dashboard.progress.ready_gates}/{dashboard.progress.total_gates} ready
            </dd>
          </div>
          <div>
            <dt>Source</dt>
            <dd data-testid="broker-source">{dashboard.source}</dd>
          </div>
        </dl>
      </section>

      <section aria-label="Readiness gates">
        <h3>Readiness Gates</h3>
        <ul data-testid="broker-gates">
          {dashboard.gates.map((gate) => (
            <li key={gate.id} data-testid={`broker-gate-${gate.id}`}>
              <span data-testid={`broker-gate-label-${gate.id}`}>{gate.label}</span>
              <span data-testid={`broker-gate-status-${gate.id}`}>{gate.status}</span>
              <span data-testid={`broker-gate-progress-${gate.id}`}>
                {gate.ready_items}/{gate.total_items}
              </span>
            </li>
          ))}
        </ul>
      </section>

      {blockingReasons.length > 0 && (
        <section aria-label="Blocking reasons">
          <h3>Blocking Reasons</h3>
          <ul data-testid="broker-blocking-reasons">
            {blockingReasons.map((reason, index) => (
              <li key={`${reason}-${index}`}>{reason}</li>
            ))}
          </ul>
        </section>
      )}

      <section aria-label="Checklist progress">
        <h3>Checklist Progress</h3>
        {renderChecklist("risk-owner", "Risk Owner", dashboard.risk_owner_checklist)}
        {renderChecklist("operator", "Operator", dashboard.operator_checklist)}
      </section>

      {dashboard.activation_criteria.errors.length > 0 && (
        <section aria-label="Activation criteria errors">
          <h3>Activation Criteria</h3>
          <ul data-testid="broker-criteria-errors">
            {dashboard.activation_criteria.errors.map((issue) => (
              <li key={`${issue.code}-${issue.path}`}>
                <span>{issue.code}</span>
                <span>{issue.path}</span>
                <span>{issue.message}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

function renderChecklist(
  keyPrefix: string,
  label: string,
  checklist: BrokerGoNoGoChecklist,
) {
  return (
    <section aria-label={`${label} checklist`} data-testid={`${keyPrefix}-checklist`}>
      <h4>{label}</h4>
      <ul>
        {checklist.items.map((item) => (
          <li key={item.id} data-testid={`${keyPrefix}-checklist-item-${item.id}`}>
            <span>{item.order}. </span>
            <span>{item.text}</span>
            <span data-testid={`${keyPrefix}-checklist-status-${item.id}`}>
              {item.status}
            </span>
            {item.blocking_reasons.length > 0 && (
              <ul data-testid={`${keyPrefix}-checklist-blocking-${item.id}`}>
                {item.blocking_reasons.map((reason) => (
                  <li key={reason}>{reason}</li>
                ))}
              </ul>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
