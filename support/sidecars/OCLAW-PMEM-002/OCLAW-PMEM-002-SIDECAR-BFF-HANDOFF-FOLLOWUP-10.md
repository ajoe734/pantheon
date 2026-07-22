# OCLAW-PMEM-002 BFF / Frontend Handoff Follow-up 10

Status: support-only implementation handoff; not canonical truth or runtime proof
Parent task: `OCLAW-PMEM-002`
Sidecar task: `OCLAW-PMEM-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-10`
Owner: Codex2
Reviewer: Codex

## Current Verdict

The BFF-first dependency identified in follow-up 9 remains open. This packet
turns that contract freeze into a narrow implementation and review receipt for
the parent-selected BFF owner. It changes no reconciler, BFF, frontend,
registry, Memory Plane, provider policy, governance, or canonical contract.

The audited baseline is `origin/dev` at `d8a479fe4`. The only relevant changes
after the follow-up 9 audit were the follow-up 9 support commits and merges.
The parent branch `origin/task/OCLAW-PMEM-002` remains at `db8e7ca0f`.

On this baseline:

- `_openclaw_agent_reconcile_request` writes desired request metadata with a
  `pending` or `blocked` status into the persona record;
- `GET /bff/personas/{persona_id}/runtime-profile` recomputes desired runtime
  inputs; and
- no persona-scoped BFF read route projects an authoritative reconcile attempt,
  observed agent state, current-generation probe, and terminal result.

Persona mutation success, desired metadata, runtime-profile success, agent
existence, and aggregate sync output therefore remain insufficient to enable an
OpenClaw conversation.

## BFF Implementation Handoff

**Repository:** `ajoe734/pantheon`
**Merge target:** `dev`
**Owner selection:** parent `OCLAW-PMEM-002` owner

Before coding, the BFF owner must name the authoritative durable attempt/result
write owner. The implementation must then expose a governed persona-scoped
projection whose adopted route and schema version are recorded in the receipt.
This packet intentionally does not invent those canonical names.

The projection must include independently sourced:

1. request identity: attempt id, persona id, requested generation and time;
2. consumer acknowledgement and lifecycle;
3. desired identity, workspace, model and generation;
4. observed identity, workspace, model, generation and observation time;
5. sanitized current-generation probe result, time and evidence reference;
6. stable outcome reason, retryability, safe repair action and completion time;
7. explicitly labelled prior-success history; and
8. server snapshot time plus contract/schema version.

The server must publish a versioned lifecycle equivalent to `queued`,
`reconciling`, `ready`, `drifted`, `blocked`, `failed`, and `unavailable`.
`ready` requires desired generation equal to observed generation plus a
successful sanitized probe for that generation. Unknown or malformed lifecycle
values fail closed as unavailable. A previous generation must never supply
missing current observed or probe evidence.

### Required BFF Tests

- duplicate request/delivery preserves one authoritative attempt outcome;
- acknowledgement cannot regress a terminal result;
- generation N late completion cannot replace generation N+1 truth;
- current-generation desired/observed mismatch is `drifted`, not `ready`;
- missing or failed current-generation probe cannot become `ready`;
- previous success remains history during a newer non-ready attempt;
- result-owner outage and malformed/unknown lifecycle return unavailable;
- 404 and authorization behavior do not disclose persona existence improperly;
- payload redaction excludes credentials, tokens, provider payloads, raw
  stdout/stderr, command lines, and exception text.

## Conditional Frontend Handoff

**Repository:** `ajoe734/execute-plans`
**Merge target:** `main`
**Start gate:** the Pantheon BFF projection is merged and a versioned contract
or sanitized representative response is attached to the composition receipt.

The frontend must consume only the governed BFF projection. It must enable
conversation only for current-generation `ready`, order responses by generation
then server observation/snapshot time, discard stale regressions, label previous
success as history, fail closed on unavailable or unknown data, and show Retry
only when the server says `retryable=true`. Repair navigation may use only
server-owned safe actions; it must not directly mutate OpenClaw, provider,
registry, memory, or governance configuration.

Frontend state-machine tests must cover every lifecycle, unknown/malformed
payloads, late response ordering, a failed new generation with prior-success
history, Retry gating, and accessible non-ready messaging.

## Composition Receipt Template

The parent owner should complete this table before claiming adoption:

| Receipt field | Required value |
|---|---|
| Durable attempt/result owner | Named component and storage boundary |
| Adopted BFF route | Exact method and path |
| Contract version | Schema and lifecycle enum version |
| Idempotency and ordering | Key rule and generation conflict rule |
| Pantheon delivery | PR, merge SHA, focused test command/result |
| Sanitized projection | Representative current-generation response |
| Frontend delivery | `execute-plans` PR, merge SHA, state-machine test result |
| Hosted ancestry | Deployed BFF and frontend SHAs descended from merges |
| Hosted proof | Projection plus `model=openclaw/{persona_id}` response tied to the same generation |
| Residual risks | Provider, canonical-memory, and materialization gaps stated separately |

Provider readiness, canonical memory health, and workspace materialization
health must remain separate truth rows. None can promote agent reconcile state.

## Reviewer Checklist

- [x] Diff is limited to this support artifact and task-scoped coordination records.
- [x] The audit baseline and unresolved BFF query gap are accurate.
- [x] No proposed route, storage owner, or lifecycle spelling is presented as canonical.
- [x] Desired, observed, probe, provider, memory, and materialization truth remain separate.
- [x] All missing, stale, malformed, unavailable, and unknown states fail closed.
- [x] Cross-repository adoption requires merged identities and hosted same-generation proof.

Reviewer `Codex` should approve this only as a support handoff. Approval does
not implement the parent BFF or frontend slice and does not prove hosted
OpenClaw, provider, or memory readiness.

## Closeout Receipt

Reviewer `Codex` approved this packet as a support-only BFF/frontend handoff.
The approval preserves the non-canonical boundary: the parent owner must still
adopt the durable result owner, route/schema, implementation PRs, and hosted
same-generation proof before making readiness claims.

Original delivery: PR `#3267`, task commit
`26c6a28e2170ec13faa1610a08a8471cc282a81d`, merge commit
`2aaca8d48bd28d2c360cbf846f9338e3ca2edf95`.

Finalization verification:

- `git diff --check`
- task-scope path audit with `git status --short`
- confirmation that task commit `26c6a28e2170ec13faa1610a08a8471cc282a81d`
  is an ancestor of `origin/dev`
