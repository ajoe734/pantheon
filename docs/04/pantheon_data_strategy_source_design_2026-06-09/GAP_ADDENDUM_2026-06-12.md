# DATASTRAT Strategy-Source — Gap Addendum (code-verified)

Generated: 2026-06-12
Status: planning addendum (verification of delivered state)
Verified against: `origin/dev` @ `0d9fe586` (read-only worktree audit)
Supersedes / consolidates: the 2026-05-20 and 2026-05-23 Strategy Seed Source
Registry SA/SD discussion drafts (chat artifacts, never committed). Those drafts
are now **superseded by** `DATA_STRATEGY_SOURCE_SYSTEM_DESIGN.md` (2026-06-09)
plus this addendum. Do not re-dispatch work directly from the May drafts.

---

## 0. Why this document exists

The May 2026 SA/SD drafts envisioned a single large "Strategy Seed Source
Registry". The 2026-06-09 design deliberately narrowed that into three planes
(Data Source Management / Strategy Seed Source Management / Persona Strategy
Discovery) and shipped them as EPIC DATASTRAT (7 tasks, all `done`/merged). This
addendum records **what actually landed in code**, **what the May vision still
leaves open**, and **the risks of building the open half in the wrong order**.

Every "delivered" claim below was confirmed by reading the merged modules on
`dev`, not from task status alone.

---

## 1. Delivered (verified in code)

| Capability | Where | Governance enforced in code? |
|---|---|---|
| Data-source vs strategy-seed-source **hard split** | `registry/data_source_registry.py`, `strategy_seed_source_registry.py`, `connector_projection.py` | YES — `source_kind` frozen + validated on deserialize; each `SourceType` projects to exactly one `RegistryRole`; cross-role requires explicit `force_role` + note |
| Registry split + JSONL dev store + lifecycle | + `registry/jsonl_store.py` | YES — lifecycle `candidate/enabled/degraded/disabled/retired`; `is_ingestable` only for enabled/degraded |
| `StrategySpecSeed` store + materializer | `strategy_seed_store.py`, `seed_materializer.py`, `strategy_seed_builder.py` | PARTIAL — converts `EvidenceBundle -> StrategySpecSeed -> store`, idempotent, but **library/API-only, no auto-trigger** |
| Persona strategy discovery | `services/control-plane/persona/persona_strategy_discovery.py` (~1300 LOC) | YES — **deterministic 8-factor scorer** (no embedding), hard blockers, advisory actions |
| LLM source-change governance | `registry/proposals.py`, `llm_proposal_adapter.py` | YES — adapter emits `draft` only; `draft->submitted->approved->applied` operator-gated |
| Source health / usage / retirement | `source_health.py`, `retirement_engine.py` | YES — recommendations-only (`propose_disable/replace/retire`); dependency-blocking; no auto-retire |
| Contracts | `docs/contracts/{data_source_registry_entry,strategy_spec_seed,persona_strategy_match,source_change_proposal}.schema.json` | YES — `const source_kind`, `research_only=true`, `execution_route=none`, `registry_write_performed=false` |

### 1.1 Blueprint invariants confirmed live in code (not just prose)

- **No direct execution from any source.** `strategy_seed_source_registry`
  rejects `metadata.execution_route != none`; seed + match schemas pin
  `research_only=true` / `execution_route=none`; persona discovery hard-blocks
  any candidate that `requires_execution_route_during_discovery`.
- **LLM may propose, not mutate.** `llm_proposal_adapter` only produces `draft`.
- **Retirement is human-gated.** `retirement_engine` emits draft recommendations,
  blocked by active dependencies.

### 1.2 Persona discovery detail (verified)

`PersonaStrategyDiscoveryService._score_candidate` scores both
`strategy_spec_seed` and `strategy_spec` candidates on 8 deterministic factors
(strategy_family 0-20, market+asset 0-15, holding_period 0-10, required_data
0-15, evidence_quality 0-15, backend_compat 0-10, risk 0-10, novelty 0-5),
applies hard blockers (license / source-status / execution-route-during-
discovery), then emits an **advisory** `RecommendedAction`
(`promote_seed_candidate` / `run_rapid_eval` / `create_research_ticket` /
`request_data_backfill` / `ignore`). Exposed via
`POST /bff/personas/{id}/strategy-discovery` (202),
`GET /bff/personas/{id}/strategy-matches`,
`POST /bff/personas/{id}/strategy-matches/{match_id}/actions` (202, read-role,
"research-only action").

---

## 2. Gaps (verified — not assumed)

| # | Gap | Severity | Evidence |
|---|---|---|---|
| 1 | **Seed -> Replication / ExperimentTask bridge does not exist** — pipeline dead-ends at the seed store | HIGH | seed code has zero references to `replication_queue` / `ExperimentTask` / `research_orchestrator`; `conversion.py` docstring: "does not write registry state, launch experiments, or create execution routes" |
| 2 | **No Seed Review Queue / Strategy Seed Inbox** | HIGH | seed status enum is only `draft/promoted_to_strategy_spec/rejected`; no accept/merge/quarantine actions; no review API or inbox read model |
| 3 | **Interaction-derived seeds (entire IDS half) not started** | MEDIUM | no Trainer->seed, Agora/Committee/Postmortem->seed bridges; no `InteractionSourceRecord`; no `IntentClassification` |
| 4 | **Redaction/visibility guard absent at seed boundary; Negative-Memory Matcher absent** | MEDIUM (safety prerequisite for #3) | seed path has no redaction; no retired/failed-strategy similarity match |
| 5 | Persona advisory action is unconsumed | LOW (ties to #1) | `promote_seed_candidate` is a string; nothing advances the pipeline from it |
| 6 | All stores are JSONL dev-stores; Postgres only in docstrings | LOW | `jsonl_store.py` self-documents non-thread-safe |

### 2.1 May-vision items that are product decisions, not bugs

- **Trust tiers T0-T5**: not in code (only lifecycle states + `license_scope` /
  `source_scope`). Decide whether the tier model is still wanted.
- **Seven-factor source trust scoring**: not present. (The 8-factor scorer is
  *persona-match* scoring, a different thing from *source* trust scoring.)

---

## 3. Risks

- **Ordering risk (highest).** Building gap #3 (interaction-derived seeds) before
  gap #4 (redaction / visibility / intent-classification / negative-memory) would
  violate the blueprint rule that raw transcript is evidence-only and that
  redaction failure blocks seed creation. That path currently has **no guard**.
  #4 must precede or ship with #3.
- **Value-realization risk.** Until #1 + #2 ship, the seven delivered tasks
  cannot pay off — seeds are produced but cannot move or be reviewed.
- **Durability.** Everything is JSONL dev-store; not production-durable and not
  concurrency-safe. Acceptable for now; flag before any production cutover.

---

## 4. Recommended sequencing

1. **Close out the backbone (highest ROI):** Seed->Replication bridge (#1) +
   Seed Review Inbox (#2, consumes #5). Dispatchable briefs in
   `DISPATCH_SEED_PIPELINE_CLOSEOUT.md`.
2. **Interaction-derived EPIC with safety-first ordering:** #3 + #4 as one
   EPIC, redaction/intent/negative-memory shipped with the Trainer/Agora bridges,
   internal sources first. Breakdown in `EPIC_INTERACTION_DERIVED_SEEDS.md`.
3. **Doc hygiene:** keep the May drafts marked superseded (this file); revisit
   trust-tier / source-scoring as explicit product decisions.
