# EPIC DATASTRAT-IDS — Interaction-Derived Strategy Seeds

Generated: 2026-06-12
Companion to: `GAP_ADDENDUM_2026-06-12.md` (gaps #3, #4)
Source vision: 2026-05-20 / 2026-05-23 SA/SD "IDS" half (Trainer / Agora /
Committee / Postmortem as governed strategy-seed sources), reconciled to the
2026-06-09 design.

## Prime directive (do not violate)

Raw chat / raw transcript is **evidence only** and must never become a seed
directly. Every interaction becomes a seed only through:

```
raw interaction
  -> InteractionSourceRecord
  -> IntentClassification
  -> Redaction / Visibility / Scope check
  -> Negative-Memory match
  -> SeedCandidate
  -> (existing) Seed Review Inbox  [DATASTRAT-SEEDFLOW-002]
  -> StrategySpecSeed
```

**Ordering rule:** the safety layer (IDS-002 redaction/visibility, IDS-003 intent
classification, IDS-007 negative-memory) must land **with or before** the first
ingestion bridge (IDS-004). Do not merge a Trainer/Agora->seed bridge while the
guard is absent. This is the single biggest risk called out in the addendum.

## Dependencies on already-delivered work

- Reuses `StrategySpecSeed` store + materializer (DATASTRAT-SEED-004).
- Terminates into the Seed Review Inbox (DATASTRAT-SEEDFLOW-002) — so this EPIC
  should start after, or in parallel with, SEEDFLOW-002's read model.
- Must honor the same invariants the registry already enforces
  (`research_only=true`, `execution_route=none`).

---

## Task breakdown

### IDS-001 — InteractionSourceRecord schema + store
- Deliverable: contract `docs/contracts/interaction_source_record.schema.json`
  (interaction_id, source_surface {trainer, ask_personas, committee,
  decision_journal, notebook, postmortem, ...}, actor_type, persona_refs,
  session_id, raw_ref, summary, evidence_refs, visibility {private/persona/desk/
  shared}, redaction_status {pending/passed/failed}) + JSONL dev store mirroring
  the registry-split pattern.
- Acceptance: record can be created from any surface; `raw_ref` points to
  evidence, never inlined raw text; visibility + redaction_status required.
- Depends: none.

### IDS-002 — Redaction / visibility / scope guard  [SAFETY — must precede IDS-004]
- Deliverable: `redaction_guard` applied at the InteractionSourceRecord boundary;
  enforces scope (tenant/user/persona), strips PII/credentials/capital amounts/
  broker refs/private notes; sets `redaction_status`.
- Acceptance: `redaction_status=failed` blocks any downstream SeedCandidate
  (typed refusal + test); private/persona visibility cannot leak into shared
  results; raw prompt never persisted to the seed store.
- Depends: IDS-001.

### IDS-003 — Intent classification  [SAFETY — must precede IDS-004]
- Deliverable: deterministic-first classifier (no embedding required for v1)
  mapping an interaction to `primary_intent` {strategy_hypothesis, risk_overlay,
  execution_policy, portfolio_allocation, persona_policy, preference_example,
  negative_memory, operational_note, non_strategy} with confidence +
  `requires_human_review`.
- Acceptance: style/coaching -> persona_policy (not strategy); investable
  hypothesis -> strategy_hypothesis; low confidence -> needs_review; non_strategy
  -> archive only. Test table mirrors SA test cases.
- Depends: IDS-001.

### IDS-007 — Negative-Memory matcher  [SAFETY — ship with IDS-004]
- Deliverable: similarity match of a SeedCandidate against retired strategies /
  rejected candidates / failed experiments / postmortems; emits
  `negative_memory_match` {warning_level info|warning|blocking, similarity, reason}.
- Acceptance: blocking match prevents seed acceptance; warning surfaces on the
  seed card; deterministic/keyword match acceptable for v1 (embedding optional
  later).
- Depends: IDS-001; reads existing registry retired/failed records.
- v1 implementation contract: `StrategySpecSeed` carries
  `negative_memory_match`; `SeedMaterializationService` compares new seed
  candidates against rejected/retired/failed seed-store records plus supplied
  failed experiment or postmortem records before store write; `blocking` is
  refused by `StrategySpecSeedStore.save`, while `warning` is retained for seed
  card/read-model display.

### IDS-004 — Trainer-to-seed bridge  [internal sources first]
- Deliverable: consume committed Trainer events (`trainer_commit` /
  `explicit_seed_submission` only — never raw/uncommitted) -> InteractionSourceRecord
  -> classify -> redact -> SeedCandidate; supports seed_kinds new_strategy /
  mutation / risk_constraint / execution_constraint / negative / persona_policy /
  data_requirement.
- Acceptance: only committed content enters; raw teaching log refused; produced
  SeedCandidate lands in the review inbox; `TrainerSeedExtractionRef` lineage
  recorded.
- Depends: IDS-002, IDS-003, IDS-007 (guard must exist).

### IDS-005 — Agora/Committee/Postmortem-to-seed bridge
- Deliverable: bridges for ConsultMemo / CommitteeVerdict / RedTeamMemo /
  explicit SeedProposal (raw DebateTranscript stays evidence-only) and
  Postmortem/Evolution -> Risk/Execution/Negative seeds.
- Acceptance: raw transcript cannot enter the seed queue; only memo/verdict/
  proposal can; `AgoraSeedExtractionRef` lineage recorded; trust handling per
  source (committee/red-team vs ordinary memo).
- Depends: IDS-002, IDS-003, IDS-007.

### IDS-006 — Seed-kind taxonomy + inbox integration
- Deliverable: extend the Seed Review Inbox (SEEDFLOW-002) seed card with
  `seed_kind`, source surface, negative-memory warnings, and convert-to-risk /
  convert-to-negative actions.
- Acceptance: a non-new-strategy interaction (e.g. a risk constraint) is reviewable
  as its own kind, not forced into "new strategy".
- Depends: IDS-004 or IDS-005 (something producing candidates), SEEDFLOW-002.

### IDS-008 — Audit + lineage + tests (cross-cutting)
- Deliverable: every step (record / redact / classify / negative-match / candidate
  / accept / reject) emits an audit event; end-to-end test from a sample Trainer
  commit and a sample committee memo through to a reviewable SeedCandidate.
- Acceptance: full replay/lineage from seed back to source event; privacy test
  asserts raw prompt never surfaces.
- Depends: all above.

---

## Suggested sequencing

1. IDS-001 (schema/store).
2. IDS-002 + IDS-003 + IDS-007 together (the guard) — **gate before any bridge**.
3. IDS-004 (Trainer) — internal source first.
4. IDS-005 (Agora/Committee/Postmortem).
5. IDS-006 (taxonomy/inbox) + IDS-008 (audit/e2e) to close.

## Explicit deferrals (product decisions, not in this EPIC)
- Trust tiers T0-T5 and seven-factor source trust scoring (addendum 2.1) —
  decide separately whether to adopt before building.
- Embedding-based similarity for IDS-003 / IDS-007 — v1 is deterministic.
- External-source connectors beyond what DATASTRAT already shipped.
