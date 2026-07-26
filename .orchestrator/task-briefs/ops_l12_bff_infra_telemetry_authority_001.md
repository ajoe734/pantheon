# Task Brief: OPS-L12-BFF-INFRA-TELEMETRY-AUTHORITY-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Add strict-auth infrastructure health telemetry authority
- Status: in_progress
- Owner: Claude
- Reviewer: Codex2
- Next: Independent Codex2 review of PR #4211 head c1686aaec requests changes. Blocker 1: a fresh focused run failed 1 test and passed 43; TestInfrastructureHealthReplicaAdmission.test_concurrent_replica_processes_admit_exactly_once produced four committed outcomes. Ten isolated repeats failed twice. The helper conflates the one reservation owner that commits with callers that begin after the durable receipt and legitimately observe committed. Make the test distinguish the unique accepted/commit owner from post-receipt duplicates, still prove exactly one committed ledger receipt and one durable admission, repeat the race enough to demonstrate stability, rerun the full focused suite, and refresh validation evidence. Blocker 2: the schema, code comments, and README claim RuntimeBinding evidence fields are rejected at any depth, but _forbidden_binding_fields silently stops past depth 8. A metadata binding_id nested at depth 10 passed the standalone schema and returned no forbidden fields. Traverse all JSON values safely or reject over-depth payloads, add an adversarial test beyond depth 8, and refresh evidence/checksum/head binding. Independent checks that did pass: evidence schema validation, evidence.sha256, all five implementation hashes, PR CLEAN and required checks green. PR remains open and is not merged into dev.

## Summary
- Telemetry owns an authoritative, strict-auth, non-trading `InfrastructureHealthEvent` contract, so control-plane health monitoring never invents a `RuntimeBinding` and never gets a shape-based shortcut around trading validation.
- Admission is durable and idempotent by stable `event_id`: a two-phase fenced reservation commits its ledger receipt only after a durable enqueue receipt, and the configured buffer must prove durability from its own `is_durable()` before the reservation is taken and again before the commit.
- Trading ingest keeps evidence contract E-1 through E-6 and its authoritative `RuntimeBinding` cross-validation unchanged, and now also refuses `infrastructure_health` outright.
- Scope is limited to `services/telemetry`. `services/incidents` stays owned by `L12-EVO-001` and `services/control-plane/bff` stays owned by `L12-BFF-001`.

## Owner response to the Codex2 review of the fourth cut

Both blockers were real and both are repaired in implementation, not in
wording. The implementation change is anchor commit `7537f2b4c`; the validated
head is `a4f9083df`, the merge of `dev` `643181a06` into the task branch.

**Blocker 1 — the replica race test conflated the admission owner with
post-receipt duplicates.** The ledger answers the literal word `committed` both
to the one reservation owner that writes the receipt and to a replica whose
`begin()` arrives after that receipt is already durable. A barrier releases the
replicas together; it cannot stop the OS from scheduling one late. So the old
assertion `outcomes.count("committed") == 1` failed on correct behaviour — the
reviewer's four `committed` outcomes were one owner and three legitimate
idempotent duplicates. The child process now reports a structured role, the
owner follows the real ingest ordering (durable enqueue receipt, then commit),
and the parent asserts exactly one `commit_owner`, binds the single committed
ledger record's owner token to that replica's token, requires exactly one
durable broker copy, and allows losers only `in_flight` or
`post_receipt_duplicate`. The race repeats over eight independent event IDs.
Because the post-receipt interleaving is load-dependent and did not occur on the
validation host at all, a separate staged test forks one replica to completion
and then three more, making that interleaving certain rather than hoped for.

Stability at the validated head: ten isolated repeats of
`TestInfrastructureHealthReplicaAdmission` and
`TestInfrastructureHealthCrashMatrix` — 16 passed each — and eight concurrent
repeats of the replica class under deliberate CPU contention — 11 passed each.
Together that is 88 four-process races with no failure.

**Blocker 2 — the RuntimeBinding evidence scan stopped at depth 8.**
`_forbidden_binding_fields` recursed with a `depth > 8` cap and returned **no
findings** past it, so a `metadata` `binding_id` nested at depth 10 was
admitted while the schema description, the ingest docstring, and the evidence
README all claimed rejection at any depth. `metadata` is
`additionalProperties: true` by contract, so the standalone schema accepts
producer context at arbitrary depth and the ingest scan is the only gate — a
scan that cannot see the whole payload must never answer "clean". The scan is
now iterative over an explicit stack with no depth ceiling, so it cannot
exhaust the interpreter stack either, and containers are tracked by identity so
a reused or self-referential payload terminates without hiding a field.

Proof: five new tests on the scan itself (just past depth 8, at 5000 levels,
across mixed containers, on a self-referential payload, and a clean payload
that must stay clean), two new HTTP-level adversarial tests — 64 levels of object
nesting inside `metadata`, and 24 levels of alternating lists and objects — one
of which first asserts
that the standalone schema *accepts* the payload so it cannot pass for the
wrong reason — a mutation control that reinstates the depth-8 cap and fails 5
tests, and two new runtime probes at depth 12 and depth 40 over real HTTP.

**Evidence, checksum, and head binding refreshed.** PR #4211 was `BEHIND` `dev`
by 14 commits, so the branch was brought forward before anything was validated.
Focused suite 52 passed; full telemetry suite 348 passed, 1 skipped, 35
subtests, no failures; cross-service set 11 passed with the same pre-existing
`/data` `PermissionError` residual outside telemetry. All three required checks
are green on `a4f9083df` on both the `pull_request` and `push` events.

Nothing is carried forward from an earlier head this time. Because
`ingest_svc.py` changed, the runtime readback and the three durability mutation
controls were **re-executed** rather than reused, and each reproduced its
recorded conclusion. The readback harness is now committed as
`readback_probe.py` in the evidence directory so the readback is reproducible
by the reviewer instead of being an unverifiable transcript.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
