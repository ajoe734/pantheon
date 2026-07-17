# Persona daily strict-operator execution tasks

Date: 2026-07-17
Planning source:
`docs/product/persona-interaction-daily-strict-operator-delivery-plan.md`

## Delivery graph

| Task | Repository | Depends on | Deliverable |
| --- | --- | --- | --- |
| `PINT-011` | Pantheon | — | Product truth, lifecycle, opinion, candidate-decision, and authority contracts |
| `PINT-012` | Pantheon | `PINT-011` | Real selected-Persona OpenClaw invocation and synthesis |
| `PINT-013` | Pantheon | `PINT-012` | Durable interaction lifecycle, readback, SSE, retry, and restart recovery |
| `PINT-014` | Pantheon | `PINT-011`, `PINT-013` | Persona-derived candidates, daily decisions, and authoritative validation |
| `PINT-015` | execute-plans | `PINT-011`–`PINT-014` | Daily contextual Persona, Workshop, reflection, and proposal UX |
| `PINT-016` | Pantheon | `PINT-011` | Strict browser operator session and hosted readiness |
| `PINT-017` | execute-plans | `PINT-015`, `PINT-016` | Persistent immutable `operator-live` release/deployment profile |
| `PINT-018` | Pantheon evidence owner | `PINT-013`–`PINT-017` | Exact-SHA hosted desktop/mobile acceptance and task-truth closeout |

`PINT-012` and `PINT-016` may execute in parallel. Contract/scaffolding work
for `PINT-014` may start early, but integration cannot finish before
`PINT-013`. No fixture, canned simulator, permissive proof window, or browser
storage override satisfies a product acceptance item.

## PINT-011 — Product truth and contract reset

Artifacts:

- additive schemas/OpenAPI for interaction lifecycle, provider invocation,
  independent typed opinion, synthesis, structured recommended measure,
  candidate decision, authoritative validation receipt, and readback;
- authority and storage ownership matrix;
- explicit supersession note on the daily-delivery interpretation of
  `PINT-010-R2` without rewriting its historical evidence;
- reconciled execution-task truth for the new correction program.

Acceptance:

- production contracts forbid keyword-driven or canned opinions;
- `accept-for-review` is distinct from formal approval and execution;
- Persona/version/provider provenance, evidence freshness, partial failure,
  disagreement, and no-consensus are representable;
- no-order, no-broker, no-capital, no-binding, no-promotion, no-policy, and
  no-memory authority is explicit and tested;
- the Pantheon PR is merged before dependent implementation merges.

## PINT-012 — Real selected-Persona OpenClaw invocation

Artifacts: governed Persona agent admission/ensure, per-participant provider
invocation, typed response validation, independent opinions, synthesis input,
degraded handling, and tests.

Acceptance:

- tenant, Persona id/version, workspace, provider agent, environment ceiling,
  and capability snapshot are exact and fail closed;
- each selected Persona is invoked independently through the authenticated
  OpenClaw adapter/provider path and receives the immutable typed context;
- provider-returned unique content and provenance are visible in readback;
- synthesis begins only after independent results and never rewrites them;
- provider unavailability produces degraded/failed state, not a forged opinion;
- `simulate_interaction_debate_and_synthesis` and magic-topic production
  behavior are removed;
- tests prove zero tool/order/broker/capital/binding/memory authority; PR merged.

## PINT-013 — Durable interaction lifecycle and readback

Artifacts: Postgres-owned interaction request/invocation/opinion/synthesis/error
records, GET list/detail routes, Workshop timeline/SSE projection, outbox,
claim/retry/idempotency, restart recovery, and tests.

Acceptance:

- the human request and every provider result/error have immutable provenance;
- reload, relogin, reconnect, and BFF restart return the same records;
- idempotent replay cannot duplicate provider side effects, cards, or events;
- partial participant failure preserves successful opinions and identifies
  missing/degraded participants;
- pending and completed outbox recovery is deterministic with RPO zero;
- process-local maps are not the authoritative session/event store; PR merged.

## PINT-014 — Daily candidate decision semantics

Artifacts: candidate generation from typed Persona recommendations, durable
modify/accept-for-review/reject/defer decisions, authoritative validation
adapter/receipt, linkage, audit, authorization, and tests.

Acceptance:

- a candidate measure is derived from `recommended_measures`, not the human
  topic wrapper;
- modify creates a revision; accept-for-review and reject record actor,
  rationale, time, exact revision/digest, interaction, and Persona provenance;
- accept-for-review performs no execution, promotion, binding, or memory write;
- browser-supplied arbitrary validation JSON is not authoritative;
- formal approval retains distinct reviewer, exact receipt/revision/digest,
  expiry, tenant, ETag/idempotency, and self-approval denial;
- restart/readback and negative authority tests pass; PR merged.

## PINT-015 — Daily Workshop and contextual UX

Repository: `ajoe734/execute-plans`.

Artifacts: Persona Detail direct actions, canonical contextual Workshop,
real lifecycle/timeline, independent opinion and disagreement cards,
follow-up/reflection, candidate decision UI, validation/reviewer UI, durable
readback, responsive/accessibility states, and tests.

Acceptance:

- Talk, Challenge, Compare, Propose, and Reflect are direct Persona Detail
  entries and preserve selected Persona, mode, source context, and return path;
- fixed simulator context/toggles are absent from the live product path;
- queued/running/partial/degraded/failed and retry states use backend truth;
- an operator completes modify, accept-for-review, reject/defer, validation,
  and eligible reviewer decisions without direct-API fallback;
- reload/readback keeps human request, opinions, synthesis, candidate revision,
  decision, and audit linkage;
- viewer controls fail closed and the BFF remains authoritative;
- desktop/mobile a11y, unit, integration, and strict-live tests pass; PR merged.

## PINT-016 — Strict browser operator auth and readiness

Artifacts: a production-shaped browser-to-BFF session path, JWT/cookie issuer,
audience and role mapping, refresh/logout behavior, CORS/CSRF/tenant controls,
strict CI credentials, provider/adapter readiness checks, and tests.

Acceptance:

- product login yields `/bff/me` authenticated with `cookie` or `bearer`
  session kind and BFF-owned operator roles/capabilities;
- no client secret or privileged default token enters the frontend bundle;
- strict operator mutation succeeds, viewer is 403, unauthenticated is 401,
  and a stub session is rejected;
- refresh/logout cannot leave a stale privileged BFF session;
- selected Persona admission and OpenClaw readiness are green on the exact
  deployed strict BFF SHA;
- BFF remains `auth_mode=strict` and `auth_stub=false`; PR merged.

## PINT-017 — Persistent operator-live release profile

Repository: `ajoe734/execute-plans`.

Artifacts: third immutable release candidate/profile, identity and digest
schemas, integration gate, deploy controller/workflow, rollback policy,
manifest, runbook, and tests.

Acceptance:

- `read-only`, `operator-live`, and bounded `write-proof` are independent
  artifacts and digests;
- `operator-live` is live/strict, real writes true, stub writes false, and has
  no embedded bearer;
- deploy requires an exact healthy BFF with strict auth and matching SHA;
- operator deployment does not arm the proof watchdog or auto-restore
  read-only;
- rollback selects an exact previously accepted operator/read-only artifact;
- hosted manifest and bundle scan prove profile truth and zero credential
  material; PR merged.

## PINT-018 — Hosted daily product acceptance and closeout

Artifacts:

- exact paired BFF/frontend deployment records;
- authenticated strict desktop/mobile product test reports;
- restart/reconnect/readback, viewer/unauth/self-approval, degraded-provider,
  idempotency, audit, and zero-execution evidence;
- `PINT-018-EVIDENCE.md` and checksummed machine-readable evidence;
- reconciled task archive/orchestrator status and independent review.

Acceptance:

- hosted flows cover ask, challenge, two-Persona disagreement, reflection,
  Persona-generated proposal, modify, accept-for-review, and reject;
- the claimed UI flow contains no direct-API fallback, runtime write override,
  permissive stub, retry masking, skipped case, or fake provider response;
- refresh, relogin, reconnect, replay, and BFF restart preserve exact readback;
- negative proof records zero orders, broker calls, capital/runtime binding,
  lifecycle promotion, policy mutation, and memory mutation;
- final hosted manifest remains `operator-live` with real writes true, stub
  writes false, no embedded token, BFF strict, and BFF stub false;
- all PRs/checks/deploys and exact SHAs are recorded and independently reviewed;
  only then may the correction program and its parent product goal be complete.

## Shared repository discipline

Every task must begin on the latest delivery branch in a clean worktree, change
only declared scope, run focused and adjacent tests, commit with `LLM-Agent`,
`Task-ID`, `Reviewer`, and `Verified` trailers, push, open a PR, wait for visible
checks, merge, and record the merge SHA. Pantheon and execute-plans changes are
never combined in one repository commit.

