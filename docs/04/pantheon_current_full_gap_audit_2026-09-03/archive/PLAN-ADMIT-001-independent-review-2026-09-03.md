# PLAN-ADMIT-001 Independent Review Evidence

Status: closeout evidence for the documentation-only admission of the
`pkt-pantheon-structural-closure-functional-v2-20260903` planning package.

- Task: `PLAN-ADMIT-001`
- Owner: Claude
- Reviewer: Antigravity
- Source packet: `.orchestrator/assistant-dev-packets/sources/pkt-pantheon-structural-closure-functional-v2-20260903`
- Baseline: `pantheon` `origin/dev@675a488d78e8f991e2f1ecfc92e595b2d84625a1`
- Merge target: `docs/04/pantheon_current_full_gap_audit_2026-09-03/**`
- Scope: documentation admission only. No product runtime code is changed by
  this task.

## 1. Digest verification

`sha256sum` recomputed for every packet document and compared byte-for-byte
against the `sourceRefs` recorded in the canonical `dev_bridge.documents`
binding for this task (via
`"$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh" show PLAN-ADMIT-001`):

| File | Recorded sha256 | Recomputed sha256 | Match |
|---|---|---|---|
| INDEX.md | `3959c4515f1b83790fa70fad637e31ab370b2a9509acf5d0845860e26055fc7a` | same | yes |
| REPORT.md | `75709d429e012868941afe7d1e37a067cfeafa8bc49ea13bcf7250c718d5ecba` | same | yes |
| SA.md | `8b9b3d06a48f383e6b4b3330c28869cbdd6f7f08719d183a212c76ccb645160a` | same | yes |
| SD.md | `ef6f664aceffcb3a594517ec5027ed74927a775a5bad7a4b76479d8cb454f567` | same | yes |
| TRACEABILITY.md | `76771001f31450325fc8c41bae5d94dbdcbc3562c42013edff434824e2f6e2dd` | same | yes |
| EXECUTION_TASKS.md | `bb7478844c3659e89a23f520db6b3360eb36ad1cf20b24f636434b72919c452c` | same | yes |
| tasks.json | `c21730622c671e4ee6c367e0d6e34773bc73342fe9f9ae73e57e50825f314fed` | same | yes |

The recorded `pantheon-origin-dev@675a488d78e8f991e2f1ecfc92e595b2d84625a1`
baseline matches the current `dev` tip at task admission time.

The files committed under
`docs/04/pantheon_current_full_gap_audit_2026-09-03/` in this PR are
byte-identical (`diff -q`, no output) to the source snapshot above, so the
merged copies carry the same digests.

## 2. Independent-reviewer challenge

An independent read-only pass (separate agent context, no access to the
digest-verification result above) fact-checked `REPORT.md` findings against
the actual `origin/dev@675a488d7` tree via `git archive`, checked `SA.md`
canonical-ownership invariants and the "rejected layering designs" (ADR)
section for weak rationale, cross-checked `SD.md` migration waves/deletion
requirements against `REPORT.md`, and cross-checked
`TRACEABILITY.md` / `EXECUTION_TASKS.md` / `tasks.json` for dangling
references, dependency-graph cycles, and owner/reviewer collisions.

**Verdict: PASS -- safe to admit as documentation-only planning evidence.**

Sampled `REPORT.md` findings independently reproduced against the baseline
tree (not fabricated or stale):

- `services/control-plane/bff/main.py` line count and absence of top-level
  route decorators (REPORT SS7.4/1.1).
- `ReadSurfacePorts` missing `create_runtime_binding` /
  `record_agora_audit_event` / `record_sponsor_decision` while `main.py`
  calls them directly (GAP-MGMT-03A).
- Duplicate Decision Journal implementations with no live caller of the
  governance one (GAP-AGORA-04 / DUP-01).
- All 8 unreachable-tail functions in `management_ai_store.py` reproduced by
  independent AST scan (REPORT SS7.5A).

`SA.md` SS12 rejected designs were each traced to a specific `REPORT.md`
finding; no weak rejection rationale found.

### Findings recorded, not blocking

These are follow-up notes for `STRUCT-OWNERSHIP-001` and downstream
dispatch, not defects in this documentation admission (this task's own
`artifacts` scope is `docs/04/pantheon_current_full_gap_audit_2026-09-03/**`
only, and every runtime-mutating task in `tasks.json` is downstream of
`PLAN-ADMIT-001` via `depends_on` and separately gated):

1. **Dependency-graph prose/data mismatch.** `EXECUTION_TASKS.md`'s ASCII
   dependency graph implies an edge
   `ENV-STAGING-PROD-PLAN-001 -> BFF-PACKAGE-001`; `tasks.json`'s
   `depends_on` for `BFF-PACKAGE-001` is `["STRUCT-OWNERSHIP-001"]` only, and
   `ENV-STAGING-PROD-PLAN-001` has no downstream consumer in `tasks.json`.
2. **Dangling traceability reference.** `TRACEABILITY.md`'s "stale superseded
   PRs" row cites a "Wave-0 delivery hygiene" packet that does not match any
   task ID in `EXECUTION_TASKS.md` / `tasks.json`.
3. **Wave-grouping mismatch.** `SD.md` SS11 groups `BFF-ROUTER-STRUCT-001`,
   `DOMAIN-WRITERS-001`, `JOURNAL-OWNER-001`, `OVERLAY-RETIRE-001` as
   concurrency-eligible Wave 2, while `tasks.json` gives each task its own
   serialized wave number chained by `depends_on`. The `tasks.json` encoding
   is strictly more conservative (serialized, not concurrent), so this is a
   spec inconsistency to reconcile before treating the wave graph as
   authoritative, not a safety defect.
4. No cycles found in the `tasks.json` dependency graph; all referenced task
   IDs exist; no task has `owner == reviewer`.

### Runtime-change risk

None found for this task. `PLAN-ADMIT-001`'s declared `artifacts` are scoped
to `docs/04/pantheon_current_full_gap_audit_2026-09-03/**` only. Every
mutation-bearing task in the packet (`BFF-*`, `DOMAIN-WRITERS-001`, etc.) is
downstream of `PLAN-ADMIT-001` via `depends_on` and is dispatched
separately; hosted/mutating tasks are tagged `work_class: "hosted"` and
gated on their own one-shot authorization per `EXECUTION_TASKS.md`'s
dispatch rule.

## 3. Rollback

Per the task's `summary_zh` rollback note: revert this documentation-only
merge commit if review discovers a material planning defect. No product
runtime state needs to be rolled back because none was changed.
