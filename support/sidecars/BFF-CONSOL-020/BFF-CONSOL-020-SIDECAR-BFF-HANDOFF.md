# BFF-CONSOL-020 Sidecar: BFF and Frontend Handoff Packet

Task ID: BFF-CONSOL-020-SIDECAR-BFF-HANDOFF
Parent Task: BFF-CONSOL-020 - runAction.ts migration to /bff/v1/commands
Helper Kind: bff_handoff_packet
Prepared by: Codex
Reviewer: Codex2
Date: 2026-05-13
Mutates canonical truth: false

## Purpose

This packet gives the BFF-CONSOL-020 owner a support-only handoff for migrating the
execute-plans write seam from legacy action dispatch to the final BFF command
surface. It does not change L1 canonical truth, core contracts, runtime code,
registry code, or governance implementation.

BFF-CONSOL-020 should make new frontend callers submit governed commands to
`POST /bff/v1/commands` while keeping the legacy `POST /bff/actions/*` caller
working through the BFF-CONSOL-019 backend adapter. Both paths must return a
frontend-normalized `CommandResponse` shape.

## Current State Observed

| Area | Observation | Impact for BFF-CONSOL-020 |
|---|---|---|
| Backend final route | `POST /bff/v1/commands` exists and uses `_submit_final_command_admission`. | New `commandClient.ts` can target the final command route directly. |
| Backend legacy adapter | BFF-CONSOL-019 finalized `/bff/actions/{entityType}/{entityId}/{actionId}` as an admission bridge to the final command path. | Existing `runAction` callers can remain compatible during migration. |
| Command status route | Command readback remains `GET /api/v1/operator/commands/{command_id}` per `BFF_COMMAND_API_CONTRACT.md`. | Frontend polling should not invent `/bff/v1/commands/{id}` unless a backend route is added separately. |
| Frontend tracked file | In this checkout, `execute-plans/src/lib/bff/runAction.ts` currently contains only the cookie-session write gate helpers. | Parent task needs to add or restore the actual write seam plus tests in the execute-plans tree it owns. |
| `commandClient.ts` | No tracked `execute-plans/src/lib/bff/commandClient.ts` exists in this checkout. | Parent task should create it as the new final-command caller module. |
| Real writes gate | Prior frontend task notes require writes to stay gated by `VITE_BFF_REAL_WRITES=true` and authenticated session. | Migration must not bypass the env + auth write gate. |

## BFF Query and Command Gap

### Closed by BFF-CONSOL-019

- Legacy `POST /bff/actions/{entityType}/{entityId}/{actionId}` is no longer a
  standalone mutation path. It builds a final command payload, derives actor
  from auth, requires `Idempotency-Key`, propagates trace/correlation/request
  headers, records policy/audit foundation evidence, and stores a typed target.
- Adapter responses are final `CommandResponse`-style envelopes and include
  durable idempotency metadata when routed through the adapter path.
- Focused backend evidence exists in
  `services/control-plane/bff/tests/test_actions_to_commands_adapter.py`.

### Still owned by BFF-CONSOL-020

| Gap | Required handoff action |
|---|---|
| New caller path | Add `execute-plans/src/lib/bff/commandClient.ts` that submits direct `POST /bff/v1/commands` requests. |
| Legacy caller compatibility | Keep `runAction.ts` able to emit `POST /bff/actions/{entityType}/{entityId}/{actionId}` for existing call sites until BFF-CONSOL-024 deprecates the old receipt path. |
| Response normalization | Normalize both direct command and legacy action-adapter receipts into the same frontend `CommandResponse` DTO. |
| Header semantics | Use canonical `Idempotency-Key`; pass `X-Trace-Id`, `X-Correlation-Id`, `X-Request-Id`, and `X-MFA-Token` when available. Do not put `idempotencyKey` in the request body. |
| Confirm-token propagation | High-risk modal issued tokens must reach command admission through `X-Confirm-Token` and/or `params.confirmToken` according to the existing frontend seam; tests must assert the token leaves the modal path. |
| Approval evidence | Approval/two-man evidence must stay in explicit command params/evidence fields expected by the backend validators; missing evidence should surface typed non-2xx BFF errors, not success statuses. |
| Typed error mapping | Preserve final error codes: `CONFIRM_TOKEN_REQUIRED`, `APPROVAL_REQUIRED`, `TWO_MAN_REQUIRED`, `IDEMPOTENCY_CONFLICT`, `INSUFFICIENT_ROLE`, `INVALID_PARAMS`, and `INVALID_REQUEST`. |
| Readback | Use `data.command_id` / `data.commandId` / `data.receipt_id` from `CommandResponse.data` for tracking, then poll `GET /api/v1/operator/commands/{command_id}` if the UI needs status. |

## Recommended Frontend Contract

### `commandClient.ts`

Create a narrow client owned by the BFF write seam:

```typescript
export interface CommandEnvelopeInput {
  command: string;
  target: { type: string; id: string };
  action?: string;
  params?: Record<string, unknown>;
  audit_context: { reason: string; incident_id?: string | null };
}

export interface CommandClientOptions {
  idempotencyKey: string;
  traceId?: string;
  correlationId?: string;
  requestId?: string;
  mfaToken?: string;
  confirmToken?: string;
}

export async function submitCommand(
  input: CommandEnvelopeInput,
  opts: CommandClientOptions,
): Promise<CommandResponse>;
```

Implementation expectations:

- POST to `${baseUrl}/bff/v1/commands`.
- Always set `Authorization` through the existing auth/session transport.
- Always set `Idempotency-Key`; use `X-Idempotency-Key` only for compatibility
  tests, not as the default.
- Forward trace headers when supplied; mint stable client-side values only if
  the existing transport already does that.
- Set `X-Confirm-Token` when `opts.confirmToken` is present.
- Reject or surface a typed error before fetch if `VITE_BFF_REAL_WRITES` is not
  enabled or the authenticated write gate fails.

### `runAction.ts`

Keep `runAction.ts` as the compatibility facade:

1. `runAction` legacy mode still maps kind/entity/action to
   `/bff/actions/{entityType}/{entityId}/{actionId}`.
2. New final-command mode should call `submitCommand()` with the command mapping
   from `BFF_COMMAND_API_CONTRACT.md` section 8.
3. `runActionSafe` should receive one normalized DTO regardless of caller path.
4. Confirm-token and approval/two-man evidence must not be embedded only in memo
   text; they need explicit fields/headers that reach BFF admission.
5. `VITE_BFF_REAL_WRITES=false` must stop both direct and legacy writes before
   fetch.

## Operator Journey

### Direct final-command caller

```text
Operator opens a detail page
  -> UI confirms live writes are enabled and session is authenticated
  -> Operator triggers an action
  -> UI builds a stable Idempotency-Key and trace/correlation ids
  -> High-risk action, if any, obtains confirmToken from the modal
  -> commandClient.submitCommand POSTs /bff/v1/commands
  -> BFF derives actor_ref from auth, validates RBAC/policy/preconditions
  -> BFF persists command, idempotency, policy, audit, and typed target evidence
  -> UI receives CommandResponse.status=accepted and data.receipt_id/command_id
  -> UI shows receipt and optionally polls /api/v1/operator/commands/{command_id}
```

### Legacy compatibility caller

```text
Existing caller invokes runAction(kind, entityId, action)
  -> runAction emits POST /bff/actions/{entityType}/{entityId}/{actionId}
  -> BFF-CONSOL-019 adapter maps the action to final command admission
  -> BFF returns the same frontend-normalized CommandResponse shape
  -> UI behavior stays unchanged while the route is still supported
```

### Error and precondition journey

```text
Missing confirm token / approval / two-man evidence
  -> BFF returns non-2xx BffErrorEnvelope
  -> Frontend maps the typed error code to the existing modal or blocked state
  -> UI must not treat requires_confirm_token / requires_approval as success
  -> Retrying uses the same Idempotency-Key only for the same command payload
```

## Command Mapping Notes

Use `BFF_COMMAND_API_CONTRACT.md` section 8 as the parent task source for the
exact mapping table. The frontend side should preserve these common fields:

| Legacy action input | Final command envelope field |
|---|---|
| `kind` / entity family | `target.type` and command class (`StrategyAction`, `DeploymentAction`, etc.) |
| `entityId` | `target.id` and domain-specific params id |
| `action` | `action` plus `params.action_id` |
| user-facing reason/memo | `audit_context.reason` and a non-canonical UI note param if needed |
| confirm token | `X-Confirm-Token` and/or explicit `params.confirmToken` |
| approval/two-man evidence | explicit command params/evidence fields expected by validators |
| generated retry key | `Idempotency-Key` header only |
| trace/correlation/request ids | `X-Trace-Id`, `X-Correlation-Id`, `X-Request-Id` |

## Suggested Test Matrix

Parent task should add focused frontend tests near the execute-plans write seam:

| Test | Expected result |
|---|---|
| `commandClient` posts direct final command | URL is `/bff/v1/commands`; body has command/target/action/params/audit_context; headers include `Idempotency-Key`. |
| Body idempotency is not sent | No `idempotencyKey` key appears in the JSON body. |
| Direct response normalization | Backend `{status,data,meta}` becomes the same frontend `CommandResponse` DTO used by `runActionSafe`. |
| Legacy action path still works | Existing caller posts `/bff/actions/*` and normalizes to the same DTO. |
| Confirm token reaches admission | `opts.confirmToken` appears in `X-Confirm-Token` and/or explicit params; it is not memo-only. |
| Missing confirm token | Mocked backend `CONFIRM_TOKEN_REQUIRED` becomes the existing high-risk modal state. |
| Missing approval evidence | Mocked backend `APPROVAL_REQUIRED` becomes a typed blocked state, not a success receipt. |
| Idempotency conflict | Mocked 409 `IDEMPOTENCY_CONFLICT` is surfaced with retry guidance. |
| Write gate off | With `VITE_BFF_REAL_WRITES=false`, neither `/bff/v1/commands` nor `/bff/actions/*` is fetched. |
| Auth gate off | Without authenticated session, no live write fetch occurs. |

Suggested local verification for the parent owner:

```bash
npm run test -- src/lib/bff/__tests__/runAction.test.ts src/lib/bff/__tests__/commandClient.test.ts src/lib/bff-v1/__tests__/writes.test.ts
npm run build
```

Backend reference verification already available from BFF-CONSOL-019:

```bash
python3 -m py_compile services/control-plane/bff/tests/test_actions_to_commands_adapter.py
python3 -m pytest services/control-plane/bff/tests/test_actions_to_commands_adapter.py -v
```

## Parent Absorption Risks and Gates

- EP5 paper-canary merge gate still applies to the Wave 3 command adapter chain:
  do not merge runtime-affecting command path changes to `main` until EP5
  closeout is confirmed.
- BFF-CONSOL-020 should not remove `/bff/actions/*`; BFF-CONSOL-024 owns old
  action receipt deprecation after the BFF-CONSOL-021 dual-write soak.
- Direct `/bff/v1/commands` must not enable live-capital side effects. BFF
  receipts currently expose `liveCapitalSideEffects=false`; frontend copy should
  not imply capital binding is live.
- New frontend code must not create a second command schema. Treat
  `BFF_COMMAND_API_CONTRACT.md` section 8 as the mapping source and the backend
  `CommandResponse` model as the success envelope.
- If the parent owner is working in a sibling execute-plans checkout, reconcile
  this packet against that tree before applying file names/tests; this support
  artifact only reflects the tracked state visible from the pantheon repo.

## Handoff Checklist for Codex2

- Create or update `execute-plans/src/lib/bff/commandClient.ts`.
- Update `execute-plans/src/lib/bff/runAction.ts` without bypassing
  `liveWriteGated()`.
- Preserve old `/bff/actions/*` caller compatibility.
- Normalize direct and legacy receipts to one frontend shape.
- Cover confirm-token propagation from high-risk modal to admission.
- Cover approval/two-man missing-evidence typed errors.
- Add idempotency and write-gate tests.
- Keep canonical truth and backend runtime out of this sidecar scope.

## Verification for This Sidecar

Performed as read-only context checks plus artifact creation:

- Read task-scoped context: `AI_COLLABORATION_GUIDE.md`,
  `.orchestrator/task-briefs/bff_consol_020_sidecar_bff_handoff.md`,
  `.orchestrator/skills/task-closeout-finalization.md`, and `ai-status.json`.
- Reconciled parent task fields in `ai-status.json` during review: owner
  `Codex2`, reviewer `Claude2`, current status `review`, artifacts
  `runAction.ts` and `commandClient.ts`. The packet was prepared while the
  parent implementation was moving from implementation to review.
- Confirmed the sidecar artifact path did not exist before this packet.
- Inspected `BFF_COMMAND_API_CONTRACT.md`, BFF-CONSOL-019 backend adapter tests,
  and the tracked execute-plans BFF write-seam file.

No canonical truth, core contract truth, runtime implementation, registry code,
or governance implementation was modified by this sidecar.

## Owner Closeout Verification

Performed during `review_approved` finalization:

- `jq '.tasks[] | select(.id=="BFF-CONSOL-020-SIDECAR-BFF-HANDOFF")' ai-status.json`
  confirmed owner `Codex`, reviewer `Codex2`, and status `review_approved`.
- `sed -n '1,260p' support/sidecars/BFF-CONSOL-020/BFF-CONSOL-020-SIDECAR-BFF-HANDOFF.md`
  re-read the approved support artifact.
- `LC_ALL=C grep -nP '[^\x00-\x7F]' support/sidecars/BFF-CONSOL-020/BFF-CONSOL-020-SIDECAR-BFF-HANDOFF.md`
  returned no matches.
- `git diff --no-index --check /dev/null support/sidecars/BFF-CONSOL-020/BFF-CONSOL-020-SIDECAR-BFF-HANDOFF.md`
  returned no whitespace-error output.
- `git status --short` showed unrelated dirty files outside this task; this
  closeout stages only the BFF-CONSOL-020 sidecar artifact.
