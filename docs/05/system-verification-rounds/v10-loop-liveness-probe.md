# V10 — Loop-Liveness / OODA-Observability Probe (capstone)

**Round:** V10 (final) of the 10-round system-verification campaign
**Direction:** A/E — empirical loop liveness (the program's founding question)
**Date:** 2026-06-14
**Branch / PR:** task/verify-v10-loop-liveness

## Non-duplication check

- `.orchestrator/task-briefs/` loop/telemetry briefs (`p0_loop_001`, `p0_tel_001`,
  `p0_tel_proj_001`, `tel_002_rb`, `ep5_005_v2`) are all **build-side** — they
  populate the loop-run / telemetry projections. This round **observes/asserts**
  liveness from the BFF surfaces; opposite side of the same wire, no overlap.
- All nine prior rounds were static audits or happy-path / error-path reads. None
  query the v5 loop/OODA liveness surfaces. → distinct.

## What it does

`scripts/probe_loop_liveness.py` queries the v5 control surfaces that project
loop/OODA liveness and reports a structured liveness summary:

- `/bff/v5/loop-runs` — canonical loop-run ledger (count)
- `/bff/v5/control-room` — aggregate OODA gate + loop counters
- `/bff/v5/execution/persona-health` — per-persona OODA stage / health
- `/bff/v5/execution/strategy-health` — per-strategy health surface

Conservative, CI-safe failure semantics: **FAIL only on unreachable / 5xx**;
empty ledgers and degraded surfaces are **reported** (an idle paper fleet
legitimately has zero loop runs), so the probe is a stable gate while still
surfacing the liveness truth in its output.

## Live result (dev, 2026-06-14)

```
== loop-liveness surfaces ==
  200  /bff/v5/loop-runs                 surfaces={'loop_runs': 'ok'}
  200  /bff/v5/control-room              surfaces={'control_room':'ok','loop_runs':'ok','sentinel_findings':'ok','ooda_control_room_status':'unavailable'}
  200  /bff/v5/execution/persona-health  surfaces={'persona_health':'ok','persona_league':'ok'}
  200  /bff/v5/execution/strategy-health surfaces={'strategy_health':'unavailable'}

== liveness observations ==
  loop-run ledger total : 0
  OODA gate / counters   : gate=enabled open=0 closed=0 failed=0 packets=0
  degraded surfaces      : ooda_control_room_status=unavailable, strategy_health=unavailable
  NOTE: loop-run ledger empty - loops not demonstrably live via v5 ledger

OK: all loop-liveness surfaces reachable (no 5xx)   EXIT=0
```

## Capstone finding

Infrastructure is **up** — BFF surfaces return 200, personas are registered with
real OODA stages (e.g. `persona-crypto` at stage "Act", `paper_running`,
score 91.8), and runtime bindings are active in paper mode. But the **v5 loop-run
ledger is empty (0 runs, 0 OODA packets)** and two surfaces report `unavailable`
(`strategy_health`, `ooda_control_room_status`).

**Conclusion:** normal operation is *provisioned* but not yet *proven* through the
canonical loop/OODA telemetry. The loops are not demonstrably live via the v5
ledger. This is the highest-value open thread for any follow-on campaign, and it
is distinct from the P0 build-side tasks that populate these projections — those
build the pipe; this round shows the pipe is currently dry on the read side.

## Program close-out

V10 completes the 10-round campaign. See `MASTER-VERIFICATION-PROGRAM.md` →
"Consolidation (V1-V10)" for the full inventory of tooling shipped, defects fixed,
and the standing open thread (loop liveness).
