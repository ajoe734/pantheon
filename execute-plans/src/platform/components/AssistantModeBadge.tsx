import React from "react";

export type AssistantMode = "user" | "kernel_observe" | "kernel_debug" | "kernel_repair";

export interface AssistantProviderSignal {
  name?: string | null;
  status?: string | null;
  reason?: string | null;
  fallback?: string | null;
  runId?: string | null;
  runtime?: string | null;
  checkedAt?: string | null;
}

export interface AssistantModeSignals {
  mode?: AssistantMode | string | null;
  sessionId?: string | null;
  expiresAt?: string | null;
  ttlSeconds?: number | null;
  provider?: AssistantProviderSignal | null;
  commandsEnabled?: boolean | null;
  commandRef?: string | null;
  contextSnapshotAt?: string | null;
  contextPackId?: string | null;
  auditRefs?: string[];
  canViewKernelControls?: boolean;
}

export function isKernelMode(mode: AssistantMode | string | null | undefined): boolean {
  return mode === "kernel_observe" || mode === "kernel_debug" || mode === "kernel_repair";
}

function titleCaseMode(mode: string): string {
  return mode
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function statusTone(status: string | null | undefined): React.CSSProperties {
  const normalized = String(status ?? "").trim().toLowerCase();
  if (normalized === "ready" || normalized === "completed" || normalized === "ok") {
    return { color: "#166534", backgroundColor: "#dcfce7", borderColor: "#86efac" };
  }
  if (normalized === "degraded" || normalized === "timeout" || normalized === "fallback") {
    return { color: "#92400e", backgroundColor: "#fef3c7", borderColor: "#fbbf24" };
  }
  if (normalized === "failed" || normalized === "error" || normalized === "disabled") {
    return { color: "#991b1b", backgroundColor: "#fee2e2", borderColor: "#fca5a5" };
  }
  return { color: "#334155", backgroundColor: "#f1f5f9", borderColor: "#cbd5e1" };
}

function formatDateTime(value: string | null | undefined): string | null {
  const text = String(value ?? "").trim();
  if (!text) return null;
  const parsed = new Date(text);
  if (Number.isNaN(parsed.getTime())) return text;
  return parsed.toLocaleString();
}

function formatTtl(expiresAt?: string | null, ttlSeconds?: number | null): string {
  const expiresText = formatDateTime(expiresAt);
  if (expiresAt) {
    const parsed = new Date(expiresAt);
    if (!Number.isNaN(parsed.getTime())) {
      const remainingMs = parsed.getTime() - Date.now();
      if (remainingMs <= 0) return `expired at ${expiresText}`;
      const totalMinutes = Math.ceil(remainingMs / 60_000);
      const hours = Math.floor(totalMinutes / 60);
      const minutes = totalMinutes % 60;
      const remaining = hours > 0 ? `${hours}h ${minutes}m remaining` : `${minutes}m remaining`;
      return `${remaining}, expires ${expiresText}`;
    }
    return `expires ${expiresAt}`;
  }
  if (typeof ttlSeconds === "number" && Number.isFinite(ttlSeconds)) {
    const minutes = Math.ceil(ttlSeconds / 60);
    return minutes >= 60 ? `${Math.floor(minutes / 60)}h ${minutes % 60}m TTL` : `${minutes}m TTL`;
  }
  return "not provided";
}

function hasKernelSignalDetails(signals: AssistantModeSignals): boolean {
  const provider = signals.provider ?? {};
  return Boolean(
      signals.expiresAt ||
      signals.ttlSeconds ||
      provider.status ||
      provider.name ||
      (signals.commandsEnabled !== null && signals.commandsEnabled !== undefined) ||
      signals.commandRef ||
      signals.contextSnapshotAt ||
      signals.contextPackId ||
      (signals.auditRefs && signals.auditRefs.length > 0),
  );
}

function SignalRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ minWidth: 180 }}>
      <div style={{ color: "#64748b", fontSize: 12 }}>{label}</div>
      <div style={{ color: "#0f172a", fontSize: 13, fontWeight: 600 }}>{children}</div>
    </div>
  );
}

function Pill({
  children,
  style,
  testId,
}: {
  children: React.ReactNode;
  style?: React.CSSProperties;
  testId?: string;
}) {
  return (
    <span
      data-testid={testId}
      style={{
        alignItems: "center",
        border: "1px solid #cbd5e1",
        borderRadius: 6,
        display: "inline-flex",
        fontSize: 12,
        fontWeight: 700,
        lineHeight: 1,
        padding: "5px 7px",
        ...style,
      }}
    >
      {children}
    </span>
  );
}

export function AssistantModeBadge({ signals }: { signals: AssistantModeSignals }): JSX.Element | null {
  const mode = String(signals.mode ?? "").trim();
  if (!isKernelMode(mode)) return null;

  const canView = signals.canViewKernelControls ?? hasKernelSignalDetails(signals);
  if (!canView) return null;

  const provider = signals.provider ?? {};
  const providerStatus = String(provider.status ?? "unknown").trim() || "unknown";
  const providerName = String(provider.name ?? "provider").trim() || "provider";
  const auditRefs = signals.auditRefs ?? [];

  return (
    <section
      aria-label="Assistant kernel session"
      data-testid="assistant-kernel-signals"
      style={{
        border: "1px solid #f59e0b",
        borderRadius: 8,
        background: "#fffbeb",
        marginTop: 10,
        padding: 12,
      }}
    >
      <div style={{ alignItems: "center", display: "flex", flexWrap: "wrap", gap: 8 }}>
        <Pill style={{ color: "#92400e", backgroundColor: "#fef3c7", borderColor: "#f59e0b" }} testId="assistant-mode">
          {titleCaseMode(mode)}
        </Pill>
        <Pill style={statusTone(providerStatus)} testId="assistant-provider-status">
          {providerName}: {providerStatus}
        </Pill>
        <Pill
          style={
            signals.commandsEnabled
              ? { color: "#166534", backgroundColor: "#dcfce7", borderColor: "#86efac" }
              : { color: "#475569", backgroundColor: "#f8fafc", borderColor: "#cbd5e1" }
          }
          testId="assistant-command-state"
        >
          commands {signals.commandsEnabled ? "enabled" : "disabled"}
        </Pill>
      </div>

      <div style={{ display: "flex", flexWrap: "wrap", gap: 12, marginTop: 10 }}>
        <SignalRow label="TTL">{formatTtl(signals.expiresAt, signals.ttlSeconds)}</SignalRow>
        <SignalRow label="Context snapshot">
          {formatDateTime(signals.contextSnapshotAt) ?? "not captured"}
        </SignalRow>
        <SignalRow label="Session">{signals.sessionId || "not assigned"}</SignalRow>
        <SignalRow label="Audit">
          {auditRefs.length > 0 ? auditRefs.join(", ") : "not recorded"}
        </SignalRow>
        {signals.contextPackId && <SignalRow label="Context pack">{signals.contextPackId}</SignalRow>}
        {signals.commandRef && <SignalRow label="Command ref">{signals.commandRef}</SignalRow>}
        {provider.runtime && <SignalRow label="Runtime">{provider.runtime}</SignalRow>}
        {provider.runId && <SignalRow label="Provider run">{provider.runId}</SignalRow>}
        {provider.checkedAt && <SignalRow label="Provider checked">{formatDateTime(provider.checkedAt)}</SignalRow>}
        {provider.fallback && <SignalRow label="Fallback">{provider.fallback}</SignalRow>}
        {provider.reason && <SignalRow label="Reason">{provider.reason}</SignalRow>}
      </div>
    </section>
  );
}

export default AssistantModeBadge;
