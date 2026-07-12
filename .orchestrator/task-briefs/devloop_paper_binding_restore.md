# DEVLOOP-PAPER-BINDING-RESTORE-001 — Restore dev paper RuntimeBinding so the loop drains signals again

Owner: Claude · Reviewer: Codex · Lane: devloop-paper-runtime · Class: execution (live ops/wiring)

## Pinned live diagnosis (verified 2026-07-03, not a guess)

The dev paper loop currently produces **zero** paper trades. Root cause is a
single, precise live failure — the RuntimeBinding store is empty, so the paper
runtime worker fail-closes on every drain.

Evidence captured on the dev VM (`/home/lupin/code/pantheon`, live containers):

1. **Binding store is empty.**
   `docker exec pantheon-runtime-manager-1 cat /data/runtime/runtime_bindings.json`
   → `binding count = 0`. There is not a single RuntimeBinding registered.
   (runtime-manager listens on `8081`, mapped to host `18081`;
   `PANTHEON_RUNTIME_BINDING_STORE_PATH=/data/runtime/runtime_bindings.json`.)

2. **Paper runtime worker crash-loops, fail-closed (correct behaviour).**
   `pantheon-pantheon-paper-runtime-1` = `Up (unhealthy)`. Logs repeat:
   ```
   ERROR services.execution.lean_runtime.paper_runtime: paper runtime drain failed
   RuntimeError: RuntimeBinding is required before paper execution can drain signals
     at services/execution/lean_runtime/paper_runtime.py:1071 drain_once
   ```
   The worker is not buggy — it refuses to drain without a binding by design.

3. **Signals are being produced against a binding that no longer exists.**
   Cron `/home/lupin/paper-loop/feed_signals.sh` runs every minute and RPUSHes
   schema-valid signals (`strategy_id=strategy-devloop-l0-001`) onto
   `pantheon:signals:pending:rb-bf09c882005b4806a389b7d1d14f6469`
   ("devloop-L0" binding queue). signal-store redis:
   `DBSIZE = 2` — only the two `pantheon:signals:pending:rb-*` queues exist,
   **no fill / telemetry keys**. Signals pile up with no consumer.

4. **The L0 helper worker container was deleted.**
   `ensure_worker.sh` (cron, every minute) does `docker start paper-rt-test`,
   but `docker ps -a` shows **no such container**. The `&& echo` last fired at
   `2026-07-03T01:06Z`; the container was removed afterwards. All failures are
   swallowed by `2>&1`, so the harness looks healthy while doing nothing.

### Why the store emptied
The store held ~15 active paper bindings for ~11 days (see prior devloop
notes). It is now 0. Most likely a worker `git reset/clean` or a dev redeploy
`compose up` reset the untracked live state / volume (matches the known
"status-tree git fragility" incident class). Confirm before hardening.

## Scope of this task

Restore a working dev paper RuntimeBinding, get the loop draining end-to-end
again, and harden so it does not silently disappear. Do **not** fake trades,
do **not** relax the fail-closed drain guard, do **not** touch supervisor
cadence.

### Required plan
1. **Investigate** why `runtime_bindings.json` is empty and where bindings are
   supposed to come from (DeploymentPlan → RuntimeBinding lifecycle vs manual
   registration). Pin the actual wipe cause with evidence.
2. **Restore a binding** for `strategy-devloop-l0-001` via the proper
   runtime-manager path (not by hand-editing the volume file if an API/registrar
   exists). Its `binding_id` MUST match the queue the producer feeds
   (`rb-bf09c882005b4806a389b7d1d14f6469`) — otherwise update `feed_signals.sh`
   + the reconciler to the new id in the same change so producer and consumer
   agree.
3. **Prove drain**: `pantheon-pantheon-paper-runtime-1` becomes healthy, the
   pending queue depth drops, and a paper fill + TelemetryEvent is produced.
4. **Fix `ensure_worker.sh`**: it babysits a ghost `paper-rt-test` container and
   swallows errors. Either recreate a proper worker unit or switch to the
   compose-managed `pantheon-paper-runtime-1`; make failures visible in the log.
5. **Harden**: add a guard so the binding store survives a redeploy / git reset
   (link to the status-tree git guard work). Re-verify a binding is present
   after a simulated recreate.

## Acceptance (live evidence required)
- `runtime_bindings.json` has ≥1 active binding for `strategy-devloop-l0-001`,
  with `binding_id` == the queue the producer feeds (before/after shown).
- `pantheon-pantheon-paper-runtime-1` reports `healthy` and drain no longer
  raises `RuntimeBinding is required`; pending queue depth drops after restore.
- End-to-end proof: a paper fill + TelemetryEvent appears **after** the fix
  (reconciler / `/bff` telemetry shows a new paper trade with real fingerprint,
  not fixture/synthesized).
- `ensure_worker.sh` no longer babysits a non-existent container, and its
  failures are visible (not silently swallowed).
- Binding survives a container/volume recreate (evidence: re-check after
  recreate). No supervisor cadence change. Existing tests stay green.

## Out of scope
- Building a real strategy engine (current producer is a thin dev demo driver —
  fine for proving the loop; replacing it is a separate, design-first task).
- US market-data upstream (SRCLIVE-005, human-gated).
- Any change to the fail-closed drain policy.
