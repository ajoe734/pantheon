# ASST-INTEG-008 Execution Plans Assistant Follow-up - Frontend Change Spec

## Feature

- Feature ID: `ASST-INTEG-008-execution-plans-assistant-follow-up`
- Screen IDs: `screen-agora-ask-personas`, `surface-management-assistant-panel`
- Workbench: Platform Admin / Agora
- Packet status: follow-up-ready

## Summary

Prepare the `execute-plans` frontend follow-up for assistant-readable UI context,
stale local-session recovery, and actionable ask-stream diagnostics.

This packet does not modify frontend code. It defines the cross-repo brief for the
next `execute-plans` task. The browser remains a hint source only; BFF
conversation readback and BFF context packs remain the source of truth once a
server session exists.

## Current Baseline

The `execute-plans` mirror already has Ask Personas wiring for:

- `POST /bff/agora/ask` through `src/lib/bff/agora.ts`;
- ask SSE through `/bff/events/stream?channel=ask`;
- transcript resync through `GET /bff/agora/ask/sessions/{session_id}`;
- assistant mode/provider badges on `src/agora/pages/AskPersonas.tsx`.

The remaining FE follow-up gaps are:

- ask payloads send prompt/persona ids/metadata but not an assistant-readable
  route/form/table/filter/validator snapshot;
- stale server sessions surface as generic `Error:` or `Resync failed:` text;
- ask SSE failures do not distinguish auth, network, wrong path, or degraded
  server stream causes.

## Files To Modify In `execute-plans`

Suggested ownership for the follow-up task:

```text
src/lib/assistant/uiContextRegistry.ts             - serialize route/form/table/filter hints
src/lib/assistant/staleSession.ts                  - classify 404/local-only readback states
src/lib/assistant/sseDiagnostics.ts                - classify ask stream failures
src/lib/bff/agora.ts                               - accept frontend/context/conversation hints
src/agora/pages/AskPersonas.tsx                    - pass hints and render stale/diagnostic UX
src/management/components/agent/AgentPanelBody.tsx - if present, reuse the same helpers
src/lib/assistant/*.test.ts                        - focused serializer/classifier tests
src/agora/pages/AskPersonas.test.tsx               - stale session and SSE state tests
```

If the frontend repo has renamed these files, keep the current structure and add
the helpers behind the existing BFF client and assistant panel. Do not create a
parallel assistant gateway or a raw provider client.

## UI Context Registry Contract

Send UI context as a hint on ask requests. The preferred request shape is:

```typescript
interface AssistantUiContextV1 {
  version: "assistant_ui_context.v1";
  capturedAt: string;
  route: {
    path: string;
    name?: string;
    params?: Record<string, string>;
    query?: Record<string, string>;
  };
  visibleSurface?: {
    workbench?: string;
    screenId?: string;
    componentId?: string;
    heading?: string;
  };
  selectedEntity?: {
    entityType: string;
    entityId: string;
    label?: string;
    href?: string;
  };
  formRegistry?: {
    formId: string;
    action: {
      kind: "bff_command" | "bff_route" | "frontend_patch_only";
      method?: "POST" | "PUT" | "PATCH";
      href?: string;
      command?: string;
      idempotencyRequired?: boolean;
      submitAuthority: "bff";
    };
    fields: AssistantFormFieldHint[];
    dirty: boolean;
    errors: Array<{ field?: string; message: string; code?: string }>;
  };
  tableContext?: {
    tableId: string;
    filters: Array<{ key: string; operator: string; value: unknown; label?: string }>;
    sort?: Array<{ key: string; direction: "asc" | "desc" }>;
    selectedRows: Array<{ id: string; entityType?: string; label?: string }>;
    visibleColumns?: string[];
  };
  attachments?: Array<{
    attachmentId: string;
    kind: "image" | "csv" | "json" | "text" | "other";
    name: string;
    sizeBytes?: number;
    proxyHref?: string;
  }>;
  visibleErrors: Array<{ message: string; source?: string; code?: string }>;
  contextRefs: Array<{ sourceId?: string; href: string; label?: string }>;
}

interface AssistantFormFieldHint {
  name: string;
  label: string;
  value?: unknown;
  valueState: "present" | "empty" | "redacted" | "unavailable";
  dirty?: boolean;
  disabled?: boolean;
  required?: boolean;
  validatorRefs: Array<{
    type: "required" | "enum" | "min" | "max" | "regex" | "custom";
    message?: string;
    params?: Record<string, unknown>;
  }>;
  optionSet?: Array<{ value: string; label: string; disabled?: boolean }>;
}
```

Attach it to `POST /bff/agora/ask` without making it authoritative:

```json
{
  "prompt": "Why is this execution loop stale?",
  "persona_ids": [],
  "frontend": { "route": "/agora/ask", "selectedEntity": { "...": "..." } },
  "metadata": {
    "source": "ask-personas-ui",
    "assistant_context_version": "assistant_ui_context.v1",
    "assistant_ui_context": { "...": "AssistantUiContextV1" }
  },
  "conversation": {
    "source": "client_hint",
    "recentTurns": [],
    "summary": "Optional UI-side summary; server readback still wins."
  }
}
```

Rules:

- FE context is hint-only and must not grant RBAC, tenant visibility, command
  authority, or provider capability.
- Do not send bearer tokens, cookies, local storage, provider session paths,
  secret values, raw inline file bytes, or hidden form fields.
- For sensitive fields, send `valueState: "redacted"` and omit `value`.
- Prefer `contextRefs` and attachment proxy hrefs over embedding heavy payloads.
- Once BFF returns or resyncs a server transcript, render that readback as the
  conversation truth. Do not merge FE `recentTurns` as if they were durable.

## Stale Local-Session UX

Classify a session as stale when transcript/readback returns either:

- typed `404` / `RESOURCE_NOT_FOUND` for
  `/bff/agora/ask/sessions/{session_id}` or
  `/bff/assistant/sessions/{session_id}/transcript`; or
- a compatibility payload with `localOnly: true` or `missingInStore: true`.

Render a dedicated stale-session state:

- label: `Local session is no longer on the server`;
- body: say that the browser has an old local session id and the server has no
  durable transcript for it;
- primary action: `Start New Server Session`;
- secondary action: keep the prompt/draft text, but clear the stale
  `serverSessionId` before the next ask;
- no empty transcript should be shown as authoritative server history.

Do not silently create a new session on resync failure. The operator should see
that the previous local id is stale, then start a new server-backed session or
send a new ask.

## Ask SSE Diagnostics

Expose ask stream state separately from assistant answer content. The diagnostic
classifier should produce one of:

| Classification | Trigger | UX / behavior |
|---|---|---|
| `auth` | initial BFF probe returns `401`/`403`, or current user session is invalid while EventSource closes | prompt re-auth or reload; stop retry loop |
| `network` | offline, DNS/TLS/CORS failure, timeout before any response, browser reports no status | show network connectivity issue; retry with backoff |
| `path` | stream endpoint returns `404`/`405`, channel is absent, or expected SSE headers are missing on a reachable BFF | show BFF route/channel mismatch; emit frontend feedback / bff-gap evidence |
| `server_stream_degraded` | `5xx`, replay unavailable, stream opens but no heartbeat/event for the configured window, or malformed ask event envelope | fall back to transcript resync; keep answer pane in degraded state |
| `unknown` | browser hides the cause | show generic stream degraded state and offer transcript resync |

Implementation requirements:

- Keep EventSource creation in a shared BFF/assistant helper, not component
  files.
- Use transcript readback after `ask.message.completed` and after reconnect.
- SSE deltas are incremental display hints only; they do not replace durable BFF
  conversation readback.
- Record diagnostic evidence in testable state: endpoint, channel, event id,
  last event timestamp, retry count, and last readback status. Do not include
  auth headers or tokens.
- When the stream is degraded but transcript readback succeeds, show the answer
  from readback and keep a small stream degraded indicator.

## Validation

The follow-up implementation should include:

- serializer tests for route, form field validators, option sets, dirty state,
  table filters, selected rows, visible errors, and redacted sensitive fields;
- ask request tests proving `assistant_ui_context.v1` is sent as hint-only
  metadata and FE `recentTurns` is not treated as durable history;
- stale-session tests for typed 404 and `localOnly/missingInStore` responses;
- SSE diagnostic tests for auth, network, path, server degraded, malformed event,
  completed-event transcript resync, and successful readback fallback;
- a focused UI test that an old local session id shows the stale-session CTA
  instead of an empty transcript.

## Acceptance Checklist

- FE sends assistant-readable route, form, table, filter, selected-row, and
  validator context.
- FE still treats BFF conversation/session readback as source of truth once a
  server session exists.
- BFF 404 or local-only readback is shown as stale local session with a clear
  new-session recovery path.
- SSE failure explains whether the likely source is auth, network, path, server
  stream degraded, or unknown.
- No provider credentials, local provider sessions, secret fields, or raw
  browser storage enter the assistant context.
- The ASST-INTEG-008 Pantheon task itself does not modify `execute-plans` code.

## References

- Integration plan: `docs/04/pantheon_assistant_kernel_user_2026-05-31/EXISTING_ARCHITECTURE_INTEGRATION_PLAN_2026-06-03.md`
- Execution task packet: `docs/04/pantheon_assistant_kernel_user_2026-05-31/EXISTING_ARCHITECTURE_EXECUTION_TASKS_2026-06-03.md`
- Ask Personas mirror: `execute-plans/src/agora/pages/AskPersonas.tsx`
- Agora BFF client mirror: `execute-plans/src/lib/bff/agora.ts`
- Assistant context model: `services/control-plane/bff/assistant/models.py`
- Assistant context composer: `services/control-plane/bff/assistant/context_composer.py`
- SSE substrate rules: `docs/pantheon-handoffs/PKT-005-sse-substrate/FRONTEND_CHANGE_SPEC.md`
