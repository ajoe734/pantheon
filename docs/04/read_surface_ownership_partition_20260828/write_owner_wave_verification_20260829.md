# Independent write-owner wave verification — 2026-08-29

Task: `ACG-WRITE-OWNER-WAVE-VERIFY-20260829`  
Owner: `Codex`  
Reviewer: `Codex2`  
Artifact status: `ready_for_independent_review`  
Scope: verification record for the resumed `ACG-RS-CALLER-MIGRATION-20260828` task; this document does not change canonical product policy.

## Verdict

The five dependency tasks are canonically `done`, their approved heads are merged into `dev`, and their focused persistence suites pass together (`57 passed`). The suites prove that each implemented store can write and then read through a fresh owner/store instance without importing `services/control-plane/bff/read_store.py`.

That evidence does **not** prove that all 31 remaining `ReadSurfaceStore` mutation members can be migrated without changing their contracts. The wave is therefore **persistence-proven but not caller-migration-ready**.

The exact source inventory remains 31 mutation member names across 46 direct `read_store` references in `main.py`. Twenty member names have a usable concrete owner destination now. Eleven require a deliberate disposition before the caller-migration task can truthfully reach zero direct mutation calls:

- four Rankings mutations do not fit the delivered `RankingRecord` schema;
- two Persona mutations cannot represent the provisioning lifecycle states used by current callers;
- `patch_capital_pool` accepts `params` at the BFF boundary while the owner accepts `metadata` and rejects `params`;
- two Decision Journal mutations have two competing durable owners;
- DeploymentPlan and RuntimeBinding have authoritative owners, but their command contracts are stricter than the current local-overlay calls and require canonical request composition rather than a name-for-name replacement.

The resumed caller-migration task must not recreate a mutable compatibility facade, write into `ReadSurfaceStore`, or treat the submitted response as persistence proof. It may migrate the ready rows below, but the blocked rows require owner/API repair or an explicit contract migration first.

## Frozen dependency evidence

| Dependency | Approved head | PR | Merge evidence | Review manifest | Canonical state |
|---|---|---:|---|---|---|
| `ACG-WRITE-OWNER-AGORA-20260829` | `4dbbf77115e3f0b01ec9f8b9a0e5fc96e6643902` | #5368 | `52eccd0047f583f40d4829fd27af400e4878431a` | `tests/agora_write_owner/evidence.json` | `done` |
| `ACG-WRITE-OWNER-RESEARCH-20260829` | `854ad85b769916b1516ec4064210a463e2fa32b8` | #5365 | `f177c88a50a9e1b95c3cfde3267a1056ee9064ad` | `tests/research_write_owner/evidence.json` | `done` |
| `ACG-WRITE-OWNER-RANKINGS-20260829` | `35de1ae5c89edcdb740c773a3aa41397e10ef526` | #5364 | `9b8f0d10042c647f3dbe6fe322f9589d21477dfd` | `tests/ranking_write_owner/evidence.json` | `done` |
| `ACG-WRITE-OWNER-PERSONA-CAPITAL-20260829` | `503a3974cf83fafc640a32761785563451b2c949` | #5367 | `837d031954f66b3f4ed6e97d807c04ca44ae5fcf` | `tests/persona_capital_write_owner/evidence.json` | `done` |
| `ACG-WRITE-OWNER-GOVERNANCE-20260829` | `7fbc97137c03b863c1457f97540bd7b42992fecc` | #5366 | `fb5d54fbf45685080eeb9bd6f7ec034df71ed597` | `tests/governance_write_owner/evidence.json` | `done` |

The canonical archive was read through the governed command root. No conclusion in this record is based only on the task worktree's stale `ai-status.json` snapshot.

## Independent verification

Executed from `task/ACG-WRITE-OWNER-WAVE-VERIFY-20260829`:

```text
python3 scripts/dev/provision_python_distribution.py
PANTHEON_PY=$(python3 scripts/dev/provision_python_distribution.py --print-python)
$PANTHEON_PY -m pytest -q \
  tests/agora_write_owner \
  tests/research_write_owner \
  tests/ranking_write_owner \
  tests/persona_capital_write_owner \
  tests/governance_write_owner

57 passed, 1 warning in 12.72s
```

The warning is the existing Starlette `TestClient` deprecation warning. The five approved diffs contain no change to `services/control-plane/bff/main.py`, `services/control-plane/bff/read_store.py`, or `services/source_ingestion/`. The owner modules and their tests also enforce the no-`read_store` and fresh-instance read boundaries.

Fresh-read proof by owner:

| Owner surface | Persistent implementation exercised | Fresh-reader assertion |
|---|---|---|
| Agora | `AgoraWriteService` -> `AgoraStore` -> `PostgresJsonOwnerStore` | a second `AgoraStore` reads the committed record from the shared fake Postgres table |
| Research | `ResearchWriteOwner` -> three `PostgresJsonOwnerStore` tables | a new owner instance reads tickets, experiments, and notes |
| Rankings | `RankingWriteStore` -> `PostgresJsonOwnerStore` | a new store instance reads the generic `RankingRecord` that was written |
| Persona / Capital / Deployment / Runtime | `PersistentPersonaOwner`, `PersistentCapitalPoolStore`, `DeploymentPlanStore`, `RuntimeManagerService` | new owner/store/service instances read the persisted records |
| Governance Decision Journal | `DecisionJournalStores` -> governance JSON/Postgres record stores | newly built stores read the created/patched entry and audit/idempotency records |

This is persistence evidence for the delivered abstractions. It is not evidence that a different legacy payload can be projected into those abstractions without loss.

## Route-to-owner map for caller migration

Status meanings:

- **ready** — an exact concrete owner operation exists; caller work still must preserve auth, idempotency, error, and response behavior.
- **compose** — the owner is authoritative, but the current BFF request is not the owner request; the caller must join canonical inputs and invoke the real command boundary.
- **blocked** — the delivered API cannot preserve the current write contract, or ownership is ambiguous.
- **delete dead tail** — the old mutation is after an unconditional deprecation return and should be removed, not dispatched.

| Legacy `read_store` mutation | Current route/helper sites | Concrete destination | Status and migration note |
|---|---|---|---|
| `append_agora_committee_evidence_files` | `POST /bff/agora/committee/{sessionId}/evidence-pack/files` | `build_agora_write_service().append_evidence_files(...)` | **ready**; unpack the BFF file list and pass verified actor roles. |
| `cancel_research_experiment` | `POST /api/v1/experiments/{experiment_id}/cancel` | `ResearchWriteOwner.cancel_research_experiment(...)` | **ready**. |
| `close_committee_session` | `POST /bff/agora/committee/sessions/{sessionId}/close` | `AgoraWriteService.close_committee_session(...)` | **ready**. |
| `create_agora_committee_evidence_pack` | `POST /bff/agora/committee/{sessionId}/evidence-pack` | `AgoraWriteService.create_evidence_pack(...)` | **ready**. |
| `create_agora_feedback` | `POST /bff/agora/feedback` | `AgoraWriteService.create_feedback(...)` | **ready**. |
| `create_agora_handoff` | persona-lab submit-commit; committee memo publish | `AgoraWriteService.create_handoff(...)` | **ready**; preserve both call-site payload projections. |
| `create_agora_note` | `POST /bff/agora/notes` | `AgoraWriteService.create_note(...)` | **ready**. |
| `create_agora_session` | `POST /bff/agora/committee/sessions` | `AgoraWriteService.create_session(...)` | **ready**. |
| `create_agora_signal` | `POST /bff/agora/signals` | `AgoraWriteService.create_signal(...)` | **ready**. |
| `create_agora_training_example` | `POST /bff/agora/training-examples` | `AgoraWriteService.create_training_example(...)` | **ready**. |
| `create_decision_journal_entry` | `POST /bff/agora/journal` | `build_decision_journal_stores(...)` + `services.governance.decision_journal.create_entry(...)` | **blocked** until the competing `AgoraWriteService.create_journal_entry` / `agora.journal_entries` owner is retired. The Governance API matches the current `body`, visibility, linked-id, and CAS/idempotency contract; the Agora API uses a different `decision` record shape. |
| `create_deployment_plan` | `POST /api/v1/deployment-plans` | authenticated `POST /api/deployment/plans` -> `DeploymentPlannerService.create_plan(..., persist=True, actor_id, tenant_id)` | **compose**; supply a real approval decision and registry entry/ref, use authenticated tenant identity, and return owner readback. The old local call's `artifact_id`/`deployment_mode`/`locked` shape is not the service request contract. |
| `create_persona` | `_persona_record_for_provisioning`, reached from `POST /bff/personas` | authenticated `POST /api/personas` -> `PersistentPersonaOwner.create(CreatePersonaRequest)` | **blocked** for current provisioning: the owner requires creation in `draft`, while the caller creates `provisioning`, `paper_running`, or `provisioning_failed` records. Do not bypass the lifecycle API. |
| `create_ranking_formula` | deprecated `POST /bff/ranking/formulas` tail | none needed for the unreachable tail | **delete dead tail**. If formula writes are restored, `RankingWriteStore` is not an exact formula owner because `RankingRecord` has no formula `name`, `description`, `params`, or versioned formula payload. |
| `create_research_experiment` | `POST /api/v1/experiments/launch` | `ResearchWriteOwner.create_research_experiment(...)` | **ready**. |
| `create_research_note` | `POST /api/v1/knowledge/notes` | `ResearchWriteOwner.create_research_note(...)` | **ready**. |
| `create_research_ticket` | `POST /api/v1/research/tickets`; persona strategy-match action helper | `ResearchWriteOwner.create_research_ticket(...)` | **ready**; both sites use the same owner. |
| `create_runtime_binding` | `POST /bff/runtimes` | `RuntimeManagerClient.deploy(request)` -> `POST /api/runtimes/deploy` -> `RuntimeManagerService.deploy(...)` | **compose**; the owner requires an approved/executing plan, active PersonaCapitalBinding, allowed scope, loader proof, artifact version, and governed paper deployment. The local stopped-runtime payload is not an admissible owner command. |
| `open_committee_session` | `POST /bff/agora/committee/sessions/{sessionId}/open` | `AgoraWriteService.open_committee_session(...)` | **ready**. |
| `patch_capital_pool` | `PATCH /bff/capital-pools/{pool_id}` | `build_capital_pool_store(...).patch(pool_id, patch=..., updated_at=...)` | **blocked**: the BFF accepts and forwards `params`; `PersistentCapitalPoolStore._PATCH_FIELDS` accepts `metadata` and rejects `params`. Define an explicit projection or change one contract before cutover. |
| `patch_decision_journal_entry` | `PATCH /bff/agora/journal/{entry_id}` | `services.governance.decision_journal.patch_entry(...)` | **blocked** by the same dual-owner conflict. The Governance implementation is the one with atomic idempotency reservation and the exact caller-supplied `request_hash`; the Agora implementation is a separate table and protocol. |
| `patch_ranking_formula` | deprecated `PATCH /bff/ranking/formulas/{formula_id}` tail | none needed for the unreachable tail | **delete dead tail**; do not map it to generic `put_ranking`. |
| `patch_research_ticket` | `PATCH /api/v1/research/tickets/{ticket_id}` | `ResearchWriteOwner.patch_research_ticket(...)` | **ready**. |
| `publish_committee_session_memo` | `POST /bff/agora/committee/sessions/{sessionId}/memos/{memoId}/publish` | `AgoraWriteService.publish_committee_memo(...)` | **ready**. |
| `put_allocation_evaluation` | real- and paper-allocation materialization helpers | no lossless delivered Rankings API | **blocked**: the durable payload requires `allocation_evaluation_id`, snapshot/policy ids, content digest, full allocation lines, authority mode, and promotion review fields. `RankingRecord.from_dict` drops them. |
| `put_ranking_snapshot` | `_pm12_attach_ranking_snapshot`, used by quarterly/league ranking routes | no lossless delivered Rankings API | **blocked**: the snapshot requires `ranking_snapshot_id`, surface, period, formula version, content digest, items, and evidence digests. `RankingRecord` cannot round-trip that record. |
| `record_agora_audit_event` | Agora commands/routes, management NL helpers, and one callback reference | `AgoraWriteService.record_audit_event(...)` | **ready**; add one typed adapter for the current event-dict/callback form rather than exposing the store. |
| `record_agora_signal_feedback` | `POST /bff/agora/signals/{signalId}/feedback` | `AgoraWriteService.record_signal_feedback(...)` | **ready**. |
| `record_sponsor_decision` | `RECORD_SPONSOR_DECISION` background command | `ConsultationServiceClient.record_sponsor_decision(...)` -> `POST /api/consult/committees/{committee_id}/sponsor-decision` | **ready**; this pre-existing Consultation owner persists the request/handoff and must replace the BFF store fallback. |
| `submit_committee_session_memo` | `POST /bff/agora/committee/sessions/{sessionId}/memos` | `AgoraWriteService.submit_committee_memo(...)` | **ready**. |
| `update_persona` | provisioning ledger/reconciler; `PATCH /bff/personas/{persona_id}` | registry edits -> authenticated `PATCH /api/personas/{id}`; lifecycle -> authenticated `PATCH /api/personas/{id}/lifecycle` | **blocked** for provisioning lifecycle. The owner transition graph contains `draft`, `research_only`, `consultable`, `paper_owner`, `live_owner`, `frozen`, and `retired`; current callers write `provisioning`, `paper_running`, and `provisioning_failed`. Generic patch correctly forbids lifecycle bypass. |

Inventory total: **31 distinct mutation names / 46 direct references**. The rows above include all 31 names from the rejected caller-migration head's `RETAINED_WRITES_DEFERRED_FROM_READ_SURFACE` inventory and the current `main.py` AST; no mutation name is omitted.

## Required repair boundary

Before `ACG-RS-CALLER-MIGRATION-20260828` can claim zero direct mutation calls without regressions:

1. Provide typed Rankings records/stores (or separate tables) for formulas, immutable ranking snapshots, and allocation evaluations, with fresh-reader tests that round-trip every integrity field. Generic `RankingRecord` encoding is not an acceptable substitute.
2. Reconcile Persona lifecycle vocabulary and authority. Provisioning must either become metadata owned by its provisioning ledger while Persona stays in the canonical lifecycle, or the Persona owner must explicitly model those states and transitions. Caller code must not use generic patch as a lifecycle bypass.
3. Reconcile CapitalPool `params` versus `metadata` at one explicit contract boundary and cover fresh readback of the chosen field.
4. Select Governance Decision Journal as the single owner for `/bff/agora/journal`, retire the duplicate Agora journal write API/tables, and retain Agora audit as a separate append after the journal owner commits.
5. Compose DeploymentPlan and RuntimeBinding commands from canonical approval, registry, PersonaCapitalBinding, loader, tenant, and auth evidence. Do not translate the old overlay payload mechanically.

## Protected paths

This verification task changes only this evidence file. It does not modify:

- `services/control-plane/bff/main.py`
- `services/control-plane/bff/read_store.py`
- any owner implementation
- Source Ingestion
- canonical architecture or policy documents
