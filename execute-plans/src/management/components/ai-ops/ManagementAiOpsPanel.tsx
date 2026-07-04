import { type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  Bot,
  CheckCircle2,
  FileText,
  MessageSquare,
  RefreshCw,
  Send,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/empty-state";
import { buildAssistantUiContext } from "@/lib/assistant/uiContextRegistry";
import {
  getManagementAssistantConversation,
  postManagementAssistantAsk,
  type ManagementAssistantAskRequest,
} from "@/lib/bff/managementAssistant";
import { cn } from "@/lib/utils";

export type ManagementAiOpsPanelMode = "ask" | "conversations";

export interface ManagementAiOpsPanelProps {
  mode?: ManagementAiOpsPanelMode;
  initialSessionId?: string;
  baseUrl?: string;
  className?: string;
}

type LoadState = "idle" | "loading" | "ready" | "error";
type AskState = "idle" | "submitting" | "success" | "degraded" | "error";
type Tone = "success" | "warning" | "running" | "failed" | "neutral";

interface LinkRef {
  key: string;
  label: string;
  href?: string;
}

interface AskView {
  answer: string;
  sessionId: string;
  traceId: string;
  confidence: string;
  status: string;
  degraded: boolean;
  providerStatus: Record<string, unknown>;
  providerMessage: string;
  conversationHref: string;
  sources: string[];
  refs: LinkRef[];
  actions: Array<Record<string, unknown>>;
}

interface TurnView {
  id: string;
  role: string;
  text: string;
  createdAt: string;
  providerStatus?: Record<string, unknown>;
  actions: Array<Record<string, unknown>>;
  attachments: Array<Record<string, unknown>>;
}

interface ConversationView {
  sessionId: string;
  title: string;
  href: string;
  turnCount: number;
  turns: TurnView[];
  degraded: boolean;
  degradedReason: string;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function asArray(value: unknown): unknown[] {
  if (Array.isArray(value)) return value;
  const record = asRecord(value);
  if (Array.isArray(record.items)) return record.items;
  if (Array.isArray(record.turns)) return record.turns;
  return [];
}

function textFrom(value: unknown, fallback = ""): string {
  const text = String(value ?? "").trim();
  return text || fallback;
}

function firstText(...values: unknown[]): string {
  for (const value of values) {
    const text = textFrom(value);
    if (text) return text;
  }
  return "";
}

function labelFrom(value: unknown, fallback = "unknown"): string {
  return textFrom(value, fallback).replace(/_/g, " ");
}

function numberFrom(value: unknown, fallback = 0): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function booleanFrom(value: unknown): boolean {
  if (value === true) return true;
  return ["true", "1", "yes"].includes(String(value ?? "").toLowerCase());
}

function responseData(response: unknown): Record<string, unknown> {
  const record = asRecord(response);
  const data = asRecord(record.data);
  return Object.keys(data).length > 0 ? data : record;
}

function routeModeFromLocation(): ManagementAiOpsPanelMode {
  if (typeof window === "undefined") return "ask";
  return window.location.pathname.includes("/management/ai/conversations")
    ? "conversations"
    : "ask";
}

function sessionIdFromLocation(): string {
  if (typeof window === "undefined") return "";
  const params = new URLSearchParams(window.location.search);
  const querySession = firstText(
    params.get("sessionId"),
    params.get("session_id"),
    params.get("conversationId"),
    params.get("id"),
  );
  if (querySession) return querySession;
  const match = window.location.pathname.match(/\/management\/ai\/conversations\/([^/?#]+)/);
  return match ? decodeURIComponent(match[1]) : "";
}

function toneForStatus(status: unknown): Tone {
  const normalized = String(status ?? "").toLowerCase();
  if (["completed", "complete", "ok", "ready", "accepted", "success"].includes(normalized)) {
    return "success";
  }
  if (["degraded", "disabled", "fallback", "partial"].includes(normalized)) return "warning";
  if (["loading", "pending", "processing", "running", "submitting"].includes(normalized)) return "running";
  if (["error", "failed", "failure", "forbidden", "unauthorized"].includes(normalized)) return "failed";
  return "neutral";
}

function toneClass(tone: Tone): string {
  if (tone === "success") return "bg-status-success/15 text-status-success border-status-success/30";
  if (tone === "warning") return "bg-status-warning/15 text-status-warning border-status-warning/30";
  if (tone === "running") return "bg-status-running/15 text-status-running border-status-running/30";
  if (tone === "failed") return "bg-status-failed/15 text-status-failed border-status-failed/30";
  return "bg-muted text-muted-foreground border-border";
}

function formatTime(value: unknown): string {
  const text = textFrom(value, "-");
  if (text === "-") return text;
  const parsed = new Date(text);
  return Number.isNaN(parsed.getTime()) ? text : parsed.toLocaleString();
}

function isHref(value: string): boolean {
  return value.startsWith("/") || /^[a-z][a-z0-9+.-]*:\/\//i.test(value);
}

function refsFromValue(key: string, value: unknown): LinkRef[] {
  if (Array.isArray(value)) {
    return value.flatMap((item, index) => refsFromValue(`${key}-${index + 1}`, item));
  }
  if (typeof value === "string") {
    const text = textFrom(value);
    if (!text) return [];
    return [{ key: `${key}:${text}`, label: labelFrom(key), href: isHref(text) ? text : undefined }];
  }
  const record = asRecord(value);
  if (Object.keys(record).length === 0) return [];
  const href = firstText(record.href, record.url, record.ref, record.path, record.storage_ref, record.storageRef);
  if (href) {
    return [{
      key: `${key}:${href}`,
      label: firstText(record.label, record.title, record.kind, labelFrom(key)),
      href: isHref(href) ? href : undefined,
    }];
  }
  return Object.entries(record)
    .filter(([, item]) => typeof item === "string")
    .flatMap(([childKey, item]) => refsFromValue(childKey, item));
}

function collectRefs(data: Record<string, unknown>): LinkRef[] {
  const refs: LinkRef[] = [];
  const conversation = asRecord(data.conversation);
  const conversationHref = firstText(conversation.href, conversation.url);
  if (conversationHref) {
    refs.push({
      key: `conversation:${conversationHref}`,
      label: "Conversation",
      href: isHref(conversationHref) ? conversationHref : undefined,
    });
  }
  for (const key of ["auditLog", "audit_log", "auditRef", "audit_ref", "evidenceRefs", "evidence_refs", "evidence"]) {
    refs.push(...refsFromValue(key, data[key]));
  }
  const seen = new Set<string>();
  return refs.filter((ref) => {
    const id = `${ref.label}:${ref.href ?? ref.key}`;
    if (seen.has(id)) return false;
    seen.add(id);
    return true;
  }).slice(0, 10);
}

function normalizeSources(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value
      .map((item) => {
        if (typeof item === "string") return item;
        const record = asRecord(item);
        return firstText(record.label, record.title, record.source_id, record.sourceId, record.href, record.id);
      })
      .filter(Boolean);
  }
  const text = textFrom(value);
  return text ? [text] : [];
}

function normalizeAsk(response: unknown): AskView {
  const data = responseData(response);
  const providerStatus = asRecord(data.provider_status ?? data.providerStatus);
  const session = asRecord(data.session);
  const conversation = asRecord(data.conversation);
  const status = textFrom(providerStatus.status ?? data.status, data.answer ? "completed" : "accepted");
  const providerMessage = firstText(
    providerStatus.display_message,
    providerStatus.displayMessage,
    providerStatus.reason,
    providerStatus.reason_code,
    providerStatus.reasonCode,
    data.message,
  );
  const sessionId = firstText(
    data.session_id,
    data.sessionId,
    session.session_id,
    session.sessionId,
    session.id,
    conversation.session_id,
    conversation.sessionId,
  );

  return {
    answer: firstText(data.answer, data.text, data.response, data.message),
    sessionId,
    traceId: firstText(data.trace_id, data.traceId),
    confidence: textFrom(data.confidence, "-"),
    status,
    degraded: toneForStatus(status) === "warning" || booleanFrom(data.degraded) || booleanFrom(providerStatus.degraded),
    providerStatus,
    providerMessage,
    conversationHref: firstText(conversation.href, conversation.url),
    sources: normalizeSources(data.sources),
    refs: collectRefs(data),
    actions: asArray(data.uiActions ?? data.ui_actions ?? data.actions)
      .map(asRecord)
      .filter((item) => Object.keys(item).length > 0),
  };
}

function normalizeTurn(value: unknown, index: number): TurnView | null {
  const record = asRecord(value);
  if (Object.keys(record).length === 0) return null;
  const providerStatus = asRecord(record.provider_status ?? record.providerStatus);
  return {
    id: firstText(record.id, record.turn_id, record.turnId, `turn-${index + 1}`),
    role: textFrom(record.role, "unknown"),
    text: firstText(record.text, record.content, record.message, record.answer) || "-",
    createdAt: firstText(record.createdAt, record.created_at, record.timestamp, record.at),
    providerStatus: Object.keys(providerStatus).length > 0 ? providerStatus : undefined,
    actions: asArray(record.actions).map(asRecord).filter((item) => Object.keys(item).length > 0),
    attachments: asArray(record.attachments).map(asRecord).filter((item) => Object.keys(item).length > 0),
  };
}

function normalizeConversation(response: unknown, fallbackSessionId: string): ConversationView {
  const envelope = asRecord(response);
  const data = responseData(response);
  const meta = asRecord(envelope.meta ?? data.meta);
  const session = asRecord(data.session);
  const turns = asArray(data.turns ?? data.messages ?? data.items)
    .map(normalizeTurn)
    .filter((turn): turn is TurnView => turn !== null);
  const sessionId = firstText(data.session_id, data.sessionId, session.session_id, session.sessionId, session.id, fallbackSessionId);
  const localOnly = booleanFrom(data.localOnly ?? data.local_only ?? meta.localOnly ?? meta.local_only);
  const missingInStore = booleanFrom(data.missingInStore ?? data.missing_in_store ?? meta.missingInStore ?? meta.missing_in_store);
  const degraded = localOnly || missingInStore || booleanFrom(data.degraded ?? meta.degraded);
  return {
    sessionId,
    title: firstText(data.title, session.title, sessionId),
    href: firstText(data.href, session.href, `/bff/management/ai/conversations/${encodeURIComponent(sessionId)}`),
    turnCount: numberFrom(data.turn_count ?? data.turnCount ?? session.turn_count ?? session.turnCount, turns.length),
    turns,
    degraded,
    degradedReason: firstText(
      data.degraded_reason,
      data.degradedReason,
      meta.degraded_reason,
      meta.degradedReason,
      localOnly ? "Conversation exists only in a client-local hint." : "",
      missingInStore ? "Conversation is missing in the durable store." : "",
    ),
  };
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

function errorTitle(message: string, fallback: string): string {
  return /(?:401|403|unauthorized|forbidden)/i.test(message) ? "Authorization required" : fallback;
}

function recentTurns(conversation: ConversationView | null): Array<Record<string, unknown>> {
  if (!conversation) return [];
  return conversation.turns.slice(-6).map((turn) => ({
    role: turn.role,
    content: turn.text,
    createdAt: turn.createdAt,
    providerStatus: turn.providerStatus,
  }));
}

function StatusBadge({ status }: { status: string }) {
  const tone = toneForStatus(status);
  const Icon = tone === "success" ? CheckCircle2 : AlertTriangle;
  return (
    <Badge variant="outline" className={cn("gap-1 capitalize", toneClass(tone))}>
      <Icon className="h-3 w-3" />
      {labelFrom(status)}
    </Badge>
  );
}

function DegradedBanner({
  ask,
  conversation,
}: {
  ask: AskView | null;
  conversation: ConversationView | null;
}) {
  const message = firstText(
    ask?.degraded ? ask.providerMessage : "",
    conversation?.degraded ? conversation.degradedReason : "",
  );
  if (!message) return null;
  return (
    <div
      className="flex items-start gap-2 rounded-md border border-status-warning/30 bg-status-warning/10 p-2 text-xs text-status-warning"
      data-testid="management-ai-degraded-banner"
    >
      <AlertTriangle className="mt-0.5 h-3.5 w-3.5 flex-none" />
      <div>
        <div className="font-medium">Degraded</div>
        <div>{message}</div>
      </div>
    </div>
  );
}

function ReferenceList({ refs }: { refs: LinkRef[] }) {
  if (refs.length === 0) return null;
  return (
    <div className="mt-3 flex flex-wrap gap-2" data-testid="management-ai-reference-list">
      {refs.map((ref) => (
        <Badge key={ref.key} variant="outline" className="max-w-full gap-1 text-[11px]">
          <FileText className="h-3 w-3 flex-none" />
          {ref.href ? (
            <a className="truncate underline-offset-2 hover:underline" href={ref.href}>
              {ref.label}
            </a>
          ) : (
            <span className="truncate">{ref.label}</span>
          )}
        </Badge>
      ))}
    </div>
  );
}

function AskResult({ result }: { result: AskView }) {
  return (
    <article className="rounded-md border border-border bg-background p-3" data-testid="management-ai-ask-result">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold leading-5">Answer</h3>
        <Badge
          variant="outline"
          className={cn("gap-1", toneClass(result.degraded ? "warning" : "success"))}
          data-testid="management-ai-ask-result-state"
        >
          {result.degraded ? <AlertTriangle className="h-3 w-3" /> : <CheckCircle2 className="h-3 w-3" />}
          {result.degraded ? "Degraded" : "Accepted by BFF"}
        </Badge>
        <StatusBadge status={result.status} />
      </div>
      <div className="mt-2 whitespace-pre-wrap break-words text-sm">
        {result.answer || "No answer text returned."}
      </div>
      <dl className="mt-3 grid gap-2 text-xs sm:grid-cols-4">
        <div>
          <dt className="text-muted-foreground">Session</dt>
          <dd className="font-mono font-medium">{result.sessionId || "-"}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Trace</dt>
          <dd className="font-mono font-medium">{result.traceId || "-"}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Confidence</dt>
          <dd className="font-medium capitalize">{labelFrom(result.confidence, "-")}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Actions</dt>
          <dd className="font-medium">{result.actions.length}</dd>
        </div>
      </dl>
      {result.sources.length > 0 ? (
        <div className="mt-3 flex flex-wrap gap-1" data-testid="management-ai-sources">
          {result.sources.map((source) => (
            <Badge key={source} variant="outline" className="text-[11px]">
              {source}
            </Badge>
          ))}
        </div>
      ) : null}
      <ReferenceList refs={result.refs} />
    </article>
  );
}

function TurnRow({ turn }: { turn: TurnView }) {
  const providerStatus = textFrom(turn.providerStatus?.status);
  return (
    <article className="rounded-md border border-border bg-background p-3" data-testid={`management-ai-turn-${turn.id}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="outline" className="capitalize">{labelFrom(turn.role)}</Badge>
          {providerStatus ? <StatusBadge status={providerStatus} /> : null}
        </div>
        <div className="text-xs text-muted-foreground">{formatTime(turn.createdAt)}</div>
      </div>
      <div className="mt-2 whitespace-pre-wrap break-words text-sm">{turn.text}</div>
      {turn.attachments.length > 0 || turn.actions.length > 0 ? (
        <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
          <span>Attachments: {turn.attachments.length}</span>
          <span>Actions: {turn.actions.length}</span>
        </div>
      ) : null}
    </article>
  );
}

function ConversationReadback({
  sessionId,
  state,
  error,
  conversation,
  onSessionIdChange,
  onLoad,
}: {
  sessionId: string;
  state: LoadState;
  error?: string;
  conversation: ConversationView | null;
  onSessionIdChange: (sessionId: string) => void;
  onLoad: () => void;
}) {
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    onLoad();
  }

  return (
    <div className="grid gap-3" data-testid="management-ai-conversation-readback">
      <form className="flex flex-wrap items-center gap-2" data-testid="management-ai-conversation-form" onSubmit={submit}>
        <label className="sr-only" htmlFor="management-ai-session-id">Session id</label>
        <input
          id="management-ai-session-id"
          className="h-8 min-w-0 flex-1 rounded-md border border-border bg-background px-2 font-mono text-xs outline-none focus:ring-2 focus:ring-ring sm:flex-none sm:w-72"
          value={sessionId}
          onChange={(event) => onSessionIdChange(event.target.value)}
          placeholder="session id"
        />
        <button
          type="submit"
          disabled={!sessionId.trim() || state === "loading"}
          className="inline-flex h-8 items-center gap-2 rounded-md border border-border px-3 text-xs font-medium hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
        >
          <RefreshCw className={cn("h-3.5 w-3.5", state === "loading" ? "animate-spin" : "")} />
          Load
        </button>
      </form>

      {state === "idle" && !sessionId.trim() ? (
        <EmptyState
          icon={<MessageSquare className="h-8 w-8" />}
          title="No conversation selected"
          description="Enter a Management AI session id to read server history."
        />
      ) : null}

      {state === "loading" ? (
        <div className="flex items-center gap-2 py-6 text-sm text-muted-foreground" role="status">
          <RefreshCw className="h-4 w-4 animate-spin" />
          Loading conversation
        </div>
      ) : null}

      {state === "error" ? (
        <EmptyState
          icon={<AlertTriangle className="h-8 w-8" />}
          title={errorTitle(error ?? "", "Conversation unavailable")}
          description={error}
          cta={{ label: "Retry", onClick: onLoad }}
        />
      ) : null}

      {state === "ready" && conversation && conversation.turns.length === 0 ? (
        <EmptyState
          icon={<MessageSquare className="h-8 w-8" />}
          title={conversation.degraded ? "Conversation not in durable store" : "No turns returned"}
          description={conversation.degraded ? conversation.degradedReason : "The BFF returned an empty turn list."}
          cta={{ label: "Refresh", onClick: onLoad }}
        />
      ) : null}

      {state === "ready" && conversation && conversation.turns.length > 0 ? (
        <div className="grid gap-3">
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
            <span>Session: {conversation.sessionId}</span>
            <span>Turns: {conversation.turnCount}</span>
            <span>Readback: {conversation.href}</span>
          </div>
          {conversation.turns.map((turn) => <TurnRow key={turn.id} turn={turn} />)}
        </div>
      ) : null}
    </div>
  );
}

export function ManagementAiOpsPanel({
  mode,
  initialSessionId,
  baseUrl,
  className,
}: ManagementAiOpsPanelProps) {
  const [initialRouteMode] = useState<ManagementAiOpsPanelMode>(() => routeModeFromLocation());
  const [initialRouteSessionId] = useState(() => sessionIdFromLocation());
  const inferredMode = mode ?? initialRouteMode;
  const detectedSessionId = initialSessionId ?? initialRouteSessionId;
  const [activeMode, setActiveMode] = useState<ManagementAiOpsPanelMode>(inferredMode);
  const [sessionId, setSessionId] = useState(detectedSessionId);
  const [question, setQuestion] = useState("");
  const [focus, setFocus] = useState("operations");
  const [context, setContext] = useState("");
  const [askState, setAskState] = useState<AskState>("idle");
  const [ask, setAsk] = useState<AskView | null>(null);
  const [askError, setAskError] = useState<string | undefined>();
  const [conversationState, setConversationState] = useState<LoadState>("idle");
  const [conversation, setConversation] = useState<ConversationView | null>(null);
  const [conversationError, setConversationError] = useState<string | undefined>();
  const autoLoadedKeyRef = useRef<string | null>(null);

  useEffect(() => {
    setActiveMode(inferredMode);
  }, [inferredMode]);

  useEffect(() => {
    if (initialSessionId !== undefined) setSessionId(initialSessionId);
  }, [initialSessionId]);

  const loadConversation = useCallback(async (requestedSessionId?: string) => {
    const id = (requestedSessionId ?? sessionId).trim();
    if (!id) {
      setConversation(null);
      setConversationError(undefined);
      setConversationState("idle");
      return;
    }
    setConversationState("loading");
    setConversationError(undefined);
    try {
      const response = await getManagementAssistantConversation(id, baseUrl);
      setConversation(normalizeConversation(response, id));
      setConversationState("ready");
    } catch (err) {
      setConversation(null);
      setConversationError(errorMessage(err, "Management AI conversation unavailable"));
      setConversationState("error");
    }
  }, [baseUrl, sessionId]);

  useEffect(() => {
    const id = detectedSessionId.trim();
    if (activeMode !== "conversations" || !id) return;
    const key = `${activeMode}:${id}`;
    if (autoLoadedKeyRef.current === key) return;
    autoLoadedKeyRef.current = key;
    void loadConversation(id);
  }, [activeMode, detectedSessionId, loadConversation]);

  const headerStatus = useMemo(() => {
    if (askState === "submitting" || conversationState === "loading") return "loading";
    if (askState === "error" || conversationState === "error") return "error";
    if (ask?.degraded || conversation?.degraded) return "degraded";
    if (ask || conversationState === "ready") return "ready";
    return "idle";
  }, [ask, askState, conversation, conversationState]);

  async function submitAsk(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedQuestion = question.trim();
    if (!trimmedQuestion) return;

    setAskState("submitting");
    setAskError(undefined);
    const trimmedSessionId = sessionId.trim();
    const loadedTurns = recentTurns(conversation);
    const ui = buildAssistantUiContext({
      route: { path: "/management/nl/ask", name: "Management NL Ask" },
      visibleSurface: {
        workbench: "management",
        componentId: "ManagementAiOpsPanel",
        heading: "Management AI Ops",
      },
      formRegistry: {
        formId: "management-ai-ops-ask",
        action: {
          kind: "bff_route",
          method: "POST",
          href: "/bff/management/nl/ask",
          idempotencyRequired: true,
          submitAuthority: "bff",
        },
        fields: [
          {
            name: "question",
            label: "Question",
            value: trimmedQuestion,
            valueState: "present",
            dirty: true,
            required: true,
            validatorRefs: [{ type: "required", message: "Question is required." }],
          },
          {
            name: "sessionId",
            label: "Session id",
            value: trimmedSessionId,
            valueState: trimmedSessionId ? "present" : "empty",
            dirty: Boolean(trimmedSessionId),
            validatorRefs: [],
          },
        ],
        dirty: true,
        errors: [],
      },
      contextRefs: ask?.conversationHref
        ? [{ href: ask.conversationHref, label: "Current Management AI conversation" }]
        : [],
    });

    const body: ManagementAssistantAskRequest = {
      question: trimmedQuestion,
      ...(trimmedSessionId ? { sessionId: trimmedSessionId } : {}),
      ...(focus.trim() ? { focus: focus.trim() } : {}),
      ...(context.trim() ? { context: context.trim() } : {}),
      ui,
      ...(loadedTurns.length > 0
        ? {
            conversation: {
              source: "client_hint",
              recentTurns: loadedTurns,
              summary: `${loadedTurns.length} server turn(s) loaded for ${conversation?.sessionId ?? trimmedSessionId}.`,
            },
          }
        : {}),
      metadata: {
        sourcePanel: "ManagementAiOpsPanel",
        panelMode: activeMode,
      },
    };

    try {
      const response = await postManagementAssistantAsk(body, baseUrl);
      const nextAsk = normalizeAsk(response);
      setAsk(nextAsk);
      setAskState(nextAsk.degraded ? "degraded" : "success");
      const nextSessionId = nextAsk.sessionId || trimmedSessionId;
      if (nextSessionId) {
        setSessionId(nextSessionId);
        void loadConversation(nextSessionId);
      }
    } catch (err) {
      setAsk(null);
      setAskError(errorMessage(err, "Management AI ask unavailable"));
      setAskState("error");
    }
  }

  return (
    <section
      className={cn("flex flex-col gap-4", className)}
      data-testid="management-ai-ops-panel"
      data-active-mode={activeMode}
    >
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-border pb-4">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <Bot className="h-4 w-4 text-primary" />
            <h2 className="text-base font-semibold">Management AI Ops</h2>
            <Badge
              variant="outline"
              className={cn("capitalize", toneClass(toneForStatus(headerStatus)))}
              data-testid="management-ai-header-status"
            >
              {labelFrom(headerStatus)}
            </Badge>
          </div>
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
            <span>Ask: /management/nl/ask</span>
            <span>Readback: /management/ai/conversations</span>
            <span>Session: {sessionId || "-"}</span>
            <span>Turns: {conversation?.turns.length ?? 0}</span>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="inline-flex h-8 overflow-hidden rounded-md border border-border">
            <button
              type="button"
              aria-pressed={activeMode === "ask"}
              onClick={() => setActiveMode("ask")}
              className={cn(
                "inline-flex items-center gap-1 px-3 text-xs font-medium hover:bg-muted",
                activeMode === "ask" ? "bg-muted" : "bg-background",
              )}
            >
              <Send className="h-3.5 w-3.5" />
              Ask
            </button>
            <button
              type="button"
              aria-pressed={activeMode === "conversations"}
              onClick={() => setActiveMode("conversations")}
              className={cn(
                "inline-flex items-center gap-1 border-l border-border px-3 text-xs font-medium hover:bg-muted",
                activeMode === "conversations" ? "bg-muted" : "bg-background",
              )}
            >
              <MessageSquare className="h-3.5 w-3.5" />
              Conversations
            </button>
          </div>
          <button
            type="button"
            onClick={() => void loadConversation()}
            disabled={!sessionId.trim() || conversationState === "loading"}
            className="inline-flex h-8 items-center gap-2 rounded-md border border-border px-3 text-xs font-medium hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
          >
            <RefreshCw className={cn("h-3.5 w-3.5", conversationState === "loading" ? "animate-spin" : "")} />
            Refresh
          </button>
        </div>
      </header>

      <DegradedBanner ask={ask} conversation={conversation} />

      {activeMode === "ask" ? (
        <div className="grid gap-3">
          <form
            className="grid gap-3 rounded-md border border-border bg-background p-3"
            data-testid="management-ai-ask-form"
            onSubmit={submitAsk}
          >
            <div className="grid gap-2 lg:grid-cols-[minmax(0,1fr)_12rem_16rem]">
              <div>
                <label className="sr-only" htmlFor="management-ai-question">Question</label>
                <textarea
                  id="management-ai-question"
                  className="min-h-20 w-full resize-y rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-ring"
                  value={question}
                  onChange={(event) => setQuestion(event.target.value)}
                  placeholder="Ask about management state"
                />
              </div>
              <div>
                <label className="sr-only" htmlFor="management-ai-focus">Focus</label>
                <input
                  id="management-ai-focus"
                  className="h-9 w-full rounded-md border border-border bg-background px-2 text-xs outline-none focus:ring-2 focus:ring-ring"
                  value={focus}
                  onChange={(event) => setFocus(event.target.value)}
                  placeholder="focus"
                />
              </div>
              <div>
                <label className="sr-only" htmlFor="management-ai-ask-session-id">Session id</label>
                <input
                  id="management-ai-ask-session-id"
                  className="h-9 w-full rounded-md border border-border bg-background px-2 font-mono text-xs outline-none focus:ring-2 focus:ring-ring"
                  value={sessionId}
                  onChange={(event) => setSessionId(event.target.value)}
                  placeholder="session id"
                />
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <label className="sr-only" htmlFor="management-ai-context">Context</label>
              <input
                id="management-ai-context"
                className="h-9 min-w-0 flex-1 rounded-md border border-border bg-background px-2 text-xs outline-none focus:ring-2 focus:ring-ring"
                value={context}
                onChange={(event) => setContext(event.target.value)}
                placeholder="optional context"
              />
              <button
                type="submit"
                disabled={!question.trim() || askState === "submitting"}
                className="inline-flex h-9 items-center gap-2 rounded-md border border-border px-3 text-xs font-medium hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
              >
                <Send className="h-3.5 w-3.5" />
                {askState === "submitting" ? "Submitting" : "Ask"}
              </button>
            </div>
          </form>

          {askState === "idle" && !ask ? (
            <EmptyState
              icon={<Bot className="h-8 w-8" />}
              title="No ask submitted"
              description="Submit an operator question to create or continue a Management AI session."
            />
          ) : null}

          {askState === "submitting" ? (
            <div className="flex items-center gap-2 py-6 text-sm text-muted-foreground" role="status">
              <RefreshCw className="h-4 w-4 animate-spin" />
              Submitting Management AI ask
            </div>
          ) : null}

          {askState === "error" ? (
            <EmptyState
              icon={<AlertTriangle className="h-8 w-8" />}
              title={errorTitle(askError ?? "", "Management AI ask unavailable")}
              description={askError}
            />
          ) : null}

          {ask && askState !== "submitting" ? <AskResult result={ask} /> : null}

          {sessionId.trim() || conversationState !== "idle" ? (
            <ConversationReadback
              sessionId={sessionId}
              state={conversationState}
              error={conversationError}
              conversation={conversation}
              onSessionIdChange={setSessionId}
              onLoad={() => void loadConversation()}
            />
          ) : null}
        </div>
      ) : (
        <ConversationReadback
          sessionId={sessionId}
          state={conversationState}
          error={conversationError}
          conversation={conversation}
          onSessionIdChange={setSessionId}
          onLoad={() => void loadConversation()}
        />
      )}
    </section>
  );
}
