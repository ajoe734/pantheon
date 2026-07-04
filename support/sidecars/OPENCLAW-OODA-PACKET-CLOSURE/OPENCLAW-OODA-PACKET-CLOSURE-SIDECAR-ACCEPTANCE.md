# OPENCLAW-OODA-PACKET-CLOSURE Acceptance and Dependency Map (Sidecar)

**Parent Task**: `OPENCLAW-OODA-PACKET-CLOSURE` — Close cron-turn -> persisted OODA packet loop
**Parent Owner**: `Claude2`
**Parent Reviewer**: `Codex`
**Parent Status**: `in_progress` (`needs_design_decision: true`)
**Sidecar Task**: `OPENCLAW-OODA-PACKET-CLOSURE-SIDECAR-ACCEPTANCE`
**Sidecar Owner**: `Claude`
**Sidecar Reviewer**: `Claude2`
**Helper Kind**: `acceptance_packet`
**Generated**: `2026-07-04`
**Mutates canonical**: `no`

> Scope constraint: support artifact only. This packet does not modify L1
> canonical truth, the `OodaLoopPacket` contract, the OpenClaw runtime
> contract, `services/persona/ooda_cycle_runtime.py`,
> `integrations/openclaw/adapter/cron_transport.py`,
> `services/control-plane/cron/persona_cron_registrar.py`, BFF routes, or any
> other implementation surface. It only packages a reviewer-facing acceptance
> checklist and dependency map for the parent's design-decision-then-implement
> work, based on the current state of the repo confirmed by reading the
> referenced files directly.

## 1. Executive Summary

`OPENCLAW-OODA-PACKET-CLOSURE` closes a real design gap, not a bug: the cron
system correctly wakes a persona's OpenClaw agent session on schedule
(`cron.add` -> `cron.run` force -> polls `cron.runs` for a terminal `status`),
but nothing on the Pantheon side turns that terminal cron run into a persisted
`OodaLoopPacket`. The two existing OODA-packet writers in this repo are both
disconnected from the cron path:

1. `services/persona/ooda_cycle_runtime.py` — a batch/backtest generator that
   emits closed packets from a fixed `AlphaSeedSource` table
   (`run_management_persona_ooda_cycles`). It is invoked as a standalone batch
   run, not from any cron/webhook trigger, and its evidence is a static seed
   table, not live cron-run output.
2. `services/control-plane/ooda/persona_ooda_bootstrap.py` — opens exactly one
   `persona_synthesis` packet at persona-creation time
   (`bootstrap_persona_ooda_packet`) and never advances or closes it. It fires
   once per persona, not once per cron turn.

Neither path reads `cron.runs` output, a run id, or a trace id, so a real
`cron.run(..., mode="force")` -> `status: ok` cycle today produces **zero**
new `/bff/ooda/packets` entries. This matches the parent's own summary
verbatim and this sidecar independently confirmed it by reading the three
named artifacts rather than only trusting the task description.

The parent brief is explicit that a static-seed or read-time-synthesized
packet must not be used to fake cron-produced evidence, and that a design
decision must precede implementation. This packet's job is to give the
parent's reviewer (`Codex`) a checklist to hold the eventual implementation
against, and a dependency map showing what is now unblocked versus what is
still open, without pre-deciding the design question itself.

## 2. Source References

| Source | Why it matters |
|---|---|
| `.orchestrator/task-briefs/openclaw_ooda_packet_closure_sidecar_acceptance.md` | This sidecar's own task brief; confirms support-only scope. |
| `ai_status.py show OPENCLAW-OODA-PACKET-CLOSURE` (live status root) | Canonical current parent state: `in_progress`, owner `Claude2`, reviewer `Codex`, `needs_design_decision: true`, `depends_on: [OPENCLAW-PERSONA-CRON-BACKFILL]`. |
| `ai_status.py show OPENCLAW-PERSONA-CRON-BACKFILL` (archive) | Confirms the sole listed dependency is now `done`, merged into `dev` at `ffa2c8b4c`. |
| `services/persona/ooda_cycle_runtime.py` | The static `AlphaSeedSource`/backtest-bank packet generator; confirmed to have no cron/webhook entry point. |
| `services/control-plane/ooda/persona_ooda_bootstrap.py` | The persona-creation-time packet opener; confirmed it never advances past `open`. |
| `integrations/openclaw/adapter/cron_transport.py` | The cron dispatch transport (`cron.add` / `cron.run` force / `cron.runs` poll); confirmed it returns run evidence (`job_id`, `run_id`, `latest_run`) but nothing in this file writes an OODA packet. |
| `services/control-plane/cron/persona_cron_registrar.py` + `workflows.py` | Confirms the four `WORKFLOW_CATALOG` entries (`research.ingest`, `governance.review`, `learning.retrain`, `deployment.plan`) carried as `upstream_entrypoint` metadata in the dispatched `systemEvent`, with no current handler that maps an entrypoint execution back to a packet. |
| `services/control-plane/ooda/contract.md` | `OodaLoopPacket` lifecycle contract (`open -> observing -> oriented -> decided -> acted -> evolving -> closed`) and the closed-packet evidence rule the eventual implementation must satisfy. |
| `services/control-plane/ooda/stage_transition.contract.md` | Stage-transition validation rules `advance()` must respect. |
| `services/control-plane/bff/main.py` (`/bff/ooda/packets`, `/bff/ooda/packets/{packet_id}`) | Confirms the read-side BFF routes the acceptance criterion "`/bff/ooda/packets` count +1" is measured against; both routes read via `read_store.list_ooda_packets` / equivalent, independent of how a packet was written. |
| `support/sidecars/OPENCLAW-PERSONA-CRON-BACKFILL/OPENCLAW-PERSONA-CRON-BACKFILL-SIDECAR-ACCEPTANCE-FOLLOWUP-5.md` | Prior sidecar packet that already scoped the `sessionTarget: main` gateway-normalization finding to this task; this packet does not re-litigate that finding. |

## 3. Repo-Current Truth Snapshot

| Truth item | Repo evidence | Implication for review |
|---|---|---|
| Cron correctly reaches a terminal run state | `cron_transport.py`'s `__call__` does `cron.add` -> `cron.run(mode=force)` -> polls `cron.runs` until a `_TERMINAL_RUN_STATUSES` status, and raises on non-`ok` terminal status. | The trigger side of the loop is solid; the gap is entirely on the packet-writeback side. |
| No code path connects a terminal cron run to an OODA packet write | Grep across `services/`, `integrations/openclaw/` for `writeback`/`write_back` and for any OpenClaw-side tool/handler wired to `OodaJsonlAppendStore` found no writeback tool; the only two writers (`ooda_cycle_runtime.py`, `persona_ooda_bootstrap.py`) are structurally unrelated to `cron_transport.py`. | Confirms the parent's own framing ("design gap, not bug") from independent code reading, not just the task description. |
| `ooda_cycle_runtime.py` is a static/batch generator | `ALPHA_SEED_SOURCES` is a fixed tuple; `run_management_persona_ooda_cycles` takes a `personas` list and a `store_path` and produces `CYCLES_PER_PERSONA` (15) closed packets per persona from `OODA_SCENARIOS`, with no cron run id, no trace id, and no caller in the cron/adapter path. | This module cannot be the closure mechanism as-is; any use of it (or its scenario/seed pattern) to fabricate a "cron produced this" packet would violate the parent's explicit "no static seed / no read-time synthesis" constraint. |
| `persona_ooda_bootstrap.py` only fires once per persona | `bootstrap_persona_ooda_packet` is invoked at persona-creation time, opens a single `persona_synthesis` packet, and never calls `advance()`/`_advance_packet` to move it past `open`. | Does not produce a new packet per cron turn, and does not close the loop it opens; separate concern from this task. |
| `upstream_entrypoint` is currently metadata-only | `workflows.py` defines four `WorkflowDefinition`s with `upstream_entrypoint` values (`research.ingest`, `governance.review`, `learning.retrain`, `deployment.plan`); `cron_transport.py` embeds this value in the dispatched `systemEvent` payload but nothing downstream branches on it to decide what kind of OODA activity happened. | Any "upstream_entrypoint-triggered workflow" design option (see §5) would need a new dispatch table mapping entrypoint -> stage/loop_type, since none exists today. |
| Read side is unaffected by the gap | `/bff/ooda/packets` and `/bff/ooda/packets/{packet_id}` in `services/control-plane/bff/main.py` read from the store layer regardless of what wrote the packet. | Acceptance criterion "count +1" is a real, checkable BFF-visible signal once a write path exists; no BFF change is implied by closing this gap. |
| Sole listed dependency is now satisfied | `OPENCLAW-PERSONA-CRON-BACKFILL` is archived `done`, PR #2985 merged into `dev` at `ffa2c8b4c` (confirmed both via the archived snapshot and via `git log` showing `ffa2c8b4c` on the current branch's ancestry). | This task's `depends_on` list has no remaining blocker; `needs_design_decision: true` is an internal-to-the-task gate, not an external dependency wait. |

## 4. Parent Acceptance Checklist

Restating the parent's own `acceptance` list from the live status root, with a
verification note for each item based on what currently exists in the repo.

| Acceptance target (verbatim from `ai-status.json`) | Current repo state | What "done" must show |
|---|---|---|
| Force-run a persona OODA cron job -> `/bff/ooda/packets` count +1 | Not yet possible: `cron_transport.py` returns run evidence but writes no packet. | A live `cron.run(mode=force)` invocation followed by a `/bff/ooda/packets` list call showing exactly one new entry attributable to that run. |
| New packet carries real producer fingerprint (cron `runId` / `trace_id` / upstream ts), not fixture/synthesized | No current writer carries a cron `run_id`; `ooda_cycle_runtime.py`'s fingerprints (`catalog_hash`, `alpha_seed_key`) are all seed-table-derived, not cron-derived. | The packet (or an `audit_refs`/`source_truth`-equivalent field) must reference the actual `job_id`/`run_id` from the `cron.runs` response that triggered it, or an equivalent trace id propagated from the OpenClaw session, not a value derived from a static table. |
| Evidence chain links the cron run to the new packet | No linkage exists today; the two systems (`cron_transport.py`, packet writers) do not share any id. | Should be independently traceable: given a `job_id`/`run_id` from `cron.runs`, a reviewer must be able to find the corresponding packet, and vice versa. |
| Existing tests green; add a live smoke proving cron->packet closure | Existing `services/control-plane/cron/` and `services/control-plane/ooda/` suites are unaffected by this gap (they test each side independently) and should stay green. | A new smoke test (not necessarily hitting a live external gateway, but exercising the actual code path chosen in the design decision) demonstrating the full cron-trigger-to-packet-persisted flow, distinct from the existing per-module unit suites. |

`needs_design_decision: true` gates all four items — none can be marked
accepted until the parent has chosen and implemented one of the mechanisms in
§5.

## 5. Design Decision Options (framed only — not decided by this sidecar)

The parent's own summary names three candidate mechanisms. This section lays
out what each implies structurally, based on what already exists in the repo,
so `Codex` has a consistent frame to evaluate whichever option `Claude2`
proposes. Choosing between them is explicitly the parent owner's job, not
this sidecar's.

| Option | What it would require | What already exists to build on | Open risk |
|---|---|---|---|
| **(a) Agent-side write-back tool**: the OpenClaw agent session itself calls a Pantheon-exposed tool/RPC at the end of its cron-triggered turn to persist the packet. | A new tool registered with the OpenClaw session (parallel to how `cron.add`/`cron.run` are gateway RPCs today) that the agent invokes with real turn evidence; a handler on the Pantheon side that validates and calls `OodaJsonlAppendStore`. | `OodaLoopPacket`/`OodaJsonlAppendStore` already validate and persist correctly; the gap is only the trigger-to-tool-call wiring and getting the agent to reliably call it every turn. | Depends on the agent actually invoking the tool every turn — a missed call silently produces no packet unless paired with the run-observer option (b) as a backstop. |
| **(b) Pantheon-side observer of `cron.runs`**: a Pantheon-owned poller/watcher (or a callback fired after `cron_transport.py`'s existing `_wait_for_terminal_run`) reads the terminal run result and synthesizes the packet from run metadata + whatever session output is available. | Either extends `cron_transport.py`'s `__call__` (it already has the terminal `latest_run` in hand) to call a packet-writer, or a separate poller reading `cron.runs` independently. | `cron_transport.py` already computes and returns `latest_run`/`runs_response` with real `job_id`/`run_id`; this is the smallest structural change since the run evidence is already in scope at the point `_wait_for_terminal_run` returns. | Must avoid degrading into a static/synthesized packet — the run's actual session output (not a canned scenario) must supply the observe/orient/decide/act/learn evidence, or this option risks violating the "no read-time synthesis" constraint. |
| **(c) `upstream_entrypoint`-triggered workflow**: dispatch a distinct Pantheon-side workflow per `upstream_entrypoint` value (`research.ingest`, `governance.review`, `learning.retrain`, `deployment.plan`) that independently performs its OODA stage and writes the packet, keyed off the entrypoint rather than the raw cron run. | A new entrypoint -> handler dispatch table; each handler presumably maps to one OODA stage/loop_type given the four entrypoints roughly track observe/decide/learn/act framing. | `WorkflowDefinition.upstream_entrypoint` values already exist and are already threaded through to the dispatched `systemEvent`; no dispatch-on-entrypoint code exists yet. | Four entrypoints do not obviously map 1:1 to a single closed 5-stage loop per cron turn; the parent would need to define whether one entrypoint closes a full loop, advances one stage of a longer-lived loop, or something else — this is likely the most design-heavy of the three options. |

This sidecar does not recommend one option over another. Whichever the
parent chooses, §4's four acceptance items apply unchanged.

## 6. Dependency Map

### 6.1 Upstream Dependencies

| Dependency | Where recorded | Status | Relevance |
|---|---|---|---|
| `OPENCLAW-PERSONA-CRON-BACKFILL` | `ai-task-archive/tasks/OPENCLAW-PERSONA-CRON-BACKFILL.json` | `done`, merged to `dev` at `ffa2c8b4c` | Was the parent's only listed `depends_on`; guarantees the 17-persona x 4-workflow cron inventory is real and stable before packet-closure work measures cron-triggered writes against it. Already unblocked — no action needed. |
| `OPENCLAW-CRON-WRITE-SCOPE` | Referenced transitively via `OPENCLAW-PERSONA-CRON-BACKFILL`'s own `depends_on` | `done` (archived) | Established the cron write-scope this task's cron transport relies on; no new pressure on this task. |
| `sessionTarget: main` gateway-normalization finding | `OPENCLAW-PERSONA-CRON-BACKFILL`'s evidence and its `SIDECAR-ACCEPTANCE-FOLLOWUP-5` packet | Explicitly scoped to this parent, not resolved by the backfill task | Confirms this task, not its dependency, owns deciding whether/how `sessionTarget` normalization affects which session a write-back handler would need to target. |

### 6.2 Structural Dependencies (same-repo, not task-board `depends_on`)

| Artifact | Current role | Relationship to this task |
|---|---|---|
| `services/control-plane/ooda/ooda_loop_packet.py` + `contract.md` | Defines the packet schema, stage lifecycle, and closed-packet evidence rule. | Whatever mechanism the parent picks must produce packets that pass `validate_packet()`/`validate()` and the closed-packet evidence rule unchanged — this task should not need to modify the contract itself. |
| `services/control-plane/ooda/jsonl_store.py` (`OodaJsonlAppendStore`) | Durable append/replay store already used by both existing writers. | Confirmed reusable as the persistence layer for whichever design option is chosen; no store-layer change implied by this gap. |
| `integrations/openclaw/adapter/cron_transport.py` | Owns the trigger + terminal-run-evidence collection. | Most likely integration point for option (b); already holds the real `job_id`/`run_id` at the moment a packet-writeback call would need to happen. |
| `services/control-plane/cron/persona_cron_registrar.py` + `workflows.py` | Owns the `WORKFLOW_CATALOG` / `upstream_entrypoint` definitions. | Relevant to option (c); no dispatch-by-entrypoint code exists yet, so this would be new code, not a modification of existing dispatch logic. |
| `services/persona/ooda_cycle_runtime.py` | Existing static/batch packet generator. | Explicitly **not** an acceptable closure mechanism per the parent's own no-synthesis constraint; listed here only because it is one of the parent's declared artifacts and reviewers should not mistake it for the fix. |
| `services/control-plane/bff/main.py` (`/bff/ooda/packets*`) | Read-side consumer of whatever the parent writes. | Downstream; acceptance is measured through these routes but they should need no code change themselves. |

### 6.3 Downstream Consumers

| Consumer | Current state | Relationship to the parent task |
|---|---|---|
| Management Control Room OODA cards/drawer (`MGMT-OODA-005`/`006`, delivered) | Already reads `/bff/ooda/packets*` | Will start reflecting real cron-triggered loops once this task closes the gap; no changes needed there for this task to land. |
| `MGMT-OODA-004` BFF read routes (delivered) | Already exposes the four `/bff/ooda/*` routes used in §3/§4 | Confirms the acceptance criterion "`/bff/ooda/packets` count +1" is measurable today without further BFF work. |
| Future evolution/postmortem follow-through work referencing `learn.*` refs | Not yet started | Depends on this task producing packets with real, not fixture, `learn` bundle evidence once loops close. |

### 6.4 Machine vs. Semantic Dependency Note

`ai-status.json`'s `depends_on` for `OPENCLAW-OODA-PACKET-CLOSURE` lists only
`OPENCLAW-PERSONA-CRON-BACKFILL`, which is now satisfied. Everything in §6.2
is a semantic/structural dependency inferred from reading the code, not a
task-board dependency edge — this section is a review aid, not a request to
mutate `depends_on`.

## 7. Scope Boundary — What Reviewer Should Reject

| Problematic move | Why it is wrong |
|---|---|
| Accepting a fix that reuses `ooda_cycle_runtime.py`'s static `AlphaSeedSource`/scenario pattern to "produce" a packet on cron trigger | That table is fixed content, not live cron-run evidence; using it (even indirectly) to fabricate the appearance of a cron-triggered packet is exactly the "static seed" shortcut the parent brief forbids. |
| Accepting a fix where the packet's evidence is synthesized at BFF read-time from generic cron-run metadata rather than persisted at write-time from the actual turn | The brief also forbids "read-time synthesis"; the closed-packet evidence rule in `contract.md` expects real bundle refs recorded when the loop closes, not backfilled on query. |
| Treating `persona_ooda_bootstrap.py`'s persona-creation packet as if it already satisfies "force-run a cron job -> count +1" | That packet is opened once per persona at creation time, unrelated to any specific cron run, and is never advanced/closed by that module — it cannot stand in for the cron-turn acceptance criterion. |
| Reopening the `sessionTarget: main` gateway-normalization question as if it were unresolved by `OPENCLAW-PERSONA-CRON-BACKFILL` | That finding was already independently verified and explicitly handed to this task by the backfill's own evidence and sidecar; this task inherits it as context, not as new open work to redo. |
| Using this sidecar to bless one of the three design options in §5 | This sidecar deliberately does not pick a design option; that decision belongs to `Claude2` as parent owner, reviewed by `Codex`. |
| Rejecting this sidecar because the parent itself is still `in_progress`/undecided | This sidecar's job is to prepare the acceptance frame in parallel, not to wait for or replace the parent's design decision. |

## 8. Non-Claims

This packet does not claim:

| Non-claim | Correct owner / proof |
|---|---|
| The design decision (option a/b/c in §5, or another) has been made | `Claude2` (parent owner), reviewed by `Codex` |
| Any of `ooda_cycle_runtime.py`, `cron_transport.py`, `persona_cron_registrar.py`, or BFF routes have been modified | Confirmed unchanged by this sidecar; only this support file was added |
| A live cron-triggered packet write has been demonstrated | Remains open parent work; §4 defines what evidence would satisfy it |
| The `sessionTarget: main` gateway-normalization root cause has been resolved | Explicitly still owned by this task per prior sidecar handoff; not claimed resolved here |
| `OPENCLAW-PERSONA-CRON-BACKFILL`'s 4 orphan `persona-diag-local-4` cron jobs are in scope for this task | Out of scope per that task's own closeout; unrelated to packet-closure work |
| Any L1 policy, `OodaLoopPacket` contract, runtime code, registry, or governance behavior changed | Out of scope for this sidecar |

## 9. Handoff

**To:** `Claude2`
**From:** `Claude`
**Requested review outcome:** Approve this sidecar if it accurately reflects
the current cron-to-packet gap, correctly frames the three design options
without prejudging them, and does not overclaim dependency or acceptance
state.

Recommended reviewer focus:

1. Confirm §3's repo-current-truth snapshot still matches the working tree at
   review time (the three named artifacts and the BFF routes).
2. Confirm §4's four acceptance items still match the parent's live
   `acceptance` list in `ai-status.json` — re-run
   `AI_NAME=Claude2 python3 scripts/ai_status.py show OPENCLAW-OODA-PACKET-CLOSURE`
   if time has passed.
3. Confirm §5 does not accidentally steer the design decision rather than
   framing it neutrally.
4. Confirm this packet stays support-only: no canonical/runtime files were
   touched, only
   `support/sidecars/OPENCLAW-OODA-PACKET-CLOSURE/OPENCLAW-OODA-PACKET-CLOSURE-SIDECAR-ACCEPTANCE.md`
   was added.
5. Once approved, this sidecar task can be finalized to `done` independently
   of when the parent task itself closes — the parent remains
   `in_progress`/`needs_design_decision` and is not being marked accepted by
   this handoff.

---
*Generated by Claude as a sidecar `acceptance_packet` helper for
`OPENCLAW-OODA-PACKET-CLOSURE`. This file is a support artifact and does not
modify canonical truth.*
