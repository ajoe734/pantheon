# Round 1 — Results

**Executed:** 2026-06-14 (UTC). **Target:** dev BFF
`https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io`, dev FE
`https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io`.

## Evidence

| Check | Probe | Result | Verdict |
|---|---|---|---|
| H1 FE reachable | `GET /` (FE) | `200` | PASS |
| H1 BFF healthy | `GET /health`, `/readyz` | `200`, `200` | PASS |
| H2 auth gate | `GET /bff/v5/loop-runs` no token | `401` | PASS |
| H2 stub auth | same + `Bearer op-dev:admin:mfa` | `200` | PASS |
| H3 contract surface | `GET /openapi.json` | `200`, 447 paths, "Pantheon Operator BFF 0.2.0" | PASS |
| H4 control-room | `GET /bff/v5/control-room` | keys: `loops, interventions, sentinel, ooda_status, meta` | PASS |
| H5 fail-closed | `ooda_status.live_capital_side_effects` | `false` (paper env) | PASS |
| H6 surface consistency | `ooda_status` vs `meta.surfaces.ooda_control_room_status` | **contradiction** | **FAIL** |

## Findings

### F1 — Control-plane is healthy but OODA loop is empty (fleet-owned gap, verified)

`ooda_status`: `enabled=true, gate_state=enabled, total_packet_count=0,
open=0, closed=0, failed=0`. `loop-runs` total 0. `sentinel`/`interventions`
empty. This independently re-confirms (live, 2026-06-14) the known
"deployed-but-not-closed" gap: the control plane serves correctly but no OODA
packets/loops flow because the upstream producers (signal producer, market
data, real artifacts) are not yet emitting. **Recorded as a finding; owned by
the in-flight fleet build-gap work — not fixed in this campaign.**

### F2 — False-green: OODA card reports stage `status: ok` while its source is `missing` (FIXED this branch)

The same control-room response is internally contradictory:

- `ooda_status` card body: every stage reports `status: "ok"`,
  `gate_state: "enabled"`.
- `meta.surfaces.ooda_control_room_status`: `{"source": "missing",
  "status": "unavailable"}`.

Root cause: `_build_ooda_control_room_status_card()` in
`services/control-plane/bff/main.py` derives `meta.status` from the dataset
source (`dataset_source("ooda_packets")` → `unavailable` when missing) but
**hardcodes** every stage card's `status` to `"ok"` regardless of source
availability. A dashboard reading `meta.surfaces` sees the OODA surface as
degraded, while one rendering the stage cards sees all-green — an operator
could believe the OODA pipeline is healthy when its backing dataset cannot be
read at all.

This is distinct from F1: F1 is "0 packets" (a real upstream gap); F2 is the
read model failing to distinguish "0 packets from a present source" from
"source missing", and mis-signalling the latter as green.

## Fix (this branch — normal dev workflow)

When `ooda_src` is missing/unavailable, propagate `status: "unavailable"` to
every stage card instead of hardcoding `"ok"`, so the card body agrees with
`meta.status`. Behaviour when the source is present (including legitimately
empty, 0 packets) is unchanged: stages remain `"ok"` with `active_count: 0`.

Test: extend `test_ooda_status_card_no_source_returns_unavailable` to assert
the stage cards also report `unavailable` when the source is missing.

## Net

7/8 hypotheses PASS. The dev control plane is reachable, healthy, auth-gated,
and fail-closed. One real defect (F2) found and fixed; one upstream gap (F1)
verified and attributed to fleet-owned work.
