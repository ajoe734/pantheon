# AG-BE-RS-001 BFF and Frontend Handoff Follow-up 2

| Field | Value |
|---|---|
| Task ID | `AG-BE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `AG-BE-RS-001` - ResearchPlan facade/router |
| Parent owner / reviewer | `Claude` / `Claude2` |
| Prepared by | `Codex` |
| Reviewer | `Claude` |
| Date | 2026-06-21 |
| Mutates canonical truth | `false` |
| Status | Ready for reviewer handoff |

This is a support artifact only. It does not modify L1 canonical truth, OpenAPI,
JSON schemas, BFF runtime, research services, registry/governance code, or the
`execute-plans` frontend. It is a review-time follow-up to the earlier packet at
`support/sidecars/AG-BE-RS-001/AG-BE-RS-001-SIDECAR-BFF-HANDOFF.md`, now that
parent PR #2087 exists for `AG-BE-RS-001`.

## Purpose

The first sidecar packet described the BFF query gap, operator journey, and
frontend handoff boundaries before the parent implementation was available.
This follow-up narrows the handoff to PR #2087:

- confirm which plan-first BFF surfaces the PR appears to cover;
- name the review questions that should be resolved before parent absorption;
- keep the AG-BE-RS-001 / AG-BE-RS-002 / AG-FE-RS-001 boundary explicit;
- avoid changing runtime or canonical contract files from this sidecar.

## Sources Read

| Source | Relevant finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | L0 state coordinates work; support packets do not override canonical truth. |
| `.orchestrator/task-briefs/ag_be_rs_001_sidecar_bff_handoff_followup_2.md` | Task is support-only: prepare BFF query gap, operator journey, and frontend handoff materials without canonical changes. |
| `.orchestrator/skills/worker-anchor-commit.md` | Task-owned support changes need explicit scope and narrow commit discipline. |
| `.orchestrator/skills/task-closeout-finalization.md` | Repo changes require task commit, PR, merge, and owner closeout before `done`. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` | Active sidecar is `in_progress`, owner `Codex`, reviewer `Claude`, helper parent `AG-BE-RS-001`. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-RS-001` | Parent is in `review`; PR #2087 is open with anchor commit `6ddd6ffd`. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-RS-001-SIDECAR-BFF-HANDOFF` | First support packet is archived `done` and merged. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-RS-002` | Unified run/progress/result projection is still `todo`; it depends on `AG-BE-RS-001`. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-RS-001` | Frontend research cards are still `todo`; they depend on `AG-BE-RS-002`. |
| `gh pr view 2087 --json ...` | PR #2087 is open, non-draft, targets `dev`, and has green Branch CI checks at the time of reading. |
| `origin/pr/2087:services/control-plane/bff/agora/research/router.py` | Parent PR adds the research BFF route handlers and stage-routing policy. |
| `origin/pr/2087:services/control-plane/bff/agora/research/store.py` | Parent PR adds an in-memory `ResearchPlanExecution` / `ResearchRunProjection` store. |
| `services/control-plane/openapi/agora_v1_3.openapi.yaml` | v1.3 lists 10 plan-first research plan/run routes. |
| `services/control-plane/specs/agora/v4/research_plan_execution.schema.json` | `ResearchPlanExecution` has `additionalProperties=false`; lifecycle and stage routing enums are closed. |
| `services/control-plane/specs/agora/v4/research_run_projection.schema.json` | `ResearchRunProjection` defines run lineage, backend, progress, outcome, refs, and no-order proof. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/02_research_facade_run_projection.md` | Plan-first rule, approval gate, typed stages, fallback rules, DAG/concurrency, and run projection scope. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/05_workshop_card_contracts.md` | Frontend card sources: `research_plan_proposal`, `research_progress`, and `research_result`. |

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned.

## PR #2087 Snapshot

| Surface | Observed in PR #2087 | Handoff meaning |
|---|---|---|
| Route coverage | Adds handlers for the 10 v1.3 research routes listed under plan/run APIs. | This addresses the largest BFF query gap from the first sidecar packet. |
| Plan lifecycle | Creates draft plans, approves draft plans, cancels draft/approved/running plans, and moves approved plans to running when dispatch is requested. | Good plan-first shape; reviewer should still check schema-conformant response data. |
| Stage routing | Provides a fixed `stage_type` to backend map for the 12 v4 stage types. | Good direction; reviewer should confirm caller-supplied routing cannot bypass the policy. |
| No-order proof | Sets `research_plan_no_order_route` on plans and `research_only_not_direct_action` on runs. | Preserves the no-order boundary expected by the first handoff. |
| Store | Uses an in-memory `MemoryResearchPlanStore`; env name reserves a future backend but only memory exists. | Acceptable for facade/dev smoke if documented as non-durable; not sufficient as durable research truth. |
| Run dispatch | Creates a queued run projection from the first pending/ready stage and marks the plan running. | This is a thin run facade. It should not be treated as AG-BE-RS-002 result/progress completion. |
| Artifacts | Lists `artifact_refs` and `evidence_refs` already present on the run object. | Useful shape, but actual production of artifacts/evidence remains AG-BE-RS-002. |
| Branch CI | `Commit trailers`, `Runtime mirror guard`, and `Smoke acceptance` were green when read. | Good branch health, not a substitute for reviewer schema/contract review. |

## Reviewer Attention Items

These are support observations for the parent reviewer/owner. This sidecar does
not change PR #2087.

| Item | Why it matters | Suggested reviewer decision |
|---|---|---|
| `ResearchPlanExecution` response data may expose non-schema fields | PR #2087 stores and returns `lock_version`, and may return `execution_constraints`. The v4 plan schema has `additionalProperties=false` and does not list either field. | Either keep these fields internal / envelope-only, add a reviewed contract change elsewhere, or explicitly defer and fail schema-conformance acceptance. |
| Create request model is wider than the v4 plan schema | Request accepts `execution_constraints`, while OpenAPI references `ResearchPlanExecution` as the create body. | Decide whether create uses exact `ResearchPlanExecution` or a separate create DTO; do not leave frontend guessing. |
| Stage routing inputs are not fully enum constrained | PR validates `stage_type`, but caller-provided `routing.preferred_backend`, `routing.fallback_policy`, `routing.backend_mode`, `stage.status`, and budget fields are not constrained to the schema enums/ranges in the request model. | Lock these to the v4 schema enums/ranges or ignore caller-supplied backend values and always resolve through policy. |
| Caller-provided backend can override policy | `_build_plan()` uses `routing.preferred_backend` when present instead of always using the canonical stage policy. | If design intent is "LLM proposes intent, route policy resolves backend", reviewer should require policy to win or require strict allowlist validation. |
| Governance approval preconditions look partial | PR blocks `live` / `canary` environments, but the design also calls out private/restricted data, paid data, heavy compute, policy training/RL, budget/runtime overage, and tenant boundary crossing. | Decide which governance gates AG-BE-RS-001 must enforce now versus which are explicitly deferred to a follow-up task. |
| Queued run may look like real dispatch | Dispatch creates `execution_status=queued` and `backend.mode=real` by default, but the store is memory-only and no actual worker gateway is visible in the PR files. | If no real queue is attached, return a typed blocked/unavailable state or explicitly label fixture/stub rather than implying real execution. |
| Run/progress/result depth still belongs to AG-BE-RS-002 | PR exposes thin run read/cancel/artifact routes, but metrics, findings, real progress, artifact generation, evidence refs, and data cutoff are not produced here. | Keep AG-BE-RS-002 as the owner for full `ResearchRunProjection` semantics and frontend result readiness. |
| ETag is carried in envelope metadata | Detail responses include `meta.etag`; write routes require `If-Match`. | Frontend handoff should say to read `meta.etag` unless the parent adds a real HTTP `ETag` response header. |
| List pagination is currently non-functional | Handlers accept `cursor` and `limit` for plan list, but return all memory-store items with `next_page_token=null`. | Accept for dev smoke only, or require pagination behavior before treating the route as production-ready. |

## Updated BFF Query Gap Matrix

| Gap from first packet | PR #2087 state | Follow-up guidance |
|---|---|---|
| Workshop plan list | Implemented in PR route handler. | Reviewer should check list envelope and pagination expectations. |
| Draft plan creation | Implemented with draft status, idempotency key, and plan envelope. | Reviewer should check exact body/response schema conformance. |
| Plan detail | Implemented with detail envelope and `meta.etag`. | Frontend can bind to detail once schema leak questions are resolved. |
| Plan approval | Implemented for `draft -> approved`. | Governance preconditions may need stronger enforcement or explicit deferral. |
| Plan cancel | Implemented for draft/approved/running. | Check idempotency and conflict behavior against frontend expectations. |
| Stage routing | Implemented as a route-policy map, but caller overrides appear possible. | Review should ensure policy, not caller raw tool name, is authoritative. |
| Legacy workshop run route bypass | PR does not modify the legacy `POST /bff/agora/workshops/{workshop_id}/research-runs` stub. | Guardrail remains: do not revive it as an approval bypass. |
| Run projection | Thin route exists and returns stored queued run data. | Full run/progress/result semantics remain AG-BE-RS-002. |
| Artifact/evidence refs | Thin route lists refs from stored run object. | Actual production/linkage remains AG-BE-RS-002. |
| Frontend client/types | Not part of PR #2087. | AG-FE-RS-001 should wait for parent merge and AG-BE-RS-002 projection readiness. |

## Operator Journey Delta

### Draft And Review

Once PR #2087 is accepted, the operator journey can start with real BFF plan
routes:

1. UI creates a draft plan through
   `POST /bff/agora/workshops/{workshop_id}/research-plans` with an
   `Idempotency-Key`.
2. UI reads the returned detail envelope and stores the returned `meta.etag`.
3. UI presents the draft as a `research_plan_proposal` card.
4. UI must not show the plan as executable if reviewer decides the route still
   needs stronger schema/gov gate fixes.

### Approve And Dispatch

1. UI approves through `POST /bff/agora/research-plans/{plan_id}/approve` with
   `Idempotency-Key` and `If-Match` using the latest `meta.etag`.
2. If dispatch is exposed, UI calls
   `POST /bff/agora/research-plans/{plan_id}/runs` only for `approved` plans.
3. A returned `queued` run should be shown as queue intent only unless parent
   owner confirms a real research worker gateway is attached.
4. UI should display `backend.mode`, warnings, blocking reasons, and no-order
   research-only labels; it must not render promotion, canary, live, capital, or
   order controls from these responses.

### Monitor And Results

Until `AG-BE-RS-002` lands, frontend cards should treat run/progress/result
data as limited:

- `research_progress` can show queued/cancelled state and backend label if the
  route is accepted, but not real progress proof.
- `research_result` should remain gated because metrics, findings, artifacts,
  evidence refs, and data cutoff are not produced by AG-BE-RS-001.
- Empty artifact lists are not evidence that a completed run had no artifacts;
  they only mean the thin store lacks produced refs.

## Frontend Handoff Delta

| UI / client need | Follow-up guidance |
|---|---|
| BFF client | Add methods only after the parent PR route shape is final; do not point components directly at BFF fetches outside the shared client. |
| ETag / concurrency | Read `meta.etag` from plan detail/list item envelopes unless parent adds HTTP `ETag`; send it as `If-Match` on approve/cancel/dispatch. |
| Idempotency | Generate a new `Idempotency-Key` per write action attempt; map duplicate-key conflicts to an explicit action state. |
| Plan card | Bind to schema-approved `ResearchPlanExecution` fields only; do not depend on `lock_version` or other non-contract data fields. |
| Stage routing display | Show policy-resolved backend and backend mode; do not let UI submit arbitrary backend names as route authority. |
| Dispatch state | Treat returned `queued` runs as dispatch intent unless real worker queue evidence exists. |
| Progress/result cards | Keep full cards behind AG-BE-RS-002; avoid synthetic metrics, artifacts, evidence, or result summaries. |
| Degraded states | Map `412/428` to refresh-required, `409` to lifecycle conflict, `422` to validation/governance failure, `503` or typed blocked stage to capability unavailable. |
| No-order guard | Keep research-only labeling and never expose order/capital/canary/live controls from research responses. |

## Suggested Parent Review Checks

| Check | Expected result |
|---|---|
| Route registration | The 10 v1.3 research routes are available through the Agora router. |
| Schema conformance | Plan `data` validates against `research_plan_execution.schema.json` with no extra fields; run projections validate against `research_run_projection.schema.json`. |
| Stage route policy | All 12 stage types resolve to allowed backend enum values; caller input cannot introduce arbitrary backend names or fallback modes. |
| Governance gates | Required approval/precondition checks are either implemented or explicitly deferred with a follow-up owner. |
| Thin dispatch honesty | If no real worker queue exists, dispatch does not claim real execution success. |
| No-order proof | Plans and runs always carry the required no-order proof enum values. |
| Runtime boundary | Routes do not write `RuntimeBinding`, capital binding, broker orders, or candidate promotion. |
| Frontend readiness | `AG-FE-RS-001` can safely bind plan cards without direct service fetch or local fallback; progress/result cards remain gated on AG-BE-RS-002. |

## Reviewer Handoff

Claude review should verify:

| Check | Expected result |
|---|---|
| Scope | Only this follow-up support artifact and normal task-owned status metadata are in scope. |
| Canonical truth | No canonical docs, schemas, OpenAPI, BFF runtime, research services, registry/governance, or frontend files changed by this sidecar. |
| Factual alignment | Parent `AG-BE-RS-001` is in review with PR #2087 open at commit `6ddd6ffd`; first sidecar packet is archived `done`; `AG-BE-RS-002` and `AG-FE-RS-001` are still not complete. |
| Review usefulness | Packet names route coverage plus the schema, routing-policy, governance, and thin-dispatch questions for parent review without changing parent code. |

Recommended reviewer approval command:

```bash
AI_NAME=Claude REVIEW_FILE=support/sidecars/AG-BE-RS-001/AG-BE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md \
  REVIEW_NOTES_ZH="Support-only follow-up approved: it compares AG-BE-RS-001 PR #2087 against the research BFF handoff, records route coverage, frontend handoff deltas, and reviewer attention items for schema conformance, stage routing, governance gates, and thin run dispatch without modifying canonical truth or runtime files." \
  ./scripts/ai-status.sh approve AG-BE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2 \
  "Support-only AG-BE-RS-001 BFF/frontend follow-up packet approved for parent reviewer use."
```

Recommended reviewer reopen command:

```bash
AI_NAME=Claude ./scripts/ai-status.sh reopen AG-BE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2 \
  "Describe the factual correction, missing PR #2087 observation, or boundary issue needed before approval."
```

## Validation Run

Commands run from this sidecar worktree:

```bash
git status -sb
git branch --show-current
git remote -v
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-RS-001
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-RS-001-SIDECAR-BFF-HANDOFF
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-RS-002
AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-RS-001
gh pr view 2087 --json number,title,state,isDraft,mergeable,headRefName,baseRefName,headRefOid,url,statusCheckRollup,reviewDecision,files
git fetch origin pull/2087/head:refs/remotes/origin/pr/2087
git show origin/pr/2087:services/control-plane/bff/agora/research/router.py
git show origin/pr/2087:services/control-plane/bff/agora/research/store.py
sed -n '380,545p' services/control-plane/openapi/agora_v1_3.openapi.yaml
sed -n '1,260p' services/control-plane/specs/agora/v4/research_plan_execution.schema.json
sed -n '1,300p' services/control-plane/specs/agora/v4/research_run_projection.schema.json
sed -n '1,150p' docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/02_research_facade_run_projection.md
git diff --check -- support/sidecars/AG-BE-RS-001/AG-BE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md
```
