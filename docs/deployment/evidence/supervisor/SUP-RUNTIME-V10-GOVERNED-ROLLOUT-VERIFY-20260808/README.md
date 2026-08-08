# SUP-RUNTIME-V10 governed rollout verification

Owner: Codex

Reviewer: Codex2

Outcome: fail closed; runtime unchanged; source-only follow-up required

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

No candidate process existed, so this record intentionally does not claim the
`python -B` launch contract, status-child bytecode proof, three fresh loops,
authoritative-shadow catch-up, queue/worker parity, or provider baseline.
Those checks remain blocked until the source-only bootstrap drift policy is
repaired and a separately authorized governed retry succeeds.

`evidence.json` is the task-scoped review manifest. The source-only packet
`source-only-followup-mutable-tracked-drift.json` is submitted through the
signed assistant dev bridge; its receipt and materialized task identity are
recorded in the manifest after supervisor drain.
