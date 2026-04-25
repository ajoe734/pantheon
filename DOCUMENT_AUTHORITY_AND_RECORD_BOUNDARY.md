# Document Authority And Record Boundary

Last updated: 2026-04-18
Status: canonical rules for separating blueprint truth from planning and execution records
Tier: L2 Planning & Execution
Scope: document mutability, authority boundaries, and the difference between canonical planning documents and implementation-generated records
Conflict rule: this file defines document-governance boundaries and mutability rules, but it does not override L1 platform policy or machine-readable state truth

## 1. Core Principle

Pantheon must keep two categories separate:

1. blueprint and canonical planning documents
2. planning, implementation, review, and execution records

Execution history may inform future planning, but it must not silently rewrite blueprint truth.

## 2. Immutable Or Deliberate-Change Documents

These files define stable truth and should change only through deliberate architecture or planning decisions:

- `TARGET_ARCHITECTURE.md`
- all L1 policy documents
- `CANONICAL_DOCUMENT_MAP.md`
- `ROADMAP.md`
- `DEVELOPMENT_WORKBREAKDOWN.md`
- `WORKBENCH_DELIVERY_BACKLOG.md`
- `DELIVERY_CLOSURE_AND_LOOP_STATES.md`
- `EXECUTION_PROOF_AND_MATURITY_LEVELS.md`
- `OSS_INTEGRATION_CHECKLIST.md`
- this file

Changing one of these files means "the intended system truth changed", not merely "implementation work happened".

## 3. Working Records And Execution Artifacts

These files are records, not blueprint truth:

- `ai-status.json`
- `ai-activity-log.jsonl`
- `current-work.md`
- `.coordination/**`
- `docs/reviews/**`
- `docs/02-architecture/consensus/sessions/**`
- deployment result writeups, smoke logs, and acceptance snapshots

These files may describe:

- what happened
- what is blocked
- what a specific planning round proposed
- what a frontend loop returned
- what was verified in one environment

They do not become canonical blueprint authority merely by existing.

## 4. Mutability Rules

Use this rule set:

1. blueprint files change only when intent, policy, or canonical backlog truth changes
2. records change to append, capture, or close out real work
3. records may cite blueprint documents, but they do not redefine them
4. if execution reveals blueprint drift, update the blueprint explicitly and say that the blueprint changed
5. do not smuggle execution conclusions into canonical truth by placing review or session artifacts inside the canonical file list

## 5. Active Planning Sessions

Planning sessions are working records, not immutable blueprint.

The active planning authority is:

- the session named by `.orchestrator/planning-state.json`

All other session directories are historical records unless explicitly reactivated.

Even the active planning session is still a working record. It can guide execution for the current round, but it does not replace canonical blueprint documents.

## 6. Practical Classification

When deciding where a fact belongs:

- if it describes durable system intent, put it in blueprint or canonical planning docs
- if it describes one planning round, one review loop, one experiment, one acceptance run, or one implementation outcome, put it in a record
- if it is machine-readable state that coordinates current work, keep it in `ai-status.json` or other runtime state files
- if it is only a human-readable rendering of machine state, keep it derived

## 7. Current Repo Implication

The repo should not treat the following as canonical blueprint files:

- `docs/reviews/*.md`
- `docs/02-architecture/consensus/sessions/*`
- one-off gap analyses
- frontend return packets

Those materials are important, but they belong to the record layer.
