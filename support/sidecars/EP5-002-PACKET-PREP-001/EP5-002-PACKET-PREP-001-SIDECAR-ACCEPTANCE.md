# EP5-002-PACKET-PREP-001 Acceptance and Dependency Map (Sidecar)

**Parent Task**: `EP5-002-PACKET-PREP-001` - Prepare runtime-manager-originated EP5 live canary proof packet
**Parent Owner**: `Codex`
**Parent Reviewer**: `Gemini`
**Parent Status**: `todo`
**Sidecar Task**: `EP5-002-PACKET-PREP-001-SIDECAR-ACCEPTANCE`
**Sidecar Owner**: `Codex2`
**Sidecar Reviewer**: `Claude`
**Helper Kind**: `acceptance_packet`
**Generated**: `2026-04-27`
**Mutates canonical**: `no`

> This is a support artifact only. It does not modify L1 canonical truth, core
> contract truth, runtime / registry / governance implementation, or broker
> execution paths. It gives the parent owner a concrete acceptance packet for
> preparing the later EP5-002 live / canary proof packet without placing,
> modifying, or canceling any live broker order.

## 1. Executive Summary

`EP5-002-PACKET-PREP-001` should produce a dry-run / packet-prep bundle that lets
the later human-gated proof task start from a runtime-manager-originated shape.
The parent should prepare, but not execute:

1. a dry-run command envelope that exercises the runtime-manager authority shape,
2. an operator checklist that names account, instrument, quantity, price,
   session, rollback plan, and stop conditions,
3. telemetry and lineage refs that can replay the intended lifecycle,
4. a runtime lifecycle schema for submit, broker acknowledgement, cancel or fill,
   telemetry, runtime-manager excerpt, and operator note evidence,
5. an IBKR packet manifest that remains a capture kit until real evidence exists,
6. validator expectations for packet completeness and placeholder rejection,
7. a closeout template that records canceled, filled, partially filled, rejected,
   or otherwise resolved outcomes truthfully.

The parent must not turn packet prep into `EP5-002-RUNTIME-LIVE-PROOF-001`. Real
live / canary side effects still require explicit human approval for the exact
account, instrument, quantity, price, session, and rollback plan.

## 2. Source References

| Source | Why it matters |
|---|---|
| `ai-status.json` | Durable board entry for parent and sidecar owner / reviewer / dependency truth |
| `.orchestrator/task-briefs/ep5_002_packet_prep_001_sidecar_acceptance.md` | Confirms this helper is support-only and must hand off to `Claude` |
| `docs/reviews/2026-04-27-sd-materializable-execution-task-packet.md` | Defines parent scope: packet prep only, no broker side effects |
| `ai-task-archive/tasks/SD-FND-002.json` | Confirms foundation envelope pilot dependency is `done` and reviewed |
| `ai-task-archive/tasks/SD-LIN-TRACE-001.json` | Confirms lineage trace dependency is `done` and reviewed |
| `docs/deployment/ep5-canary-ready/README.md` | Existing prerequisite bundle and proof-boundary language for EP5-001 vs EP5-002 |
| `docs/deployment/ep5-canary-ready/operator-approval-checklist.md` | Existing operator checklist shape and dry-run / real-rehearsal separation |
| `docs/deployment/ep5-canary-ready/broker-venue-config-boundary.md` | VM-2-only broker / venue secret boundary and canary capital guardrails |
| `docs/deployment/ibkr-minimal-live-order-cancel-manual.md` | Existing minimal IBKR live order / cancel packet capture manual |
| `scripts/validate_ep5_live_order_cancel.py` | Existing init / record / validate packet tooling that does not submit orders |
| `scripts/run_ep5_canary_readiness.py` | Existing readiness, human-gate, rollback drill, and event-trace archive helper |
| `scripts/run_ibkr_live_order_cancel.py` | Later proof harness; dangerous path requiring explicit live-order acknowledgement, not for this sidecar |

## 3. Dependency Map

| Dependency | Current state | What the parent can rely on | Parent caution |
|---|---|---|---|
| `SD-FND-002` | `done`, archived at `2026-04-27T16:03:25Z` | BFF `POST /api/v1/operator/commands` and runtime-manager `execute_kill_switch` pilot paths use shared foundation envelopes, trace context, idempotency replay / conflict behavior, policy decision, audit action, and stable error envelopes; reviewer reran 59 passing tests | This is pilot adoption, not complete migration of every command path or durable cross-service idempotency primitive |
| `SD-LIN-TRACE-001` | `done`, archived at `2026-04-27T14:35:50Z` | Telemetry exposes `source_runtime_telemetry_trace` through `GET /api/telemetry/lineage/traces/<trace_id>/source-runtime-telemetry`; missing edges and conflict markers remain explicit; targeted suite passed 38 tests | This is a derived read model, not source / runtime / broker truth ownership and not order lifecycle reconciliation depth |

Downstream tasks that should not be absorbed into this parent:

| Task | Boundary |
|---|---|
| `EP5-002-RUNTIME-LIVE-PROOF-001` | Executes and archives the human-gated live / canary proof after explicit approval |
| `SD-RECON-001` | Deep order / fill / cancel / position / drift / alert lifecycle reconciliation |
| `CROSS-REPO-SD-VERIFY-001` | Multi-repo command authority and telemetry boundary verification |
| `SD-SRC-EVIDENCE-001` | Governed source connector, evidence bundle, knowledge object, and search gateway work |

## 4. Parent Acceptance Checklist

| Parent acceptance target | Evidence to create during parent run | Pass condition |
|---|---|---|
| Dry-run command envelope is runtime-manager-originated | A JSON or markdown packet showing the intended command envelope, trace / correlation / idempotency fields, actor, policy context, audit context, target stage, and rollback refs | The packet names runtime-manager as lifecycle origin and never instructs the parent to place or cancel a live broker order |
| Operator checklist is exact enough for a later human gate | Checklist fields for account, instrument, security type, venue, quantity, order type, limit price rule, TWS / IBKR session, operator ID, rollback plan, and stop conditions | Reviewer can see the exact future approval surface; no placeholder can be confused for approval |
| Telemetry / lineage refs are prepared | A refs section tying future order lifecycle events to `source_runtime_telemetry_trace`, runtime binding ID, deployment plan ID, telemetry event IDs, and missing-edge handling | The later proof can replay a trace and expose missing evidence explicitly |
| Runtime lifecycle schema is explicit | Template schema for submit request / response, broker acknowledgement, cancel request / response or fill evidence, runtime-manager excerpt, telemetry trace, and operator note | Packet distinguishes accepted, canceled, filled, partially filled, rejected, expired, or incident outcomes |
| IBKR packet manifest remains a capture kit | Manifest lists required files from `validate_ep5_live_order_cancel.py` and points to the manual runbook | Manifest states it is not EP5 proof until real evidence replaces placeholders and validation passes |
| Validator expectations are documented | Expected validator command, required files, placeholder rejection, minimal order guardrails, broker acknowledgement, cancel / no-fill disposition, telemetry trace, and runtime excerpt checks | Parent produces a reviewer-replayable validation plan without invoking the live-order harness |
| Closeout template records truthful disposition | Template includes operator signoff, broker result, cancel/fill result, telemetry trace replay status, runtime-manager lifecycle result, and incident follow-up if needed | Any non-ideal outcome is recorded as truth, not hidden behind a success label |
| No live broker side effect occurs | Parent evidence includes only docs/templates/dry-run packet files and optional validator init output | No use of `scripts/run_ibkr_live_order_cancel.py --i-understand-live-order`, no live submit/cancel, and no broker state change |

## 5. Suggested Parent Verification Commands

Run from `/home/lupin/code/pantheon` unless noted.

Use these read-only or packet-initialization checks to ground the parent packet:

```bash
rg -n "EP5-002-PACKET-PREP-001|EP5-002-RUNTIME-LIVE-PROOF-001|live / canary proof packet prep" \
  ai-status.json docs/reviews/2026-04-27-sd-materializable-execution-task-packet.md

rg -n "proof_boundary|not EP5-002 proof|validate_ep5_live_order_cancel|run_ibkr_live_order_cancel" \
  docs/deployment/ep5-canary-ready docs/deployment/ibkr-minimal-live-order-cancel-manual.md scripts

rg -n "source_runtime_telemetry_trace|source-runtime-telemetry|order_submitted|order_canceled|order_cancelled" \
  services/telemetry services/registry/lineage scripts/validate_ep5_live_order_cancel.py

rg -n "operator/commands|X-Trace-Id|X-Correlation-Id|X-Idempotency-Key|foundation_error" \
  services/control-plane/bff/main.py services/control-plane/bff/test_governance_command_submission.py

rg -n "execute_kill_switch|rollback|kill_switch|CommandEnvelope|IdempotencyRecord" \
  services/runtime-manager services/foundation
```

Optional packet-init confidence check. This writes only a capture-kit folder with
placeholders and does not submit an order:

```bash
python3 scripts/validate_ep5_live_order_cancel.py init \
  --packet-dir /tmp/pantheon/ep5-002-packet-prep/live-order-cancel-template \
  --account '<account-ref>' \
  --limit-price '<operator-set-far-from-market-value>' \
  --runtime-binding-id '<runtime-binding-id>' \
  --deployment-plan-id '<deployment-plan-id>' \
  --operator-id '<operator-id>'
```

Optional validator test confidence, if the parent owner wants a fresh tooling
check rather than relying on existing repo state:

```bash
python3 scripts/test_validate_ep5_live_order_cancel.py
python3 scripts/test_run_ibkr_live_order_cancel.py
```

Do not run the live harness from this parent prep task:

```bash
# forbidden for EP5-002-PACKET-PREP-001
python3 scripts/run_ibkr_live_order_cancel.py ... --i-understand-live-order
```

## 6. Review Guardrails

| Reviewer should reject | Reason |
|---|---|
| Treating this sidecar as the parent packet itself | This file is a checklist and dependency map, not the final parent handoff packet |
| Editing L1 canonical truth or runtime implementation from this sidecar | The helper scope is support-only |
| Placing, modifying, or canceling a broker order during packet prep | Live / canary side effects belong only to the later human-gated proof task |
| Using `EP5-001` readiness artifacts as `EP5-002` proof | Existing docs explicitly mark them prerequisite-only and not EP5-002 proof |
| Treating direct IBKR harness evidence as runtime-manager-originated proof | Parent must prepare a runtime-manager-originated packet shape, not promote a direct harness as canonical lifecycle proof |
| Accepting placeholders as approval or evidence | Placeholders are allowed only in templates / capture kits and must fail final proof validation |
| Omitting adverse outcome handling | Filled, partially filled, rejected, expired, or incident outcomes must be closeout states, not suppressed errors |

## 7. Reviewer Checklist

| Check | Status | Evidence |
|---|---|---|
| Support artifact only | PASS | This sidecar creates only `support/sidecars/EP5-002-PACKET-PREP-001/EP5-002-PACKET-PREP-001-SIDECAR-ACCEPTANCE.md` |
| No canonical truth edited by sidecar | PASS | No L1 policy docs, contract docs, runtime registry, governance implementation, broker harness, or service code were modified |
| Dependencies mapped | PASS | `SD-FND-002` and `SD-LIN-TRACE-001` are both archived `done` with reviewer approval and test evidence |
| Parent acceptance is concrete | PASS | Section 4 maps each packet-prep target to evidence the parent can create and reviewer pass conditions |
| Broker side-effect boundary is explicit | PASS | Sections 1, 4, 5, and 6 forbid live order placement / cancel during packet prep |
| Downstream human gate remains separate | PASS | `EP5-002-RUNTIME-LIVE-PROOF-001` stays outside this sidecar and outside parent packet prep execution |

## 8. Handoff to Reviewer (`Claude`)

This sidecar is ready for reviewer use as the acceptance / dependency packet for
`EP5-002-PACKET-PREP-001`.

What it gives you now:

1. a dependency map showing both parent prerequisites are complete and what each
   contributes to runtime-manager packet prep,
2. a parent acceptance checklist for dry-run envelope, operator checklist,
   telemetry / lineage refs, runtime lifecycle schema, IBKR manifest, validator
   expectations, and closeout template,
3. replayable search and packet-init commands that avoid live broker side
   effects,
4. explicit review guardrails so this helper does not mutate canonical truth or
   collapse packet prep into human-gated proof execution.

Recommended reviewer stance:

1. approve this sidecar if it accurately reflects the support-only boundary and
   gives the parent owner a usable packet-prep checklist,
2. keep the parent task responsible for creating the actual packet artifacts and
   deciding whether any validator or docs follow-up is needed,
3. reject any attempt to treat this sidecar, EP5-001 readiness artifacts, or a
   direct IBKR harness template as completed EP5-002 live / canary proof.

---
*Generated by Codex2 as a sidecar `acceptance_packet` helper for
`EP5-002-PACKET-PREP-001`. This file is a support artifact and does not modify
canonical truth.*
