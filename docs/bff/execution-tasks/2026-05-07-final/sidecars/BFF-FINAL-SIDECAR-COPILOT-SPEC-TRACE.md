# BFF-FINAL-SIDECAR-COPILOT-SPEC-TRACE

Owner: Copilot
Reviewer: Codex
Depends on: BFF-FINAL-001
Parent: BFF-FINAL-010
Mutates canonical code: no

Last updated: 2026-05-08T01:10:00Z

## Scope

Trace the final BFF contract requirements to the BFF-FINAL execution tasks. This is support-only spec review work. Do not edit `services/control-plane/bff/*` or canonical contract files.

## Deliverable

Update this file with:

1. Contract requirements grouped by response envelope, command admission, preconditions, action catalog, SSE, MCP import, evidence redaction, Agora patching, and interventions.
2. The BFF-FINAL task that owns each requirement.
3. Any ambiguity, duplicate ownership, or missing acceptance point.
4. A short checklist for `BFF-FINAL-010`.

## Acceptance

- Trace is useful for final review and handoff.
- Ambiguities are explicit instead of silently resolved.
- No canonical code edits are made.

## Contract requirements (grouped)

Below each group lists the concrete requirements we expect the BFF to satisfy, and the owning BFF-FINAL task where applicable.

1) Response envelope and surface contract

- Requirement: All successful command responses must use CommandResponse<T> envelope with consistent metadata fields (requestId, timestamp, status, data).
  - Acceptance pointer: schema docs, contract tests asserting shape and headers.
  - Owning task: BFF-FINAL-002 (Command admission & response shaping)

- Requirement: Errors use BffErrorEnvelope with machine-parsable code, message, and optional remediation hints; non-2xx semantics must be explicit.
  - Acceptance pointer: error schema and BFF error translation tests.
  - Owning task: BFF-FINAL-003 (Precondition & error semantics)

2) Command admission, idempotency, and authentication

- Requirement: Inbound commands must pass admission guards: Idempotency-Key header or body idempotencyKey, authentication token validation, and rate/guard checks.
  - Acceptance pointer: idempotency tests, header enforcement tests, and adapter-level admission unit tests.
  - Owning task: BFF-FINAL-002

- Requirement: Idempotent command processing must be deterministic: duplicate requests with same idempotency key return the prior CommandResponse instead of re-executing actions.
  - Acceptance pointer: idempotency replay tests and persisted command store behavior.
  - Owning task: BFF-FINAL-002

3) Preconditions, enforcement and side-effects

- Requirement: Preconditions (resource locks, approval state, quota) must cause non-side-effecting precondition failures using 4xx (428/409) and must not emit downstream commands on failure.
  - Acceptance pointer: precondition tests showing no downstream calls and correct 4xx codes.
  - Owning task: BFF-FINAL-003

- Requirement: Approval-required commands must be rejected with clear remediation steps when missing approvals.
  - Acceptance pointer: approval denial tests and SSE prompting artifacts.
  - Owning task: BFF-FINAL-003

4) Backend action catalog and descriptor mapping

- Requirement: The canonical action catalog (descriptor ↔ action) must be authoritative; descriptor metadata must include governance annotations and UI mapping stable keys.
  - Acceptance pointer: catalog artifact, catalog endpoint tests, integration tests with frontend mapping.
  - Owning task: BFF-FINAL-004

- Requirement: Action descriptors must be versioned and backward-compatible; catalog changes require catalog migration notes.
  - Acceptance pointer: catalog migration artifact and governance metadata.
  - Owning task: BFF-FINAL-004

5) Server-Sent Events (SSE), approval channels, and replay semantics

- Requirement: SSE channels expose approval/ask channels, include replay metadata (original request id, sequence), and degrade gracefully when channel absent.
  - Acceptance pointer: SSE integration tests, channel presence checks, replay metadata tests.
  - Owning task: BFF-FINAL-005

- Requirement: Replay semantics must preserve original timestamps and correlation ids to enable audit and remediation flows.
  - Acceptance pointer: replay tests and audit logs.
  - Owning task: BFF-FINAL-005

6) MCP import and tool-action import semantics

- Requirement: MCP import endpoints must securely import tool/action descriptors and not permit creation of actions outside controlled flows; import must be idempotent and validated.
  - Acceptance pointer: import-tools endpoint integration tests, validation test cases.
  - Owning task: BFF-FINAL-006

- Requirement: Tool action imports should not implicitly enable standalone create paths unless explicitly authorized by governance flags.
  - Acceptance pointer: governance flag checks in import tests.
  - Owning task: BFF-FINAL-006

7) Evidence redaction and read-surface contracts

- Requirement: EvidenceKind and RedactedEvidenceRef semantics must ensure that read surfaces expose redacted references without leaking sensitive payload; redaction must be reversible only by authorized workflows.
  - Acceptance pointer: read-surface tests validating redacted refs and absence of raw evidence in responses.
  - Owning task: BFF-FINAL-007

8) Agora journal JSON Merge Patch facade

- Requirement: Agora journal patch endpoint must accept JSON Merge Patch payloads, apply deterministic merges, and emit an audit diff for each patch.
  - Acceptance pointer: merge-patch acceptance tests and emitted audit diff artifacts.
  - Owning task: BFF-FINAL-008

9) v5 interventions, two-man semantics, and remediation

- Requirement: Interventions (v5) must implement two-man approval semantics where required, surface remediation guards, and log two-man evidence.
  - Acceptance pointer: two-man tests, route gating tests, remediation logs.
  - Owning task: BFF-FINAL-009

10) Overall verification and handoff

- Requirement: Final verification must run the BFF test-suite, confirm cleanup, and produce a delivery note with commit hashes, test run output summary, and push status.
  - Acceptance pointer: test run artifacts and delivery note in `.coordination/responses/`.
  - Owning task: BFF-FINAL-010

## Trace mapping (contract clause → owning task)

- Command admission / Idempotency & CommandResponse envelope → BFF-FINAL-002
- Precondition error semantics (non-2xx BffErrorEnvelope, token/approval enforcement) → BFF-FINAL-003
- Backend canonical action catalog (descriptor ↔ action) → BFF-FINAL-004
- SSE approval & ask channels (replay metadata & degradation semantics) → BFF-FINAL-005
- MCP import / tool action import semantics → BFF-FINAL-006
- Evidence redaction contract (EvidenceKind, RedactedEvidenceRef) → BFF-FINAL-007
- Agora journal JSON Merge Patch facade → BFF-FINAL-008
- v5 interventions & two-man semantics → BFF-FINAL-009
- Overall verification & handoff → BFF-FINAL-010

## Open ambiguities / potential gaps (expanded)

1. Test artifacts locations: some acceptance criteria reference "tests" (e.g., SSE tests, idempotency tests). Confirm canonical test paths, which CI job(s) run them, and whether sidecar should list exact pytest invocation or only reference test files.
2. Push/publication: ensure BFF-FINAL-010 closeout will include branch push; record `push_status` during finalization and verify upstream remote is configured.
3. Overlapping ownership: BFF-FINAL-002 and BFF-FINAL-006 both touch command admission surface — clarify adapter-level admission (BFF-layer) versus import-time guards (MCP/tool-level). Recommend a short ownership note per sub-surface.
4. Evidence redaction runtime keys: confirm which fields are considered sensitive for redaction and which workflows can un-redact. Recommend adding a small artifact listing EvidenceKind → sensitive fields mapping.
5. SSE replay retention: define retention window for replay metadata used in audit; this may affect storage/backing store tasks and should be included in acceptance notes.

## Recommended short checklist for BFF-FINAL-010 owner (expanded)

- Re-read consensus packet and each task artifact listed above.
- For each acceptance pointer, confirm either an executable test exists, or an explicit artifact (file/endpoint) is listed. If missing, add a small follow-up task and assign owner.
- Run focused verification commands (examples):
  - pytest services/control-plane/bff/tests::test_idempotency -q
  - pytest services/control-plane/bff/tests::test_sse_channels -q
  - pytest services/control-plane/bff/tests:: -q  # run BFF suite
- Capture commit hashes for any task-scoped commits and stage only task files.
- Produce final delivery note and coordinate response in `.coordination/responses/` including:
  - Task-ID, owner, reviewer
  - Commit hash(es)
  - Test run summary (pass/fail and key failures)
  - `push_status` and remote/upstream info
- If any ambiguous ownership remains, request a small follow-up re-dispatch from the reviewer (Codex) with a short spec fragment.
- After closeout checklist completes, run:
  - `AI_NAME=Copilot ./scripts/ai-status.sh done BFF-FINAL-SIDECAR-COPILOT-SPEC-TRACE "Spec-trace artifact created and handoff packet ready"`

## Open deliverables for reviewer (Codex)

- Confirm that the expanded grouped requirements align with the canonical contract and that no actionable canonical code changes are implied.
- Indicate whether ambiguities 1-5 above require re-dispatch, or can be resolved during BFF-FINAL-010 closeout.




