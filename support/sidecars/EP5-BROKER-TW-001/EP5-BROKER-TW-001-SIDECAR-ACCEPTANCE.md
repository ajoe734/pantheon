# EP5-BROKER-TW-001 Acceptance and Dependency Map (Sidecar)

**Parent Task:** `EP5-BROKER-TW-001` - Scaffold Shioaji TW broker adapter with fail-closed sandbox-only gating  
**Parent Owner:** `Claude`  
**Parent Reviewer:** `Codex2`  
**Parent Status:** `todo`  
**Sidecar Task:** `EP5-BROKER-TW-001-SIDECAR-ACCEPTANCE`  
**Sidecar Owner:** `Codex`  
**Sidecar Reviewer:** `Claude`  
**Helper Kind:** `acceptance_packet`  
**Generated:** `2026-05-10`  
**Mutates canonical:** `no`

> This is a support artifact only. It does not modify L1 canonical truth, core
> contract truth, runtime / registry / governance implementation, or broker
> runtime behavior. It packages the parent acceptance checklist, dependency map,
> review guardrails, and handoff notes for the Shioaji Taiwan broker sandbox
> sidecar slice.

## 1. Executive Summary

`EP5-BROKER-TW-001` should add a bounded `services/broker/shioaji/` adapter that
can exercise Shioaji simulation account semantics without enabling production
live order side effects.

The parent task is a Track A broker sandbox support slice for EP5 canary
readiness. Its useful output is not a canonical policy change and not a real
capital proof. It should give the repo a Shioaji-specific broker-side adapter
surface that:

1. pins the Shioaji SDK in a service-local requirements file,
2. defaults closed unless `BROKER_SHIOAJI_SANDBOX_ENABLED` is explicitly true,
3. exposes submit / cancel / get-status behavior against a simulation account
   or test boundary,
4. always rejects production live behavior with a clear error payload,
5. aligns response shape with the existing paper broker sidecar enough for
   downstream smoke / readiness tooling to consume it, and
6. can produce or reference broker sandbox evidence suitable for later
   `broker_sandbox_smoke_ref` and human-gate packet wiring.

The parent should reuse current repo boundaries where they exist:

- `services/execution/shioaji_adapter.py` already models Shioaji order intent,
  contract, and quote payload shape without importing the vendor SDK into core
  execution code.
- `scripts/run_broker_sandbox_order_smoke.py` already defines the repo's
  fail-closed Shioaji simulation evidence packet shape: auth, account
  readiness, place, cancel/replace, readback, execution/no-fill, telemetry, and
  reconciliation.
- `services/broker/paper_simulation.py` and `services/broker/main.py` show the
  current broker-side response invariants: explicit gate, clear broker error
  payloads, no real capital, and live fail-closed.

## 2. Source References

| Source | Why it matters |
|---|---|
| `ai-status.json` | Current durable board truth for parent and sidecar owner / reviewer / acceptance criteria |
| `.orchestrator/task-briefs/ep5_broker_tw_001_sidecar_acceptance.md` | Confirms this helper is support-only and must hand off to `Claude` |
| `PAPER_CANARY_LIVE_POLICY.md` | Canonical policy: broker sandbox / paper-account API smoke is allowed and required early, while production live side effects remain fail-closed |
| `docs/deployment/ep5-canary-ready/README.md` | Current EP5 readiness bundle and human-gate packet flow |
| `docs/deployment/ep5-canary-ready/broker-venue-config-boundary.md` | Defines Shioaji as the Taiwan execution broker boundary and requires non-production smoke modes |
| `docs/deployment/ep5-canary-ready/operator-approval-checklist.md` | Shows where broker sandbox smoke evidence fits before later canary / live gates |
| `services/execution/runtime-manager/contract.md` | Requires canary/live activation gate evidence including `broker_sandbox_smoke_ref` |
| `services/runtime-manager/service.py` | Enforces canary/live activation gate fields and canary scale limits in code |
| `services/broker/paper_simulation.py` | Existing broker response and invariant model for simulated orders |
| `services/broker/main.py` and `services/broker/test_broker.py` | Existing broker gate, route, error-code, and test style |
| `services/execution/shioaji_adapter.py` and `services/execution/test_shioaji_adapter.py` | Existing Shioaji payload / symbol / quote boundary the new broker adapter should not contradict |
| `scripts/run_broker_sandbox_order_smoke.py` | Existing non-production Shioaji simulation evidence writer and production-live mode rejector |
| `support/reviews/P2-BROKER-SANDBOX-ORDER-001-codex-review.md` | Reviewer-approved baseline for broker sandbox smoke evidence boundaries |

## 3. Repo-Current Truth Snapshot

| Truth item | Current evidence | Implication for parent |
|---|---|---|
| Parent artifacts do not exist yet | `support/sidecars/EP5-BROKER-TW-001/` was absent before this packet; `services/broker/shioaji/` is not present in the broker tree | Parent must create the adapter package and tests; this sidecar is only the acceptance packet |
| Generic broker sidecar is paper-only and gated | `services/broker/main.py` uses `BROKER_PAPER_ENABLED`, rejects live orders with `LIVE_ADAPTER_DISABLED`, and returns paper order status/list/get payloads only when enabled | Shioaji broker should follow the same fail-closed default posture and explicit error-payload style |
| Paper simulation order model records no real capital | `PaperOrder` carries `sim_fill_flag=True`, `is_real_order=False`, `is_real_capital=False`, and `deployment_stage="paper"` | Shioaji simulation responses should keep no-real-capital / no-production-live fields explicit |
| Shioaji execution payload boundary already exists | `services/execution/shioaji_adapter.py` normalizes TW symbols, builds order payloads, and marks `simulation=True` when configured | Parent can reuse or mirror payload semantics, but should not silently rewrite execution-plane contract truth |
| Broker sandbox smoke packet shape already exists | `scripts/run_broker_sandbox_order_smoke.py` supports `--provider shioaji --mode simulation` and writes structured lifecycle evidence | Parent should align its README / smoke guidance to this evidence shape instead of inventing a conflicting packet vocabulary |
| Runtime canary activation requires a broker smoke ref | Runtime manager contract and service require `broker_sandbox_smoke_ref` for canary/live forward activation | Parent output should say how its Shioaji sandbox evidence feeds that ref, without claiming the canary gate is approved |

## 4. Parent Acceptance Checklist

| Parent acceptance target | Required parent evidence | Review pass condition |
|---|---|---|
| Shioaji SDK pinned in `services/broker/shioaji/requirements.txt` | Service-local requirements file with a pinned `shioaji` package version and no changes to shared requirements | Reviewer can see a reproducible dependency pin for this adapter only |
| Adapter scaffolds submit / cancel / get-status against simulation account | `services/broker/shioaji/adapter.py` exposes a small adapter API and records simulation account / order refs without production live side effects | Submit returns a structured accepted/simulated response; cancel and get-status return replayable simulation/readback shape |
| Live order path always fail-closed | Code path or public function rejects live / production / real-money mode independent of the sandbox env gate | Error response has explicit status and error code, e.g. `SHIOAJI_LIVE_ADAPTER_DISABLED`; no SDK live submit/cancel is reachable |
| `BROKER_SHIOAJI_SANDBOX_ENABLED` gate enforced with default false | Tests run with env unset/false and true; README documents accepted true values | Unset default rejects submit/cancel/get-status or any SDK-touching path with an explicit disabled error |
| Unit / smoke tests cover fail-closed default and gated sandbox path | `services/broker/shioaji/test_adapter.py` covers closed gate, enabled simulation submit, cancel, get-status/readback, invalid input, and live reject | Tests can run without raw Shioaji credentials and without network side effects, using fakes or SDK stubs where needed |
| Interface aligns with `services/broker/paper_simulation.py` shape | Adapter responses include stable keys for status, order id/ref, symbol, qty/quantity, side, order_type, created/updated timestamps, simulation flags, and error payloads | Downstream smoke/readiness code can consume the response without guessing whether it is real capital or sandbox simulation |
| Evidence can feed EP5 human-gate packet flow | README or generated smoke note names the expected evidence directory and how it maps to `broker_sandbox_smoke_ref` | Parent does not need to run real credentials, but it must make the handoff path explicit and compatible with `run_ep5_canary_readiness.py` |
| No raw secrets enter repo artifacts | README and tests use secret refs or placeholders only; adapter does not persist raw API key / secret values in evidence | Secret-pattern scan over parent-owned docs/evidence should not find real credential material |

## 5. Dependency Map

### 5.1 Upstream Anchors

| Dependency | Current state | Parent can rely on | Parent caution |
|---|---|---|---|
| Paper / canary / live policy | L1 policy present | Sandbox / paper-account broker API smoke is valid pre-canary work | Do not treat sandbox smoke as production live approval |
| Existing broker paper sidecar | Present under `services/broker/` | Gate, error payload, simulated order invariants, and test style | Do not mutate the existing paper behavior unless parent scope explicitly requires it |
| Existing Shioaji execution adapter | Present under `services/execution/` | TW symbol normalization and Shioaji payload vocabulary | Do not move this execution boundary into canonical truth changes from the sidecar slice |
| Broker sandbox order smoke runner | Present and reviewed through P2 broker sandbox work | Packet vocabulary for auth/account/place/cancel/readback/execution/telemetry/reconciliation | New adapter should complement this runner; it should not loosen the production-live rejection rules |
| Runtime manager canary/live gate | Contract and service enforce gate fields | `broker_sandbox_smoke_ref` is the later consumer of Shioaji sandbox evidence | Parent should prepare the ref path only; risk-owner and operator approvals remain separate |

### 5.2 Downstream Consumers

| Consumer | Relationship to parent output |
|---|---|
| `EP5-BROKER-TW-001` reviewer (`Codex2`) | Uses this checklist to confirm the Shioaji adapter is fail-closed, testable, and evidence-ready |
| `scripts/run_ep5_canary_readiness.py emit-human-gate-packet` | Later packet flow can cite the parent-produced Shioaji sandbox smoke evidence path |
| Runtime manager canary activation | Requires `broker_sandbox_smoke_ref` plus human gate, risk-owner approval, operator approval, and scale limits |
| Future EP5 canary / live proof task | Consumes sandbox evidence but must still get explicit human approval before real capital / real order proof |

### 5.3 Board Dependency Note

`ai-status.json` shows the parent with no machine-readable `depends_on`. The map
above is therefore an execution and review dependency map, not a request to edit
task state. The parent remains unblocked, but its closeout should preserve the
policy and evidence boundaries listed here.

## 6. Suggested Parent Verification Commands

Run from `/home/lupin/code/pantheon` after the parent implementation lands.
These commands are intended for the parent owner / reviewer, not this support
sidecar:

```bash
python3 -m pytest \
  services/broker/test_broker.py \
  services/execution/test_shioaji_adapter.py \
  scripts/test_run_broker_sandbox_order_smoke.py \
  services/broker/shioaji/test_adapter.py

python3 scripts/run_broker_sandbox_order_smoke.py \
  --provider shioaji \
  --mode simulation \
  --symbol 2330.TWSE \
  --side buy \
  --quantity 1 \
  --limit-price 950 \
  --account-ref shioaji-simulation-account-ref \
  --credential-ref secret://shioaji-simulation \
  --output-dir /tmp/pantheon/ep5-broker-tw-001/shioaji-smoke

python3 scripts/run_broker_sandbox_order_smoke.py \
  --provider shioaji \
  --mode live \
  --symbol 2330.TWSE \
  --side buy \
  --quantity 1 \
  --limit-price 950 \
  --output-dir /tmp/pantheon/ep5-broker-tw-001/live-reject-check
```

Expected disposition:

- the test suite passes without raw credentials or network side effects,
- Shioaji simulation smoke writes structured packet JSON,
- the production-live smoke command exits non-zero before any payload that could
  imply live order side effects is generated.

If the parent adds repo-local smoke artifacts, also run a secret-pattern scan
over only those parent-owned artifacts:

```bash
rg -n "(AKIA|ASIA|api[_-]?key\\s*[:=]|secret\\s*[:=]|private[_-]?key|-----BEGIN|password\\s*[:=]|token\\s*[:=])" \
  services/broker/shioaji support/sidecars/EP5-BROKER-TW-001 docs/deployment/evidence
```

Any hit that contains real credential material should block approval. Hits in
field names such as `credential_ref` or documented redacted placeholders may be
acceptable only when clearly non-secret.

## 7. Review Guardrails

| Reviewer should reject | Reason |
|---|---|
| Adapter defaults to enabled or reaches SDK submit/cancel when `BROKER_SHIOAJI_SANDBOX_ENABLED` is unset | Parent acceptance requires fail-closed default |
| Any production live, real-money, or capital-moving path is reachable | Current sprint objective keeps broker production live and capital binding fail-closed |
| Raw API keys, certificates, passwords, or tokens are written to docs, evidence, tests, or status | Repo artifacts may carry only secret refs / placeholders |
| Parent modifies L1 policy, runtime-manager contract truth, registry truth, or governance implementation to make the adapter pass | This task is a bounded broker sidecar scaffold, not canonical truth work |
| Shioaji sandbox evidence is described as canary/live proof | Sandbox / simulation smoke is readiness evidence only |
| Existing `services/execution/shioaji_adapter.py` semantics are contradicted without a separate owner-approved contract task | Execution adapter boundary already exists and should remain stable unless explicitly assigned |
| Parent omits cancel or get-status/readback behavior | Parent title and sprint objective require place/cancel/readback/reconcile support, not submit-only scaffolding |

## 8. Reviewer Checklist

| Check | Status | Evidence |
|---|---|---|
| Support artifact only | PASS | This sidecar creates only `support/sidecars/EP5-BROKER-TW-001/EP5-BROKER-TW-001-SIDECAR-ACCEPTANCE.md` |
| No canonical truth edited | PASS | No L1 policy docs, contract docs, runtime registry, governance implementation, or broker runtime code are modified by this sidecar |
| Parent acceptance is concrete | PASS | Section 4 maps each parent acceptance criterion to required evidence and review pass conditions |
| Dependency map is localized | PASS | Section 5 names existing broker, Shioaji, sandbox-smoke, and runtime-gate surfaces without requesting task-state mutation |
| Fail-closed and no-real-capital boundary is explicit | PASS | Sections 1, 4, 6, and 7 keep production live and capital side effects rejected |
| Human-gate handoff remains separate | PASS | Packet says sandbox evidence can feed `broker_sandbox_smoke_ref`, not that human/risk/operator gates are approved |

## 9. Handoff to Reviewer (`Claude`)

This sidecar is ready for review as the acceptance / dependency packet for
`EP5-BROKER-TW-001`.

What it gives you:

1. a parent acceptance matrix grounded in current repo files,
2. a dependency map separating the existing execution Shioaji payload boundary
   from the new broker-side adapter package,
3. review guardrails for the fail-closed sandbox default, no raw secrets, and
   no production live side effects, and
4. suggested parent verification commands that preserve the non-production
   evidence boundary.

Recommended reviewer stance:

1. approve this sidecar if the packet accurately reflects the parent scope and
   current repo truth,
2. keep the parent owner responsible for creating `services/broker/shioaji/`
   and deciding exactly how to stage any smoke evidence, and
3. reject any parent closeout that claims canary/live proof, enables production
   live behavior, stores raw Shioaji credentials, or omits cancel/status
   readback coverage.

---
*Generated by Codex as a sidecar `acceptance_packet` helper for
`EP5-BROKER-TW-001`. This file is a support artifact and does not modify
canonical truth.*
