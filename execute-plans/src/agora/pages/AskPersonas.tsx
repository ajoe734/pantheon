import React, { useEffect, useRef, useState } from "react";
import { postAsk, openAskSse, getAskSession } from "@/lib/bff/agora";
import {
  assistantCatalogRouteFromHandlerRef,
  getAssistantOrchestratorStatus,
  invokeAssistantCatalogRoute,
  type ManagementAssistantAskRequest,
} from "@/lib/bff/managementAssistant";
import {
  assistantUiContextMetadata,
  buildAssistantUiContext,
  type AssistantUiContextV1,
} from "@/lib/assistant/uiContextRegistry";
import AssistantModeBadge, {
  isKernelMode,
  type AssistantMode,
  type AssistantModeSignals,
  type AssistantProviderSignal,
} from "@/platform/components/AssistantModeBadge";

type JsonRecord = Record<string, unknown>;
type ManagementAskControlMode = NonNullable<ManagementAssistantAskRequest["controlMode"]>;
type ManagementAskOpenClaw = NonNullable<ManagementAssistantAskRequest["openclaw"]>;
type ManagementAskRepair = NonNullable<ManagementAskOpenClaw["repair"]>;
type CatalogRenderSurface = "button" | "command" | "card_action";

interface SourceCitation {
  source_id: string;
  href?: string;
  snapshot_at?: string;
  status?: string;
}

function isUserMode(mode: AssistantMode | null): boolean {
  return mode === null || mode === "user";
}

function recordFrom(value: unknown): JsonRecord {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as JsonRecord
    : {};
}

function arrayFrom(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function firstString(...values: unknown[]): string | null {
  for (const value of values) {
    if (value === null || value === undefined || typeof value === "object") continue;
    const text = String(value ?? "").trim();
    if (text) return text;
  }
  return null;
}

function firstBoolean(...values: unknown[]): boolean | null {
  for (const value of values) {
    if (typeof value === "boolean") return value;
    if (typeof value === "string") {
      const normalized = value.trim().toLowerCase();
      if (["true", "1", "yes", "enabled"].includes(normalized)) return true;
      if (["false", "0", "no", "disabled"].includes(normalized)) return false;
    }
  }
  return null;
}

function firstNumber(...values: unknown[]): number | null {
  for (const value of values) {
    if (typeof value === "number" && Number.isFinite(value)) return value;
    if (typeof value === "string" && value.trim()) {
      const parsed = Number(value);
      if (Number.isFinite(parsed)) return parsed;
    }
  }
  return null;
}

function normalizeMode(...values: unknown[]): AssistantMode | null {
  const mode = firstString(...values);
  if (
    mode === "user" ||
    mode === "kernel_observe" ||
    mode === "kernel_debug" ||
    mode === "kernel_repair"
  ) {
    return mode;
  }
  return null;
}

function uniqueStrings(values: unknown[]): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const value of values) {
    const candidates = Array.isArray(value) ? value : [value];
    for (const candidate of candidates) {
      const text = firstString(candidate);
      if (!text || seen.has(text)) continue;
      seen.add(text);
      result.push(text);
    }
  }
  return result;
}

function normalizeProvider(...values: unknown[]): AssistantProviderSignal {
  const records = values.map(recordFrom);
  const status = firstString(
    ...values,
    ...records.map((record) => record.status),
    ...records.map((record) => record.provider_status),
    ...records.map((record) => record.providerStatus),
  );
  return {
    name: firstString(...records.map((record) => record.provider), ...records.map((record) => record.name)),
    status,
    reason: firstString(...records.map((record) => record.reason), ...records.map((record) => record.degraded_reason)),
    fallback: firstString(...records.map((record) => record.fallback)),
    runId: firstString(...records.map((record) => record.run_id), ...records.map((record) => record.runId)),
    runtime: firstString(
      ...records.map((record) => record.runtime),
      ...records.map((record) => record.provider_runtime),
    ),
    checkedAt: firstString(
      ...records.map((record) => record.checked_at),
      ...records.map((record) => record.checkedAt),
      ...records.map((record) => record.snapshot_at),
      ...records.map((record) => record.snapshotAt),
      ...records.map((record) => record.created_at),
      ...records.map((record) => record.createdAt),
    ),
  };
}

function normalizeAssistantSignals(value: unknown, fallbackSessionId?: string | null): AssistantModeSignals {
  const root = recordFrom(value);
  const data = recordFrom(root.data ?? root);
  const session = recordFrom(data.session ?? data.assistant_session ?? data.assistantSession);
  const meta = recordFrom(root.meta ?? data.meta);
  const assistantMeta = recordFrom(meta.assistant ?? data.assistant);
  const context = recordFrom(data.context ?? data.context_pack ?? session.context ?? session.context_pack);
  const commandState = recordFrom(data.command_state ?? data.commandState ?? assistantMeta.command_state);
  const controlMode = recordFrom(
    data.control_mode ??
    data.controlMode ??
    meta.control_mode ??
    meta.controlMode ??
    assistantMeta.control_mode ??
    assistantMeta.controlMode,
  );
  const provider = normalizeProvider(
    data.provider,
    data.provider_status,
    data.providerStatus,
    assistantMeta.provider,
    assistantMeta.provider_status,
    meta.provider_status,
    meta.providerStatus,
    meta.provider,
    session.provider,
  );
  const mode = normalizeMode(
    data.mode,
    data.assistant_mode,
    data.assistantMode,
    session.mode,
    session.assistant_mode,
    session.assistantMode,
    assistantMeta.mode,
    controlMode.mode,
  );
  const commandMeta = recordFrom(meta.command);
  const explicitKernelVisibility = firstBoolean(
    data.can_view_kernel_controls,
    data.canViewKernelControls,
    data.kernel_controls_visible,
    data.kernelControlsVisible,
    session.can_view_kernel_controls,
    session.canViewKernelControls,
    assistantMeta.can_view_kernel_controls,
    assistantMeta.canViewKernelControls,
  );

  return {
    mode,
    sessionId: firstString(
      fallbackSessionId,
      data.session_id,
      data.sessionId,
      session.session_id,
      session.sessionId,
      session.id,
    ),
    expiresAt: firstString(
      data.expires_at,
      data.expiresAt,
      session.expires_at,
      session.expiresAt,
      assistantMeta.expires_at,
      assistantMeta.expiresAt,
      controlMode.expires_at,
      controlMode.expiresAt,
    ),
    ttlSeconds: firstNumber(
      data.ttl_seconds,
      data.ttlSeconds,
      session.ttl_seconds,
      session.ttlSeconds,
      assistantMeta.ttl_seconds,
      assistantMeta.ttlSeconds,
      controlMode.ttl_seconds,
      controlMode.ttlSeconds,
    ),
    provider,
    commandsEnabled: firstBoolean(
      data.commands_enabled,
      data.commandsEnabled,
      data.command_enabled,
      data.commandEnabled,
      commandState.enabled,
      commandState.commands_enabled,
      assistantMeta.commands_enabled,
      assistantMeta.commandsEnabled,
      controlMode.active,
    ),
    commandRef: firstString(
      commandState.command_id,
      commandState.commandId,
      commandMeta.commandId,
      commandMeta.command_id,
    ),
    contextSnapshotAt: firstString(
      context.snapshot_at,
      context.snapshotAt,
      data.context_snapshot_at,
      data.contextSnapshotAt,
      assistantMeta.context_snapshot_at,
      assistantMeta.contextSnapshotAt,
      meta.snapshot_at,
    ),
    contextPackId: firstString(
      context.context_pack_id,
      context.contextPackId,
      data.context_pack_id,
      data.contextPackId,
      session.context_pack_id,
      session.contextPackId,
      assistantMeta.context_pack_id,
      assistantMeta.contextPackId,
    ),
    auditRefs: uniqueStrings([
      data.audit_refs,
      data.auditRefs,
      session.audit_refs,
      session.auditRefs,
      meta.audit_id,
      meta.auditId,
      meta.audit_refs,
      meta.auditRefs,
      assistantMeta.audit_id,
      assistantMeta.auditId,
      assistantMeta.audit_refs,
      assistantMeta.auditRefs,
    ]),
    canViewKernelControls: explicitKernelVisibility ?? undefined,
  };
}

function normalizeCitation(item: unknown, index: number): SourceCitation | null {
  const record = recordFrom(item);
  const sourceId = firstString(record.source_id, record.sourceId, record.id, record.ref, record.href);
  if (!sourceId) return null;
  return {
    source_id: sourceId || `source-${index + 1}`,
    href: firstString(record.href, record.url, record.route) ?? undefined,
    snapshot_at: firstString(record.snapshot_at, record.snapshotAt, record.checked_at, record.checkedAt) ?? undefined,
    status: firstString(record.status, record.staleness) ?? undefined,
  };
}

function normalizeSourceCitations(value: unknown): SourceCitation[] {
  const root = recordFrom(value);
  const data = recordFrom(root.data ?? root);
  const context = recordFrom(data.context_pack ?? data.context ?? root.context_pack);
  const messages = arrayFrom(data.messages ?? data.transcript ?? root.messages ?? root.transcript);
  const candidates = [
    ...arrayFrom(root.sources),
    ...arrayFrom(root.source_citations),
    ...arrayFrom(root.sourceCitations),
    ...arrayFrom(data.sources),
    ...arrayFrom(data.source_citations),
    ...arrayFrom(data.sourceCitations),
    ...arrayFrom(context.sources),
    ...messages.flatMap((message) => {
      const record = recordFrom(message);
      return arrayFrom(record.citations ?? record.sources);
    }),
  ];
  const seen = new Set<string>();
  return candidates
    .map(normalizeCitation)
    .filter((citation): citation is SourceCitation => Boolean(citation))
    .filter((citation) => {
      const key = `${citation.source_id}:${citation.href ?? ""}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
}

function transcriptMessages(value: unknown): Array<{ role: string; content: string }> {
  const root = recordFrom(value);
  const dataValue = root.data ?? root;
  const data = recordFrom(dataValue);
  const session = recordFrom(data.session ?? root.session);
  const raw = Array.isArray(dataValue)
    ? dataValue
    : arrayFrom(data.transcript ?? data.messages ?? session.transcript ?? session.messages);
  return raw.map((it) => {
    const record = recordFrom(it);
    return {
      role: firstString(record.role, record.sender) ?? "assistant",
      content: firstString(record.content, record.delta, record.message) ?? "",
    };
  });
}

function eventPayload(raw: unknown): { type: string | null; data: JsonRecord } {
  const payload = recordFrom(raw);
  const data = recordFrom(payload.data);
  return {
    type: firstString(payload.type, payload.event, data.type, data.event),
    data: Object.keys(data).length > 0 ? data : payload,
  };
}

function recentTurns(messages: Array<{ role: string; content: string }>): Array<Record<string, unknown>> {
  return messages.slice(-8).map((message) => ({
    role: message.role,
    content: message.content.slice(0, 1200),
  }));
}

function taskCount(value: unknown): number {
  const root = recordFrom(value);
  const data = recordFrom(root.data ?? root);
  return arrayFrom(data.tasks).length;
}

function workerCount(value: unknown): number {
  const root = recordFrom(value);
  const data = recordFrom(root.data ?? root);
  return arrayFrom(data.workers).length;
}

function systemStatusData(value: unknown): JsonRecord {
  const root = recordFrom(value);
  return recordFrom(root.data ?? root);
}

function openClawToolPolicy(value: unknown): JsonRecord {
  const data = systemStatusData(value);
  return recordFrom(data.openclawToolPolicy ?? data.openclaw_tool_policy);
}

function assistantSkillDescriptors(value: unknown): JsonRecord[] {
  const policy = openClawToolPolicy(value);
  return arrayFrom(policy.effectiveSkills ?? policy.effective_skills)
    .map(recordFrom)
    .filter((skill) => Boolean(firstString(skill.id)));
}

function assistantSkillId(skill: JsonRecord): string {
  return firstString(skill.id) ?? "assistant.catalog.skill";
}

function assistantSkillLabel(skill: JsonRecord): string {
  return firstString(skill.title, skill.label, skill.name, skill.id) ?? "Assistant action";
}

function assistantSkillHandlerRef(skill: JsonRecord | null): string | null {
  if (!skill) return null;
  return firstString(skill.handler_ref, skill.handlerRef);
}

function assistantSkillInputSchema(skill: JsonRecord): JsonRecord {
  return recordFrom(skill.input_schema ?? skill.inputSchema);
}

function assistantSkillInputProperties(skill: JsonRecord): JsonRecord {
  return recordFrom(assistantSkillInputSchema(skill).properties);
}

function assistantSkillRequiredInputs(skill: JsonRecord): string[] {
  return uniqueStrings(arrayFrom(assistantSkillInputSchema(skill).required));
}

function assistantSkillConfirmRequired(skill: JsonRecord): boolean {
  const policy = recordFrom(skill.confirm_policy ?? skill.confirmPolicy);
  return firstBoolean(policy.required, skill.requires_confirmation, skill.requiresConfirmation) === true;
}

function assistantSkillRenderSurface(skill: JsonRecord): CatalogRenderSurface {
  const explicitSurface = firstString(
    skill.ui_surface,
    skill.uiSurface,
    skill.render_surface,
    skill.renderSurface,
  );
  const surface = (explicitSurface ?? firstString(skill.surface, skill.result_surface, skill.resultSurface) ?? "")
    .trim()
    .toLowerCase();
  if (["button", "toolbar", "toolbar_button"].includes(surface)) return "button";
  if (["card_action", "card-action", "degraded_card_action", "degraded-card-action"].includes(surface)) {
    return "card_action";
  }
  if (["command", "assistant_command", "openclaw_tool", "openclaw_workflow"].includes(surface)) {
    const handlerRef = assistantSkillHandlerRef(skill);
    return assistantCatalogRouteFromHandlerRef(handlerRef) ? "button" : "command";
  }
  return assistantCatalogRouteFromHandlerRef(assistantSkillHandlerRef(skill)) ? "button" : "command";
}

function hasInputProperty(properties: JsonRecord, name: string): boolean {
  return Object.prototype.hasOwnProperty.call(properties, name);
}

function setDescriptorInput(
  body: JsonRecord,
  properties: JsonRecord,
  name: string,
  value: unknown,
): void {
  if (!hasInputProperty(properties, name)) return;
  if (value === undefined || value === null) return;
  body[name] = value;
}

function catalogSkillInputBody(
  skill: JsonRecord,
  options: {
    prompt: string;
    sessionId: string | null;
    uiContext: AssistantUiContextV1;
    openClawMode: AssistantMode;
    controlMode?: ManagementAskControlMode;
    openclaw?: ManagementAskOpenClaw;
    recentTurns?: Array<Record<string, unknown>>;
  },
): JsonRecord {
  const properties = assistantSkillInputProperties(skill);
  const body: JsonRecord = {};
  const featureSummary = options.prompt.trim() || (options.sessionId ? `Assistant follow-up for ${options.sessionId}` : "");
  const affectedModules = ["execute-plans", "assistant", "management_ai"];
  const contextPackRequest = {
    mode: options.openClawMode,
    include: [
      "ui",
      "control_room",
      "jobs",
      "alerts",
      "audit",
      "recent_sse",
      "persona_health",
      "strategy_health",
      "management_nl",
      "docs_rag",
      "repo_status",
    ],
    question: options.prompt,
    frontend: {
      route: options.uiContext.route.path,
      visibleErrors: options.uiContext.visibleErrors,
      contextRefs: options.uiContext.contextRefs,
    },
  };
  const conversation = {
    source: "client_hint",
    recentTurns: options.recentTurns ?? [],
  };
  const metadata = assistantUiContextMetadata(options.uiContext, { source: "ask-personas-ui" });

  setDescriptorInput(body, properties, "question", options.prompt);
  setDescriptorInput(body, properties, "prompt", options.prompt);
  setDescriptorInput(body, properties, "sessionId", options.sessionId);
  setDescriptorInput(body, properties, "session_id", options.sessionId);
  setDescriptorInput(body, properties, "focus", "all");
  setDescriptorInput(body, properties, "controlMode", options.controlMode);
  setDescriptorInput(body, properties, "control_mode", options.controlMode);
  setDescriptorInput(body, properties, "openclaw", options.openclaw);
  setDescriptorInput(body, properties, "ui", options.uiContext);
  setDescriptorInput(body, properties, "conversation", conversation);
  setDescriptorInput(body, properties, "metadata", metadata);
  setDescriptorInput(body, properties, "provider", "codex");
  setDescriptorInput(body, properties, "reason", "management_ai_frontend_catalog_skill");
  setDescriptorInput(body, properties, "conversationId", options.sessionId);
  setDescriptorInput(body, properties, "conversation_id", options.sessionId);
  setDescriptorInput(body, properties, "featureSummary", featureSummary);
  setDescriptorInput(body, properties, "feature_summary", featureSummary);
  setDescriptorInput(body, properties, "affectedModules", affectedModules);
  setDescriptorInput(body, properties, "affected_modules", affectedModules);
  setDescriptorInput(body, properties, "archive", true);
  setDescriptorInput(body, properties, "emitTaskPacket", true);
  setDescriptorInput(body, properties, "emit_task_packet", true);
  setDescriptorInput(body, properties, "queueTaskPacket", true);
  setDescriptorInput(body, properties, "queue_task_packet", true);
  setDescriptorInput(body, properties, "contextPackRequest", contextPackRequest);
  setDescriptorInput(body, properties, "context_pack_request", contextPackRequest);
  setDescriptorInput(body, properties, "contextPack", { ui: options.uiContext });
  setDescriptorInput(body, properties, "context_pack", { ui: options.uiContext });
  setDescriptorInput(body, properties, "extraContext", {
    route: options.uiContext.route.path,
    visibleErrors: options.uiContext.visibleErrors,
    contextRefs: options.uiContext.contextRefs,
  });
  setDescriptorInput(body, properties, "extra_context", {
    route: options.uiContext.route.path,
    visibleErrors: options.uiContext.visibleErrors,
    contextRefs: options.uiContext.contextRefs,
  });
  return body;
}

function missingCatalogSkillInputs(skill: JsonRecord, body: JsonRecord): string[] {
  return assistantSkillRequiredInputs(skill).filter((name) => {
    const value = body[name];
    if (value === undefined || value === null) return true;
    if (typeof value === "string" && !value.trim()) return true;
    if (Array.isArray(value) && value.length === 0) return true;
    return false;
  });
}

function catalogSkillDisabledReason(skill: JsonRecord, body: JsonRecord): string | null {
  const handlerRef = assistantSkillHandlerRef(skill);
  if (!handlerRef || !assistantCatalogRouteFromHandlerRef(handlerRef)) {
    return "handler is not a frontend-routable BFF route";
  }
  const missing = missingCatalogSkillInputs(skill, body);
  if (missing.length > 0) return `missing ${missing.join(", ")}`;
  return null;
}

function systemProviderReadiness(value: unknown): JsonRecord {
  const data = systemStatusData(value);
  return recordFrom(data.providerReadiness ?? data.provider_readiness);
}

function providerReadinessLine(value: unknown): string {
  const provider = systemProviderReadiness(value);
  const providerName = firstString(provider.providerName, provider.provider_name, provider.provider, provider.name) ?? "provider";
  const status = firstString(provider.status) ?? "unknown";
  const ready = firstBoolean(provider.ready);
  const readyText = ready === true ? "ready" : ready === false ? "not ready" : null;
  const auth = firstString(provider.authStatus, provider.auth_status, provider.auth);
  const checkedAt = firstString(provider.checkedAt, provider.checked_at);
  const reason = firstString(provider.degradedReason, provider.degraded_reason, provider.reason);
  return [
    `${providerName}: ${status}`,
    readyText,
    auth ? `auth ${auth}` : null,
    checkedAt ? `checked ${checkedAt}` : null,
    reason ? `reason ${reason}` : null,
  ].filter(Boolean).join(" - ");
}

function repairWorkspaceLine(value: unknown): string {
  const provider = systemProviderReadiness(value);
  const workspace = recordFrom(provider.repairWorkspace ?? provider.repair_workspace);
  const status = firstString(workspace.status) ?? "unknown";
  const ready = firstBoolean(workspace.ready);
  const readyText = ready === true ? "ready" : ready === false ? "not ready" : null;
  const root = firstString(workspace.root);
  const writable = firstBoolean(workspace.writable);
  const count = firstNumber(workspace.worktreeCount, workspace.worktree_count) ?? 0;
  return [
    `status ${status}`,
    readyText,
    writable === true ? "writable" : writable === false ? "not writable" : null,
    `worktrees ${count}`,
    root ? `root ${root}` : null,
  ].filter(Boolean).join(" - ");
}

function assistantDevBridgeStatus(value: unknown): JsonRecord {
  const data = systemStatusData(value);
  return recordFrom(data.assistantDevBridge ?? data.assistant_dev_bridge);
}

function assistantDevBridgeLine(value: unknown): string {
  const bridge = assistantDevBridgeStatus(value);
  const inbox = recordFrom(bridge.inbox);
  const status = firstString(bridge.status) ?? "unknown";
  const pending = firstNumber(inbox.pendingCount, inbox.pending_count) ?? 0;
  const processed = firstNumber(inbox.processedCount, inbox.processed_count) ?? 0;
  const failed = firstNumber(inbox.failedCount, inbox.failed_count) ?? 0;
  const receipts = firstNumber(inbox.receiptCount, inbox.receipt_count) ?? 0;
  const lastDrain = firstString(bridge.lastDrainAt, bridge.last_drain_at);
  return [
    `status ${status}`,
    `pending ${pending}`,
    `processed ${processed}`,
    `failed ${failed}`,
    `receipts ${receipts}`,
    lastDrain ? `last drain ${lastDrain}` : null,
  ].filter(Boolean).join(" - ");
}

function packetId(value: unknown): string | null {
  const root = recordFrom(value);
  const data = recordFrom(root.data ?? root);
  return firstString(data.packetId, data.packet_id);
}

function taskPacketId(value: unknown): string | null {
  const root = recordFrom(value);
  const meta = recordFrom(root.meta);
  const taskPacket = recordFrom(meta.taskPacket ?? meta.task_packet);
  const data = recordFrom(root.data ?? root);
  return firstString(taskPacket.packetId, taskPacket.packet_id, data.packetId, data.packet_id);
}

function taskPacketRecord(value: unknown): JsonRecord | null {
  const root = recordFrom(value);
  const meta = recordFrom(root.meta);
  const direct = recordFrom(meta.taskPacket ?? meta.task_packet);
  if (Object.keys(direct).length > 0) return direct;
  const data = recordFrom(root.data ?? root);
  const packet = recordFrom(data.taskPacket ?? data.task_packet ?? root.taskPacket ?? root.task_packet);
  return Object.keys(packet).length > 0 ? packet : null;
}

function devBridgeRecord(value: unknown): JsonRecord {
  const root = recordFrom(value);
  const meta = recordFrom(root.meta);
  return recordFrom(meta.devBridge ?? meta.dev_bridge);
}

function packetTasks(value: unknown): JsonRecord[] {
  const packet = taskPacketRecord(value);
  if (!packet) return [];
  return arrayFrom(packet.tasks).map(recordFrom).filter((task) => Object.keys(task).length > 0);
}

function devDocTaskCount(value: unknown): number {
  const root = recordFrom(value);
  const data = recordFrom(root.data ?? root);
  const tasks = arrayFrom(data.executionTasks ?? data.execution_tasks ?? data.tasks);
  return tasks.length || packetTasks(value).length;
}

function devDocArtifactPaths(value: unknown): string[] {
  const root = recordFrom(value);
  const data = recordFrom(root.data ?? root);
  const tasks = [
    ...arrayFrom(data.executionTasks ?? data.execution_tasks ?? data.tasks).map(recordFrom),
    ...packetTasks(value),
  ];
  return uniqueStrings(tasks.flatMap((task) => arrayFrom(task.artifacts ?? task.artifactPaths ?? task.artifact_paths)));
}

function firstPacketTaskId(value: unknown): string | null {
  const firstTask = packetTasks(value)[0];
  return firstString(firstTask?.id, firstTask?.taskId, firstTask?.task_id);
}

function buildAssistantDevDispatchCommand(value: unknown): string | null {
  const packet = taskPacketRecord(value);
  if (!packet) return null;
  const payload = JSON.stringify({ taskPacket: packet }, null, 2);
  const queueCommand =
    firstString(devBridgeRecord(value).queueCommand, devBridgeRecord(value).queue_command)
    ?? "python3 scripts/queue_assistant_dev_task_packet.py";
  return `${queueCommand} <<'JSON'\n${payload}\nJSON`;
}

export default function AskPersonas(): JSX.Element {
  const [prompt, setPrompt] = useState("");
  const [messages, setMessages] = useState<Array<{ role: string; content: string }>>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [mode, setMode] = useState<AssistantMode | null>(null);
  const [assistantSignals, setAssistantSignals] = useState<AssistantModeSignals>({});
  const [sourceCitations, setSourceCitations] = useState<SourceCitation[]>([]);
  const [systemStatus, setSystemStatus] = useState<JsonRecord | null>(null);
  const [devDocResult, setDevDocResult] = useState<JsonRecord | null>(null);
  const [catalogResultSkill, setCatalogResultSkill] = useState<JsonRecord | null>(null);
  const [catalogActionPending, setCatalogActionPending] = useState<string | null>(null);
  const [workflowError, setWorkflowError] = useState<string | null>(null);
  const [openClawMode, setOpenClawMode] = useState<AssistantMode>("kernel_debug");
  const [repairTaskId, setRepairTaskId] = useState("");
  const [repairWorktree, setRepairWorktree] = useState("");
  const [repairScope, setRepairScope] = useState("");
  const [handoffCopied, setHandoffCopied] = useState<string | null>(null);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    let cancelled = false;
    void getAssistantOrchestratorStatus()
      .then((res) => {
        if (!cancelled) setSystemStatus(recordFrom(res));
      })
      .catch(() => undefined);

    return () => {
      cancelled = true;
      if (esRef.current) {
        esRef.current.close();
      }
    };
  }, []);

  const appendDelta = (delta: string, role = "assistant") => {
    setMessages((m) => [...m, { role, content: delta }]);
  };

  const currentUiContext = (): AssistantUiContextV1 =>
    buildAssistantUiContext({
      visibleSurface: {
        workbench: "Platform Admin / Agora",
        screenId: "screen-agora-ask-personas",
        componentId: "surface-management-assistant-panel",
        heading: "Ask Personas",
      },
      formRegistry: {
        formId: "ask-personas-prompt",
        action: {
          kind: "bff_route",
          method: "POST",
          href: "/bff/management/nl/ask",
          idempotencyRequired: true,
          submitAuthority: "bff",
        },
        fields: [
          {
            name: "prompt",
            label: "Prompt",
            value: prompt,
            valueState: prompt.trim() ? "present" : "empty",
            dirty: Boolean(prompt.trim()),
            required: true,
            validatorRefs: [{ type: "required", message: "Prompt is required before ask." }],
          },
          {
            name: "openclawMode",
            label: "OpenClaw mode",
            value: openClawMode ?? "user",
            valueState: openClawMode ? "present" : "empty",
            dirty: openClawMode !== "kernel_debug",
            required: false,
            validatorRefs: [],
          },
          {
            name: "repairTaskId",
            label: "Repair task",
            value: repairTaskId,
            valueState: repairTaskId.trim() ? "present" : "empty",
            dirty: Boolean(repairTaskId.trim()),
            required: openClawMode === "kernel_repair",
            validatorRefs: openClawMode === "kernel_repair"
              ? [{ type: "required", message: "Repair task is required in kernel repair mode." }]
              : [],
          },
          {
            name: "repairWorktree",
            label: "Repair worktree",
            value: repairWorktree,
            valueState: repairWorktree.trim() ? "present" : "empty",
            dirty: Boolean(repairWorktree.trim()),
            required: openClawMode === "kernel_repair",
            validatorRefs: openClawMode === "kernel_repair"
              ? [{ type: "required", message: "Repair worktree is required in kernel repair mode." }]
              : [],
          },
          {
            name: "repairScope",
            label: "Repair scope",
            value: repairScope,
            valueState: repairScope.trim() ? "present" : "empty",
            dirty: Boolean(repairScope.trim()),
            required: openClawMode === "kernel_repair",
            validatorRefs: openClawMode === "kernel_repair"
              ? [{ type: "required", message: "Repair scope is required in kernel repair mode." }]
              : [],
          },
        ],
        dirty: Boolean(prompt.trim()),
        errors: prompt.trim() ? [] : [{ field: "prompt", message: "Prompt is empty.", code: "required" }],
      },
      visibleErrors: workflowError ? [{ message: workflowError, source: "assistant-workflow" }] : [],
      contextRefs: sessionId
        ? [
            {
              sourceId: "assistant_transcript",
              href: `/bff/assistant/sessions/${sessionId}/transcript`,
              label: "Assistant transcript",
            },
          ]
        : [],
    });

  const repairScopeItems = (): string[] =>
    repairScope
      .split(/[\n,;]/)
      .map((item) => item.trim())
      .filter(Boolean);

  const openClawRepairPayload = (): ManagementAskOpenClaw | undefined => {
    if (openClawMode !== "kernel_repair") return undefined;
    const repair: ManagementAskRepair = {};
    if (repairTaskId.trim()) {
      repair.taskId = repairTaskId.trim();
      repair.expectedBranch = `task/${repairTaskId.trim()}`;
    }
    if (repairWorktree.trim()) repair.taskWorktree = repairWorktree.trim();
    const scope = repairScopeItems();
    if (scope.length > 0) repair.declaredScope = scope;
    repair.mergeTarget = "dev";
    repair.requireClean = true;
    return Object.keys(repair).length > 0 ? { repair } : undefined;
  };

  const controlModePayload = (): ManagementAskControlMode | undefined => {
    if (!openClawMode || openClawMode === "user") return undefined;
    return {
      mode: openClawMode,
      reason: "management_ai_frontend_openclaw_mode",
      ttlSeconds: 1800,
    };
  };

  const handleSend = async () => {
    if (!prompt.trim()) return;
    setMessages([{ role: "operator", content: prompt }]);
    setStatus("sending");
    setMode(null);
    setAssistantSignals({});
    setSourceCitations([]);
    setWorkflowError(null);

    try {
      const uiContext = currentUiContext();
      const body = {
        prompt,
        persona_ids: [],
        frontend: {
          route: uiContext.route.path,
          visibleErrors: uiContext.visibleErrors,
          contextRefs: uiContext.contextRefs,
        },
        metadata: assistantUiContextMetadata(uiContext, { source: "ask-personas-ui" }),
        conversation: {
          source: "client_hint",
          recentTurns: recentTurns(messages),
        },
      };
      const res = await postAsk(body);
      // res may be assistant envelope or Agora compatibility envelope.
      const data = res?.data ?? res;
      const sessionRecord = recordFrom(data?.session);
      const id = firstString(
        data?.session_id,
        res?.session_id,
        sessionRecord.sessionId,
        sessionRecord.session_id,
        sessionRecord.id,
      );
      if (!id) throw new Error("no session id returned");
      setSessionId(id);
      const sessionSignals = normalizeAssistantSignals(res, id);
      const sessionMode = normalizeMode(sessionSignals.mode);
      setMode(sessionMode);
      setAssistantSignals(sessionSignals);
      const initialCitations = normalizeSourceCitations(res);
      if (initialCitations.length > 0) {
        setSourceCitations(initialCitations);
      }
      setStatus("active");

      // subscribe to SSE deltas
      if (esRef.current) esRef.current.close();
      const es = openAskSse((ev) => {
        try {
          const parsed = JSON.parse(ev.data);
          const payload = eventPayload(parsed);
          // Supports both {type,data:{session_id,...}} and legacy top-level events.
          if (payload.data.session_id !== id && payload.data.sessionId !== id) return;
          const type = payload.type ?? "";
          const eventSignals = normalizeAssistantSignals(parsed, id);
          setAssistantSignals((signals) => ({
            ...signals,
            ...eventSignals,
            mode: eventSignals.mode ?? signals.mode,
          }));
          if (type === "delta" || type === "ask.message.delta" || type.endsWith(".delta")) {
            appendDelta(String(payload.data.delta ?? payload.data.content ?? ""));
            const citations = normalizeSourceCitations(payload.data);
            if (citations.length > 0) setSourceCitations(citations);
          }
          if (type === "completed" || type === "ask.message.completed" || type.endsWith(".completed")) {
            setStatus("completed");
            // extract source citations from SSE completion event
            const citations = normalizeSourceCitations(payload.data);
            if (citations.length > 0) {
              setSourceCitations(citations);
            }
            // fetch final transcript
            void (async () => {
              try {
                const s = await getAskSession(id);
                const final = transcriptMessages(s);
                if (final.length > 0) {
                  setMessages((m) => [...m, ...final]);
                }
                const updatedSignals = normalizeAssistantSignals(s, id);
                setAssistantSignals((signals) => ({
                  ...signals,
                  ...updatedSignals,
                  mode: updatedSignals.mode ?? signals.mode,
                }));
                setMode(normalizeMode(updatedSignals.mode) ?? sessionMode);
                // also pick up source citations from transcript response
                const fetchedCitations = normalizeSourceCitations(s);
                if (fetchedCitations.length > 0) {
                  setSourceCitations(fetchedCitations);
                }
              } catch (e) {
                // ignore
              }
            })();
          }
        } catch (e) {
          // ignore parse errors
        }
      });
      esRef.current = es;
    } catch (err) {
      setStatus("error");
      appendDelta(`Error: ${String(err)}`, "assistant");
    }
  };

  const handleCatalogAction = async (skill: JsonRecord, surface: CatalogRenderSurface) => {
    const skillId = assistantSkillId(skill);
    const label = assistantSkillLabel(skill);
    const handlerRef = assistantSkillHandlerRef(skill);
    const uiContext = currentUiContext();
    const openclaw = openClawRepairPayload();
    const controlMode = controlModePayload();
    const body = catalogSkillInputBody(skill, {
      prompt,
      sessionId,
      uiContext,
      openClawMode,
      controlMode,
      openclaw,
      recentTurns: recentTurns(messages),
    });
    const disabledReason = catalogSkillDisabledReason(skill, body);
    if (disabledReason) {
      setWorkflowError(`${label} is unavailable: ${disabledReason}.`);
      return;
    }
    if (!handlerRef) {
      setWorkflowError(`${label} is unavailable: missing handler.`);
      return;
    }
    if (assistantSkillConfirmRequired(skill)) {
      const ok = typeof window === "undefined"
        ? true
        : window.confirm(`${label} requires confirmation. Continue?`);
      if (!ok) return;
    }
    setWorkflowError(null);
    setStatus(`catalog_${surface}_running`);
    setCatalogActionPending(skillId);
    try {
      const res = await invokeAssistantCatalogRoute(handlerRef, body);
      const resultSurface = firstString(skill.result_surface, skill.resultSurface);
      if (resultSurface === "assistant_management_answer") {
        const data = recordFrom(res.data ?? res);
        const id = firstString(data.sessionId, data.session_id, data.session);
        if (id) setSessionId(id);
        const answer = firstString(data.answer);
        if (answer) {
          setMessages([{ role: "operator", content: prompt }, { role: "assistant", content: answer }]);
        }
        const signals = normalizeAssistantSignals(res, id);
        setAssistantSignals(signals);
        setMode(normalizeMode(signals.mode));
        setStatus(
          firstString(recordFrom(data.providerStatus ?? data.provider_status).status, data.status) ?? "completed",
        );
        const citations = normalizeSourceCitations(res);
        if (citations.length > 0) setSourceCitations(citations);
        return;
      }
      if (resultSurface === "assistant_orchestrator_status") {
        setSystemStatus(res);
        setStatus("system_status_loaded");
        return;
      }
      if (resultSurface === "assistant_control_mode_status") {
        const signals = normalizeAssistantSignals(res, sessionId);
        setAssistantSignals((current) => ({
          ...current,
          ...signals,
          mode: signals.mode ?? current.mode,
        }));
        setMode(normalizeMode(signals.mode) ?? mode);
        setStatus("control_mode_loaded");
        return;
      }
      if (resultSurface === "assistant_transcript_resync") {
        const final = transcriptMessages(res);
        setMessages(final);
        const record = recordFrom(res.data ?? res);
        setStatus(firstString(record.status) ?? "transcript_resynced");
        const updatedSignals = normalizeAssistantSignals(res, sessionId);
        setMode(normalizeMode(updatedSignals.mode));
        setAssistantSignals(updatedSignals);
        const citations = normalizeSourceCitations(res);
        if (citations.length > 0) {
          setSourceCitations(citations);
        }
        return;
      }
      setDevDocResult(recordFrom(res));
      setCatalogResultSkill(skill);
      setHandoffCopied(null);
      const generatedTaskId = firstPacketTaskId(res);
      if (generatedTaskId && !repairTaskId.trim()) {
        setRepairTaskId(generatedTaskId);
      }
      const generatedScope = devDocArtifactPaths(res);
      if (generatedScope.length > 0 && !repairScope.trim()) {
        setRepairScope(generatedScope.join("\n"));
      }
      setStatus(`catalog_${surface}_ready`);
    } catch (err) {
      const message = `${label} failed: ${String(err)}`;
      setStatus("error");
      setWorkflowError(message);
      appendDelta(message, "assistant");
    } finally {
      setCatalogActionPending(null);
    }
  };

  const handleCopyDispatchCommand = async () => {
    const command = buildAssistantDevDispatchCommand(devDocResult);
    if (!command) return;
    if (typeof navigator === "undefined" || !navigator.clipboard?.writeText) {
      setWorkflowError("Clipboard unavailable; dispatch command is visible below.");
      return;
    }
    try {
      await navigator.clipboard.writeText(command);
      setHandoffCopied("dispatch_command");
    } catch (err) {
      setWorkflowError(`Copy failed: ${String(err)}`);
    }
  };

  const devBridge = devDocResult ? devBridgeRecord(devDocResult) : {};
  const devArtifactPaths = devDocResult ? devDocArtifactPaths(devDocResult) : [];
  const devDispatchCommand = devDocResult ? buildAssistantDevDispatchCommand(devDocResult) : null;
  const catalogSkills = systemStatus ? assistantSkillDescriptors(systemStatus) : [];
  const catalogToolbarSkills = catalogSkills.filter((skill) => assistantSkillRenderSurface(skill) === "button");
  const catalogCommandSkills = catalogSkills.filter((skill) => assistantSkillRenderSurface(skill) === "command");
  const catalogCardActionSkills = catalogSkills.filter((skill) => assistantSkillRenderSurface(skill) === "card_action");
  const catalogResultLabel =
    firstString(catalogResultSkill?.title, catalogResultSkill?.label, catalogResultSkill?.result_surface, catalogResultSkill?.resultSurface)
    ?? "Catalog result";

  const renderCatalogAction = (skill: JsonRecord, surface: CatalogRenderSurface) => {
    const skillId = assistantSkillId(skill);
    const label = assistantSkillLabel(skill);
    const body = catalogSkillInputBody(skill, {
      prompt,
      sessionId,
      uiContext: currentUiContext(),
      openClawMode,
      controlMode: controlModePayload(),
      openclaw: openClawRepairPayload(),
      recentTurns: recentTurns(messages),
    });
    const disabledReason = catalogSkillDisabledReason(skill, body);
    const busy = catalogActionPending === skillId;
    const disabled = Boolean(disabledReason) || Boolean(catalogActionPending);
    const required = assistantSkillRequiredInputs(skill);
    return (
      <button
        key={`${surface}:${skillId}`}
        onClick={() => void handleCatalogAction(skill, surface)}
        disabled={disabled}
        title={[
          disabledReason,
          required.length > 0 ? `requires ${required.join(", ")}` : null,
          assistantSkillConfirmRequired(skill) ? "confirmation required" : null,
        ].filter(Boolean).join(" - ")}
        data-skill-id={skillId}
        data-skill-surface={surface}
      >
        {busy ? "Running..." : label}
        {assistantSkillConfirmRequired(skill) ? " *" : ""}
      </button>
    );
  };

  return (
    <div>
      <h2>Ask Personas</h2>
      <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={6} cols={80} />
      <div style={{ display: "grid", gap: 8, gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", marginTop: 8 }}>
        <label>
          <span>OpenClaw mode</span>
          <select
            value={openClawMode ?? "kernel_debug"}
            onChange={(event) => setOpenClawMode(normalizeMode(event.target.value) ?? "kernel_debug")}
          >
            <option value="user">User</option>
            <option value="kernel_observe">Kernel observe</option>
            <option value="kernel_debug">Kernel debug</option>
            <option value="kernel_repair">Kernel repair</option>
          </select>
        </label>
        {openClawMode === "kernel_repair" && (
          <>
            <label>
              <span>Task ID</span>
              <input value={repairTaskId} onChange={(event) => setRepairTaskId(event.target.value)} />
            </label>
            <label>
              <span>Task worktree</span>
              <input value={repairWorktree} onChange={(event) => setRepairWorktree(event.target.value)} />
            </label>
            <label>
              <span>Declared scope</span>
              <textarea value={repairScope} onChange={(event) => setRepairScope(event.target.value)} rows={3} />
            </label>
          </>
        )}
      </div>
      <div>
        <button onClick={handleSend} disabled={status === "sending" || !prompt.trim()}>
          Ask
        </button>
        {catalogToolbarSkills.map((skill) => renderCatalogAction(skill, "button"))}
      </div>
      {catalogCommandSkills.length > 0 && (
        <div data-testid="assistant-catalog-commands" style={{ border: "1px solid #ddd", marginTop: 8, padding: 8 }}>
          <strong>Catalog commands:</strong>{" "}
          {catalogCommandSkills.map((skill) => renderCatalogAction(skill, "command"))}
        </div>
      )}
      <div>
        <strong>Status:</strong> {status ?? "idle"} {sessionId ? `(session ${sessionId})` : null}
      </div>
      {workflowError && (
        <div role="alert" style={{ border: "1px solid #fca5a5", color: "#991b1b", marginTop: 8, padding: 8 }}>
          {workflowError}
          {catalogCardActionSkills.length > 0 && (
            <div data-testid="assistant-catalog-card-actions" style={{ marginTop: 8 }}>
              {catalogCardActionSkills.map((skill) => renderCatalogAction(skill, "card_action"))}
            </div>
          )}
        </div>
      )}
      {!isUserMode(mode) && isKernelMode(mode) && (
        <AssistantModeBadge signals={{ ...assistantSignals, mode, sessionId }} />
      )}
      {systemStatus && (
        <div style={{ border: "1px solid #ddd", marginTop: 8, padding: 8 }}>
          <strong>System:</strong> {taskCount(systemStatus)} tasks, {workerCount(systemStatus)} workers
          <div>
            <strong>Provider:</strong> {providerReadinessLine(systemStatus)}
          </div>
          <div>
            <strong>Repair workspace:</strong> {repairWorkspaceLine(systemStatus)}
          </div>
          <div>
            <strong>Dev inbox:</strong> {assistantDevBridgeLine(systemStatus)}
          </div>
          <div>
            <strong>Effective catalog:</strong> {catalogSkills.length} skills
          </div>
        </div>
      )}
      {devDocResult && (
        <div data-testid="assistant-dev-handoff" style={{ border: "1px solid #ddd", marginTop: 8, padding: 8 }}>
          <strong>{catalogResultLabel}:</strong> {packetId(devDocResult) ?? "packet pending"}
          {taskPacketId(devDocResult) ? ` - task packet ${taskPacketId(devDocResult)}` : ""}
          <div>
            <strong>Tasks:</strong> {devDocTaskCount(devDocResult)}
            {devArtifactPaths.length > 0
              ? ` - ${devArtifactPaths.length} scoped artifacts`
              : ""}
          </div>
          <div>
            <strong>Bridge:</strong>{" "}
            {firstString(devBridge.dispatchMode, devBridge.dispatch_mode)
              ?? "repo_local_required"}
            {firstBoolean(devBridge.noDirectShellFromWeb, devBridge.no_direct_shell_from_web)
              ? " - web shell disabled"
              : ""}
            {" - supervisor pickup"}
          </div>
          {devDispatchCommand && (
            <div style={{ marginTop: 8 }}>
              <button onClick={handleCopyDispatchCommand}>Copy Supervisor Queue Command</button>
              {handoffCopied === "dispatch_command" && <span style={{ marginLeft: 8 }}>Copied</span>}
              <pre style={{ whiteSpace: "pre-wrap", maxHeight: 260, overflow: "auto", marginTop: 8 }}>
                {devDispatchCommand}
              </pre>
            </div>
          )}
        </div>
      )}
      <div>
        <h3>Messages</h3>
        <div style={{ whiteSpace: "pre-wrap", border: "1px solid #ddd", padding: 8 }}>
          {messages.map((m, i) => (
            <div key={i}><strong>{m.role}:</strong> {m.content}</div>
          ))}
        </div>
      </div>
      {/* Source citations: shown in both user and kernel modes */}
      {sourceCitations.length > 0 && (
        <div>
          <h4>Sources</h4>
          <ul>
            {sourceCitations.map((cite, i) => (
              <li key={i}>
                {cite.href ? (
                  <a href={cite.href} target="_blank" rel="noopener noreferrer">
                    {cite.source_id}
                  </a>
                ) : (
                  <span>{cite.source_id}</span>
                )}{" "}
                <span style={{ fontSize: "0.85em", color: "#555" }}>
                  ({cite.status ?? "source"}, {cite.snapshot_at ?? "snapshot pending"})
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {/* Kernel-only controls: not rendered for user mode */}
    </div>
  );
}
