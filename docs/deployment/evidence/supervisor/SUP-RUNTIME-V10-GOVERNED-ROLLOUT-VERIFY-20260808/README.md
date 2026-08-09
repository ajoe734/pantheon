# SUP-RUNTIME-V10 governed rollout verification

Owner: Codex

Reviewer: Codex2

Outcome: fail closed; runtime unchanged; legacy-drift bootstrap follow-up required

## 2026-08-09 governed retry after PR #4648

The prerequisite source repair was independently reviewed at exact head
`10c5ba3fcf44209d2ec33cf2478a72b3a86fa048` and merged to `dev` by PR #4648
as `f5570754e6b9534893fc65744e82abe7f0ff0a74`. The task branch composed that
merge, then the authorized retry again used only:

```text
$PANTHEON_COMMAND_ROOT/scripts/sync-dev-root.sh $PANTHEON_COMMAND_ROOT
```

The sync script protected the running mutable root, fetched accepted `dev`,
materialized the SHA-named standalone candidate `f5570754e...`, and delegated
all promotion authority to the candidate transaction. The transaction stopped
before baseline capture, config mutation, process signalling, or candidate
launch with `ValueError: Tracked git tree is dirty`.

PR #4648 correctly prevents future supervisor queue/watch paths from
materializing task briefs in the command checkout, without weakening the
promotion cleanliness guard. It cannot remove four task-brief overwrites that
the legacy incumbent accumulated before the fix was active. The live PID
`3816018`, mutable cwd at `5877b644...`, argv, allowlisted environment, and
config SHA-256 `904830b6...` remained unchanged after the abort.

Durable transaction evidence:

```text
/home/lupin/pantheon-ci-deploy/runtime/promotion-evidence/
supervisor-runtime-promotion-20260809T062622204757Z-3114628.json
SHA-256: 2621af8274e995708218bf245d42447ad68d5138dfb52047896392c176ce8f14
```

A read-only candidate discovery passed immutable commit/tree/remote/Git
identity for `f5570754e...` with tree `a910a73b...`. After the discovery
status child, `git status --short --ignored` and an explicit filesystem scan
both found no `__pycache__`, `.pyc`, or `.pyo` path. Discover-only remained
ineligible because no launched candidate state exists and it does not bind the
legacy mutable incumbent; this is expected and is not recovery evidence.

No candidate process existed, so this record does not claim the `python -B`
launch contract, status-child proof under a launched runtime, three fresh
loops, authoritative-shadow catch-up, queue/worker parity, or provider
baseline. Those gates remain pending.

The next source-only packet is
`source-only-followup-legacy-drift-bootstrap.json`. It asks for an explicit,
transactional legacy-bootstrap boundary that can safely address already
accumulated, provenance-verifiable generated context drift without mutating the
active checkout or broadly ignoring tracked changes. A later live retry still
requires separate governed dispatch.

The signed packet was queued at `06:31:28Z` and the supervisor drained it at
`06:32:24Z`. Canonical `show` proves the task row was committed as `todo` with
owner `Codex2` and reviewer `Codex`, but the receipt is `failed` and admission
is `not_attempted`: the pinned `5877b644...` status runtime raised
`OverflowError: date value out of range` while refreshing derived
`current-work.md` after the authoritative assign transaction. Dispatch remains
fail-closed because `scripts/explain_dispatch.py` reports that the signed packet
is not admitted. The already-governed
`OPS-AI-STATUS-SENTINEL-TIMESTAMP-OVERFLOW-20260809` source task owns that
status-renderer defect. Do not claim the legacy-bootstrap implementation is
underway until a successful admission receipt and authoritative readback exist.

When later governed status refresh succeeded, replaying the original packet id
correctly returned `duplicate` because it is durably archived under `failed/`.
A retry packet with a new id but the byte-model-identical task spec was also
correctly rejected: the already-materialized task still binds the original
signed provenance. This closes off unsafe operator workarounds but exposes a
bridge atomicity/recovery gap. The additional source-only packet
`source-only-followup-bridge-partial-assign-recovery.json` requests a supported
authoritative recovery path for this exact post-commit failure; it does not
broaden or duplicate the legacy-bootstrap implementation scope.

That bridge-recovery packet was processed and admitted at `06:41:24Z`.
Authoritative materialization readback verified event count `13943` and equal
expected/projected SHA-256
`122280612385d969d79baf72d210f0f2b5c52886df96a6d0c3184a173b088445`.
Task `SUP-ASSISTANT-DEV-BRIDGE-PARTIAL-ASSIGN-RECOVERY-20260809` now exists as
`todo`, owned by `Claude2` and reviewed by `Codex`. This is the accepted source
repair lane; the original legacy-bootstrap task remains blocked on admission.

## 2026-08-08 prior fail-closed attempts

On 2026-08-08 the authorized retry used only
`$PANTHEON_COMMAND_ROOT/scripts/sync-dev-root.sh $PANTHEON_COMMAND_ROOT`.
The sync script protected the live mutable `dev-root`, fetched merged `dev`,
materialized immutable candidate
`5877b64425c8d6aede147d6cbbc6fbb9e228c259`, and handed all promotion
authority to the candidate's transactional operator.

The prior candidate-Git identity repair is present through merge commit
`fee6f738a58b82ccc269ed8481ddc5a1a7a68b85`, but the retry stopped at the
next fail-closed boundary: the mutable incumbent contains one tracked change,
an orchestrator-regenerated task brief. The operator rejected it with
`ValueError: Tracked git tree is dirty` before incumbent capture, config
mutation, process signalling, or candidate launch.

Durable transaction evidence:

```text
/home/lupin/pantheon-ci-deploy/runtime/promotion-evidence/
supervisor-runtime-promotion-20260808T225515388905Z-3787166.json
SHA-256: 0b60885dcf2e0434e92c3ae02b0478dce800ca8ff0330a5f7ba9a87d1d50c33b
```

Post-abort observations confirm PID `98981` still runs from mutable commit
`619acd04184e8d3fc3aef322d160e7c9106670ad`, and the live config hash remains
`904830b6ff1487f0a3d665be13446c55c8ab20d775ae37d9a0107647270eafa9`.
The immutable candidate passed commit/tree/remote/standalone-Git identity
discovery and remained free of ignored `__pycache__`, `.pyc`, and `.pyo`
paths after the discovery child ran.

At 22:58:23Z an external mechanism subsequently started PID `3816018` from
the mutable `dev-root` after that checkout moved to `5877b644...`. This was
not the aborted transaction's candidate launch: the cwd is not the immutable
SHA-named candidate, argv is `python3.12 -u` without `-B`, and the process
environment still lacks `PANTHEON_COMMAND_ROOT` and
`PANTHEON_COMMAND_RUNTIME_SHA`. The regenerated tracked task-brief drift also
reappeared immediately. This later process is alive but does not satisfy the
governed V10 rollout contract and is not accepted as recovery evidence.

No candidate process existed, so this record intentionally does not claim the
`python -B` launch contract, status-child bytecode proof, three fresh loops,
authoritative-shadow catch-up, queue/worker parity, or provider baseline.
Those checks remained blocked pending the preventive source repair and a
separately authorized governed retry.

`evidence.json` is the task-scoped review manifest. The source-only packet
`source-only-followup-mutable-tracked-drift.json` is submitted through the
signed assistant dev bridge. Supervisor receipt
`sup-runtime-v10-mutable-tracked-drift-followup-20260808-2255.json` records
successful authoritative materialization of
`SUP-RUNTIME-V10-MUTABLE-TRACKED-DRIFT-FOLLOWUP-20260808` at 23:01:24Z.
That task later merged through PR #4648; the 2026-08-09 retry above proves the
preventive fix alone cannot bootstrap the already-dirty legacy incumbent.
