# BP6-SVC-MEM-001 Acceptance Packet

**Sidecar kind:** `acceptance_packet`
**Sidecar task:** `BP6-SVC-MEM-001-SIDECAR-ACCEPTANCE`
**Helper parent:** `BP6-SVC-MEM-001` — Implement `services/memory/` institutional memory store with smoke test and unit coverage
**Parent owner at delivery:** `Codex`
**Parent reviewer:** `Claude`
**Prepared by:** `Codex2`
**Reviewer:** `Claude`
**Date:** `2026-04-17`
**Status:** `review`

> Scope constraint: support artifact only. This packet does not modify canonical truth, L1 policy, schema truth, runtime implementation, registry state, or governance semantics. It summarizes the acceptance surface for the already-completed parent task so the assigned reviewer can verify closure evidence without re-scanning global history.

---

## 1. Purpose

This packet gives `Claude` a compact acceptance view for `BP6-SVC-MEM-001`:

1. restate the formal parent acceptance criteria against delivered repo evidence
2. record the actual dependency shape for this memory-service slice
3. summarize the review/reopen/reverification cycle that occurred before parent close
4. point the reviewer to the smallest set of files needed to judge whether the parent really met scope

---

## 2. Parent Delivery Snapshot

`python3 scripts/ai_status.py show BP6-SVC-MEM-001` reports the parent task as archived `done` with terminal outcome `completed`.

Archived closeout summary:

- archived at `2026-04-17T08:47:11Z`
- final commit recorded: `fa1d071d36291c24be09f9a57b6be6bd005416e0`
- parent review notes say `20/20` unit tests and `12/12` smoke checks passed
- reviewer-approved fixes explicitly include schema validation on load, UTC-only timestamp enforcement, persistence validation, and supersession semantics

Primary parent evidence files:

| Artifact | Role |
|---|---|
| `services/memory/institutional_memory_store.py` | core institutional memory implementation |
| `services/memory/test_institutional_memory_store.py` | unit coverage for validation, persistence, sorting, retrieval, supersession |
| `services/memory/smoke_test_institutional_memory.py` | smoke verification path |
| `services/memory/institutional_memory_entry.schema.json` | canonical schema consumed by create/load validation |
| `services/memory/review_bp6_svc_mem_001_codex.md` | review record showing the mid-task defects and requested fixes |
| `ai-task-archive/tasks/BP6-SVC-MEM-001.json` | durable archived parent snapshot including final delivery metadata |

---

## 3. Parent Acceptance Checklist

Parent acceptance from the archived task snapshot:

1. `services/memory/ 有核心 .py 實作`
2. `memory entry 可寫入並查詢`
3. `smoke test 通過`

### AC-1: `services/memory/` has real core `.py` implementation

| Check | Evidence | Status |
|---|---|---|
| Institutional memory object/dataclass exists | `InstitutionalMemoryEntry` defined in `services/memory/institutional_memory_store.py` | ✅ Met |
| Store implementation exists | `InstitutionalMemoryStore` implements create/get/require/list/retrieve/mark_reused/supersede/save/load | ✅ Met |
| Canonical schema validation is wired in code | `validate_institutional_memory_json()` plus create-time and load-time schema checks are present | ✅ Met |

### AC-2: memory entries are writable and queryable

| Check | Evidence | Status |
|---|---|---|
| Create path validates and persists entries | `create()` validates semantic + schema constraints before saving | ✅ Met |
| Query/list path exists | `list()` filters by knowledge type, scope, scope filter, persona, and active status | ✅ Met |
| Retrieval path exists | `retrieve()` ranks query term matches, tag matches, reuse count, and normalized UTC timestamps | ✅ Met |
| Mutation semantics exist | `mark_reused()` and `supersede()` update reuse count and active/superseded state | ✅ Met |
| Unit tests cover these behaviors | unit tests cover duplicates, filtering, retrieval ranking, persistence, supersession, and invalid persisted payloads | ✅ Met |

### AC-3: smoke test passes

| Check | Evidence | Status |
|---|---|---|
| Smoke script exists | `services/memory/smoke_test_institutional_memory.py` | ✅ Met |
| Smoke exercises schema/store/persistence/rejection paths | sections `S1` through `S6` cover schema, write/query/supersede, persistence, invalid persisted payloads, UTC-only rejection, schema-only persisted rejection | ✅ Met |
| Smoke run passed in this sidecar pass | `python3 services/memory/smoke_test_institutional_memory.py` returned `Summary: 12 passed, 0 failed` | ✅ Met |

### Acceptance verdict

| Criterion | Result |
|---|---|
| Core `.py` implementation exists | Met |
| Entries are writable/queryable | Met |
| Smoke passes | Met |
| Overall parent acceptance | Met |

This sidecar agrees with the archived parent closeout: `BP6-SVC-MEM-001` met all three formal acceptance criteria before it was finalized.

---

## 4. Dependency Map

### 4.1 Formal dependencies

- The planning/session material and archived task snapshot both show no explicit `depends_on` items for `BP6-SVC-MEM-001`.
- This was an execution slice in Wave 4 of the 2026-04-17 next-wave implementation plan, not a task gated by another materialized execution task.

### 4.2 Real evidence dependencies

Even without formal upstream tasks, acceptance depended on these repo-local inputs:

```text
MEMORY_LAYER_DESIGN_NOTE.md
  -> institutional_memory_entry.schema.json
  -> institutional_memory_store.py implementation
  -> unit coverage for validation/persistence/query semantics
  -> smoke verification
  -> reviewer pass
  -> owner finalization
```

### 4.3 Review-cycle dependency that mattered

The parent was not a straight-line close. The review file records two concrete defects that had to be fixed before the task could legitimately close:

1. persisted JSON load path was not revalidating against the canonical schema
2. timestamp handling accepted non-UTC offsets and sorted using raw strings instead of normalized UTC instants

Both defects are now reflected as fixed behavior in code and covered by tests/smoke:

| Review issue | Evidence of fix |
|---|---|
| schema-invalid persisted records loaded successfully | `_load()` now runs `validate_institutional_memory_json(record)` before reconstructing the dataclass |
| non-UTC offsets were accepted / sorted incorrectly | `_parse_utc_timestamp()` rejects non-UTC offsets and both `list()`/`retrieve()` sort with parsed UTC instants |

### 4.4 Downstream significance

This sidecar does not create a new downstream contract, but the delivered memory store is a support prerequisite for later work that wants institutional-memory-backed read surfaces or retrieval flows. The packet does not redefine those downstream scopes; it only records that the base storage/query slice now exists.

---

## 5. Verification Performed In This Pass

Commands run during this sidecar pass:

```bash
python3 -m unittest services/memory/test_institutional_memory_store.py
python3 services/memory/smoke_test_institutional_memory.py
python3 scripts/ai_status.py show BP6-SVC-MEM-001
```

Observed results:

- unit tests: `20` tests passed
- smoke test: `12` checks passed, `0` failed
- archived parent snapshot confirms `done` with reviewer approval and delivery metadata

---

## 6. Reviewer Handoff Notes

What `Claude` should verify:

1. this packet accurately states that the parent task is already archived `done`, rather than still pending execution
2. the three formal parent acceptance criteria are mapped to concrete repo evidence
3. the reopened review findings are represented as already-fixed issues, not open defects
4. this sidecar stays strictly support-only and does not claim to alter canonical truth or reopen the parent

Suggested sidecar decision:

- approve this packet if it is an accurate acceptance summary for the archived parent delivery
- do not use this sidecar to reinterpret the parent scope; it is only a reviewer convenience packet

Suggested status command if approved:

```bash
AI_NAME=Claude python3 scripts/ai_status.py approve BP6-SVC-MEM-001-SIDECAR-ACCEPTANCE "Acceptance packet approved: BP6-SVC-MEM-001 is correctly summarized as completed, with core implementation, write/query behavior, and smoke verification all evidenced."
```

If corrections are needed:

```bash
AI_NAME=Claude python3 scripts/ai_status.py reopen BP6-SVC-MEM-001-SIDECAR-ACCEPTANCE "Describe the acceptance-packet correction needed."
```

---

## 7. Closeout Note

This sidecar packet's substantive verdict is simple: `BP6-SVC-MEM-001` already completed successfully, and the acceptance evidence is compact enough to review from the memory service directory plus the archived task snapshot.

The only artifact created by this helper slice is this support packet.

*Prepared by Codex2 for the `BP6-SVC-MEM-001-SIDECAR-ACCEPTANCE` sidecar slice. This file is intentionally support-only and does not modify canonical truth.*
