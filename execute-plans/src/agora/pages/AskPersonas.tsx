import React, { useEffect, useRef, useState } from "react";
import { postAsk, openAskSse, getAskSession } from "@/lib/bff/agora";
import AssistantModeBadge, {
  isKernelMode,
  type AssistantMode,
  type AssistantModeSignals,
  type AssistantProviderSignal,
} from "@/platform/components/AssistantModeBadge";

type JsonRecord = Record<string, unknown>;

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
    runtime: firstString(...records.map((record) => record.runtime), ...records.map((record) => record.provider_runtime)),
    checkedAt: firstString(...records.map((record) => record.checked_at), ...records.map((record) => record.checkedAt)),
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
  const provider = normalizeProvider(
    data.provider,
    data.provider_status,
    data.providerStatus,
    assistantMeta.provider,
    assistantMeta.provider_status,
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
    ),
    ttlSeconds: firstNumber(
      data.ttl_seconds,
      data.ttlSeconds,
      session.ttl_seconds,
      session.ttlSeconds,
      assistantMeta.ttl_seconds,
      assistantMeta.ttlSeconds,
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
    ),
    commandRef: firstString(commandState.command_id, commandState.commandId, commandMeta.commandId, commandMeta.command_id),
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
  const data = recordFrom(root.data ?? root);
  const session = recordFrom(data.session ?? root.session);
  const raw = arrayFrom(data.transcript ?? data.messages ?? session.transcript ?? session.messages);
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

export default function AskPersonas(): JSX.Element {
  const [prompt, setPrompt] = useState("");
  const [messages, setMessages] = useState<Array<{ role: string; content: string }>>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [mode, setMode] = useState<AssistantMode | null>(null);
  const [assistantSignals, setAssistantSignals] = useState<AssistantModeSignals>({});
  const [sourceCitations, setSourceCitations] = useState<SourceCitation[]>([]);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    return () => {
      if (esRef.current) {
        esRef.current.close();
      }
    };
  }, []);

  const appendDelta = (delta: string, role = "assistant") => {
    setMessages((m) => [...m, { role, content: delta }]);
  };

  const handleSend = async () => {
    if (!prompt.trim()) return;
    setMessages([{ role: "operator", content: prompt }]);
    setStatus("sending");
    setMode(null);
    setAssistantSignals({});
    setSourceCitations([]);

    try {
      const body = {
        prompt,
        persona_ids: [],
        metadata: { source: "ask-personas-ui" },
      };
      const res = await postAsk(body);
      // res may be assistant envelope or Agora compatibility envelope.
      const data = res?.data ?? res;
      const sessionRecord = recordFrom(data?.session);
      const id = firstString(data?.session_id, res?.session_id, sessionRecord.sessionId, sessionRecord.session_id, sessionRecord.id);
      if (!id) throw new Error("no session id returned");
      setSessionId(id);
      const sessionSignals = normalizeAssistantSignals(res, id);
      const sessionMode = sessionSignals.mode ?? null;
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
                setMode(updatedSignals.mode ?? sessionMode);
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

  const handleResync = async () => {
    if (!sessionId) return;
    try {
      const s = await getAskSession(sessionId);
      const final = transcriptMessages(s);
      setMessages(final);
      const record = recordFrom(s);
      setStatus(firstString(record.status) ?? null);
      const updatedSignals = normalizeAssistantSignals(s, sessionId);
      setMode(updatedSignals.mode ?? null);
      setAssistantSignals(updatedSignals);
      const citations = normalizeSourceCitations(s);
      if (citations.length > 0) {
        setSourceCitations(citations);
      }
    } catch (e) {
      appendDelta(`Resync failed: ${String(e)}`, "assistant");
    }
  };

  return (
    <div>
      <h2>Ask Personas</h2>
      <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={6} cols={80} />
      <div>
        <button onClick={handleSend} disabled={status === "sending" || !prompt.trim()}>
          Ask
        </button>
        <button onClick={handleResync} disabled={!sessionId}>
          Resync Transcript
        </button>
      </div>
      <div>
        <strong>Status:</strong> {status ?? "idle"} {sessionId ? `(session ${sessionId})` : null}
      </div>
      {!isUserMode(mode) && isKernelMode(mode) && (
        <AssistantModeBadge signals={{ ...assistantSignals, mode, sessionId }} />
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
