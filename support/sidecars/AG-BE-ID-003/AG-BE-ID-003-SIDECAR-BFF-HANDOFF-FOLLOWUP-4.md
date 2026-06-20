# AG-BE-ID-003 Sidecar BFF and Frontend Handoff Packet - Followup 4

| Field | Value |
|---|---|
| Sidecar task | `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-4` |
| Helper parent | `AG-BE-ID-003` - Interactive/trainer/research session BFF facade |
| Helper kind | `bff_handoff_packet` |
| Parent owner / reviewer | `Codex2` / `Claude` |
| Sidecar owner / reviewer | `Codex` / `Codex2` |
| Date | `2026-06-20` |
| Status | `ready_for_review` |
| Mutates canonical truth | `false` |

Scope constraint: this packet is support material only. It does not change L1
canonical truth, OpenAPI, capability manifests, BFF runtime code, route
registries, governance policy, database migrations, or execute-plans source
files.

## 1. Purpose

Followup 3 identified the parent blocker: the canonical Agora v1.1 servant
session route family is `/bff/agora/servant/sessions`, but
`ServantSessionCreateRequest` cannot express `interactive`, `trainer`, or
`research_task` because it allows only `intent`, `strategy_ref`, and
`metadata`, with `additionalProperties: false`.

This followup turns that blocker into a decision-ready handoff for the parent
owner, reviewer, and downstream frontend:

1. re-check the current parent and upstream servant state
2. isolate the exact contract decision needed before coding
3. identify why legacy `/bff/agora/sessions` remains unsafe as a substitute
4. give execute-plans a conservative frontend gate map
5. list the absorption gates the parent must close before claiming readiness

This packet does not approve, reopen, or implement the parent task.

## 2. Sources Checked

| Source | Why it matters |
|---|---|
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-003` | Parent remains blocked waiting for a reviewer decision on `session_type`. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-002` | Upstream servant ensure/provision/reconcile is archived done and merged. |
| `support/sidecars/AG-BE-ID-003/AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md` | Immediate predecessor packet and current blocker framing. |
| `services/control-plane/openapi/agora_v1_1.openapi.yaml` | Defines servant session route family and incomplete create schema. |
| `services/control-plane/specs/agora/capability_manifest.json` | Still lists legacy session prefixes, not `/bff/agora/servant/sessions`. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/C1_agora_openclaw_skills_master_spec.md` | Common skill envelope includes `session_type`. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/skills/agora/strategy-dialogue/SPEC.md` | Strategy dialogue allows `interactive` and `trainer`, not `research_task`. |
| `scripts/dispatch_agora_cross_repo_2026-06-20.py` | Parent acceptance requires interactive/trainer/research sessions, SSE, audit fields, and `OPENCLAW_UPSTREAM_DEGRADED`. |
| `services/control-plane/bff/agora/router.py` and `services/control-plane/bff/agora/servant/router.py` | Confirms only servant ensure is registered in the package router today. |
| `services/control-plane/bff/main.py` and `services/control-plane/bff/read_store.py` | Legacy session and ask-session behavior still defaults to or filters on `quick_ask`. |
| `services/control-plane/bff/models.py` | Current error enum has `DEPENDENCY_UNAVAILABLE`, not `OPENCLAW_UPSTREAM_DEGRADED`. |

## 3. Current State Re-Check

`AG-BE-ID-002` is done. The parent can compose with the merged servant ensure
surface and use its servant persona/profile metadata as the upstream source for
session routing.

`AG-BE-ID-003` is still blocked before implementation. Its active status says
the task requires `POST /bff/agora/servant/sessions` for
`interactive`, `trainer`, and `research_task`, but no canonical BFF request
contract says where the client supplies the type or how the BFF derives it.

No BFF code implements `/bff/agora/servant/sessions` today. Focused search for
`servant/sessions` under `services/control-plane/bff/agora`,
`services/control-plane/bff/main.py`, and BFF tests returns no match.

## 4. Contract Decision Needed

### C1 - Create request cannot carry the required type

`ServantSessionCreateRequest` currently allows:

```yaml
intent:
  type: string
strategy_ref:
  type: string
metadata:
  type: object
  additionalProperties: true
additionalProperties: false
```

The public request has no `session_type` or `sessionType`. A compliant frontend
cannot send either field.

### C2 - The skill envelope needs a type

The common OpenClaw skill envelope includes:

```json
"session_type": "interactive|trainer|research_task|consult|committee|red_team|background_job"
```

The BFF therefore needs an explicit mapping from its public create request to
the OpenClaw invocation envelope. That mapping is not frozen.

### C3 - `research_task` has no named skill mapping in the checked skill spec

The checked `agora-strategy-dialogue` skill allows only:

```text
interactive, trainer
```

That does not satisfy the parent acceptance for `research_task`. Before
implementation, the parent needs to name the OpenClaw skill/session kind that
owns research task sessions, or narrow the acceptance through a reviewer
decision.

## 5. Decision Matrix For Parent Owner

| Option | What changes | Parent risk |
|---|---|---|
| Add `session_type` to `ServantSessionCreateRequest` | OpenAPI/schema/public BFF contract exposes required enum `interactive`, `trainer`, `research_task`. | Requires canonical contract edit by parent owner, not this sidecar. Lowest frontend ambiguity once approved. |
| Derive type server-side from `intent` or profile context | Public request stays unchanged; BFF chooses a type. | High ambiguity. The dispatch requires three explicit user-visible types, and intent parsing is not a stable contract. |
| Put type only inside `metadata.session_type` | Technically allowed by current schema. | Hidden contract. Frontend could send it, but OpenAPI would not document or validate it as required; not acceptable as strict live-mode proof unless explicitly approved. |
| Add separate route/action variants for each type | Avoids a request field by using route context. | Requires new route contracts not present in v1.1 OpenAPI; likely broader than AG-BE-ID-003 unless approved. |
| Default every create to one type | BFF can implement quickly. | Conflicts with parent acceptance for interactive/trainer/research sessions and should be treated as a blocker, not a shortcut. |

Recommended sidecar position: use an explicit public type contract. The parent
owner should ask the reviewer to approve either `session_type` or a documented
equivalent before implementing route code. Do not accept undeclared fields or
silently derive types from free text.

## 6. Why Legacy Routes Are Not A Substitute

The legacy surfaces remain useful evidence, but they should not be used to
claim AG-BE-ID-003 readiness:

| Legacy surface | Current observation | Readiness impact |
|---|---|---|
| `POST /bff/agora/sessions` | Still defaults to `quick_ask` in `main.py` and `read_store.py` when no `mode` or `sessionType` is supplied. | Produces non-accepted session types and bypasses the servant route family. |
| `POST /bff/agora/ask/sessions` | Hardcodes `mode: quick_ask`. | Covers ask sessions only, not interactive/trainer/research servant sessions. |
| `POST /bff/agora/ask/sessions/{sessionId}/close` | Requires the stored mode to be `quick_ask`. | Does not satisfy servant-session terminate. |
| `GET /bff/sse/agora/sessions/{sessionId}` | Delegates to `stream_ask_events()` and does not scope by `sessionId`. | Not proof of session-scoped servant SSE. |
| `OPENCLAW_UPSTREAM_DEGRADED` | No BFF/OpenAPI/spec match found; current enum includes `DEPENDENCY_UNAVAILABLE`. | Parent must add or explicitly preserve the accepted degradation code. |

## 7. Capability Manifest Alignment Gap

`capability_manifest.json` still maps session capability prefixes to:

```text
/bff/agora/ask/sessions
/bff/agora/sessions
```

It does not list `/bff/agora/servant/sessions`, while OpenAPI v1.1 defines that
route family. If the parent implements servant sessions as canonical, it must
also decide how capability discovery exposes that family to execute-plans.

The parent should not claim frontend live-mode readiness until either:

1. the manifest is updated by the owning task to include the servant session
   route family; or
2. the parent records a precise compatibility rule explaining why frontend
   discovery should use OpenAPI or another source instead of the manifest for
   this route family.

## 8. Frontend Handoff

Until the parent records the contract decision and lands the route family,
execute-plans should keep create/message/stream/terminate controls disabled.

### Safe now

| Frontend action | Surface |
|---|---|
| Resolve operator Agora scope | `GET /bff/agora/me` |
| Show servant provisioning status | `POST /bff/agora/servant/ensure` after user action |
| Read capability hints cautiously | `GET /bff/agora/capabilities`, with the known servant-session prefix gap |

### Still blocked

| Frontend action | Blocker |
|---|---|
| Create interactive session | No public contract field or derivation rule for `interactive`. |
| Create trainer session | Same type blocker; strategy-dialogue allows trainer but BFF create request cannot express it. |
| Create research task session | No named research-task skill/session mapping in checked skill spec. |
| Send servant session message | `/bff/agora/servant/sessions/{session_id}/messages` has OpenAPI shape but no BFF implementation. |
| Terminate servant session | OpenAPI route exists; BFF implementation absent. |
| Stream servant session events | OpenAPI route exists at `/stream`; BFF implementation absent; legacy SSE alias is shared ask channel. |
| Show accepted OpenClaw degraded state | `OPENCLAW_UPSTREAM_DEGRADED` is not in current BFF enum/specs. |

### Recommended client shape after parent decision

If the parent approves an explicit type field, execute-plans can shape its
strict-mode client around:

```ts
type ServantSessionType = "interactive" | "trainer" | "research_task";

createServantSession(input: {
  sessionType: ServantSessionType;
  intent: string;
  strategyRef?: string;
  metadata?: Record<string, unknown>;
}): Promise<ServantSessionEnvelope>;

sendServantSessionMessage(
  sessionId: string,
  content: string,
  attachmentRefs?: string[],
): Promise<ServantMessageEnvelope>;

terminateServantSession(sessionId: string): Promise<ServantSessionEnvelope>;

streamServantSessionEvents(sessionId: string): EventSource;
```

If the parent chooses snake_case on the wire, the frontend should keep the
client API ergonomic but serialize to the canonical BFF field exactly as
approved.

## 9. Operator Journey

### Before parent decision

1. Operator resolves Agora identity via `GET /bff/agora/me`.
2. Operator ensures the private servant via `POST /bff/agora/servant/ensure`.
3. UI shows servant readiness, but session create/message/stream/terminate
   controls remain disabled with a backend-contract-unavailable state.

### After parent implementation

1. Operator resolves Agora identity and servant profile.
2. Operator creates a servant session with the parent-approved representation
   of `interactive`, `trainer`, or `research_task`.
3. BFF records `trace_id`, `request_id`, `actor_id`, `user_id`, `persona_id`,
   and `session_id` in audit trail and response meta.
4. BFF maps the session to the correct OpenClaw session/skill kind.
5. Operator sends messages; responses arrive through the session-scoped SSE
   route.
6. OpenClaw degradation returns a 503 preserving
   `OPENCLAW_UPSTREAM_DEGRADED`.
7. Operator terminates the session; BFF persists terminal state and emits a
   terminal session event.

## 10. Parent Absorption Gates

| Gate | Required parent decision or implementation |
|---|---|
| P0 upstream servant | Compose with merged `POST /bff/agora/servant/ensure` from AG-BE-ID-002. |
| P1 type contract | Record how the create request carries or derives `interactive`, `trainer`, and `research_task`. |
| P2 research mapping | Name the OpenClaw skill/session kind for `research_task`. |
| P3 OpenAPI/schema alignment | Update or explicitly approve the public create contract; do not accept undeclared fields. |
| P4 BFF package placement | Implement under the Agora servant/session package without overwriting servant ensure. |
| P5 capability discovery | Align `capability_manifest.json` or record an explicit discovery exception. |
| P6 audit fields | Include `trace_id`, `request_id`, `actor_id`, `user_id`, `persona_id`, and `session_id` for reads/writes and response meta. |
| P7 degradation code | Preserve `OPENCLAW_UPSTREAM_DEGRADED` as the accepted session degradation code. |
| P8 SSE scope | Implement servant session stream scoped by `session_id`; do not reuse shared ask-channel stream as proof. |
| P9 tests | Cover create for all approved types, invalid/missing type handling, message post, terminate, stream, audit meta, idempotency, capability discovery, and degradation. |

## 11. Verification Run

Commands run for this packet:

```bash
git status -sb
git branch --show-current
git remote -v
./scripts/git/task_start.sh "AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-4"
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-003
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-002
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-4
rg -n "ServantSessionCreateRequest|/bff/agora/servant/sessions|createServantSession|session_type|sessionType|interactive|trainer|research_task|OPENCLAW_UPSTREAM_DEGRADED" services/control-plane/openapi/agora_v1_1.openapi.yaml services/control-plane/specs/agora/capability_manifest.json services/control-plane/specs/agora/strategy_workshop.schema.json
rg -n "session_type|interactive|trainer|research_task|servant/sessions|ServantSessionCreateRequest|OPENCLAW_UPSTREAM_DEGRADED" docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure docs/04/pantheon_agora_cross_repo_2026-06-20/SD_2026-06-20.md scripts/dispatch_agora_cross_repo_2026-06-20.py
rg -n "servant/sessions" services/control-plane/bff/agora services/control-plane/bff/main.py services/control-plane/bff/tests || true
rg -n "OPENCLAW_UPSTREAM_DEGRADED" services/control-plane/bff services/control-plane/openapi services/control-plane/specs/agora || true
rg -n "@.*bff/agora/sessions|@.*bff/agora/ask/sessions|@.*bff/sse/agora/sessions" services/control-plane/bff/main.py
rg -n "create_agora_session|def create_agora_session|mode.*quick_ask" services/control-plane/bff/read_store.py services/control-plane/bff/main.py
```

Results:

- Task branch was reset to current `origin/dev` before edits.
- Followup 4 is active, owner `Codex`, reviewer `Codex2`.
- Parent `AG-BE-ID-003` remains blocked waiting for a reviewer decision.
- Upstream `AG-BE-ID-002` is done and merged.
- OpenAPI v1.1 defines servant session routes but no type field in
  `ServantSessionCreateRequest`.
- BFF has no `servant/sessions` implementation or tests today.
- BFF/OpenAPI/specs do not define `OPENCLAW_UPSTREAM_DEGRADED` today.
- Legacy session routes still live in `main.py`; legacy create defaults to
  `quick_ask`, and the legacy session SSE route aliases the shared ask stream.

## 12. Support-Only Boundary Confirmation

- No L1 canonical policy or architecture document was edited.
- No OpenAPI, capability manifest, BFF runtime, router, registry, governance,
  migration, schema, or frontend implementation was changed.
- The intended support packet artifact is this file:
  `support/sidecars/AG-BE-ID-003/AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-4.md`.

## 13. Reviewer Handoff

Reviewer: `Codex2`

Please review this support packet for:

1. support-only scope compliance
2. accuracy of the current parent blocker and AG-BE-ID-002 dependency state
3. correctness of the contract decision matrix
4. usefulness of the frontend gates for execute-plans
5. whether the parent absorption gates are specific enough for Codex2/Claude to
   make the AG-BE-ID-003 implementation decision

Suggested approval command:

```bash
AI_NAME=Codex2 ./scripts/ai-status.sh approve AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-4 "Followup 4 support packet approved; contract decision matrix, frontend gates, and parent absorption gates are accurate and support-only."
```
