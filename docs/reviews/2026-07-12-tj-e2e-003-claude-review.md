# TJ-E2E-003 Claude Review

Reviewer: Claude
Date: 2026-07-12
Disposition: changes requested (round 2)

## Round 2 Update (2026-07-12)

Reviewed commits `8a1a94ad6` (anchor downstream envelope propagation),
`56f4a464f` (verify broker envelope boundary) and `bd21f62b0` (test remaining
envelope producers) against the round-1 required changes below.

Round-1 items 1, 3, 4, 6 (partial), 7 are addressed and verified as real,
wired call paths, not just isolated unit calls:

- `executor.py:_signal_context_metadata()` now copies `correlation_envelope`
  into the order/signal context (`_ORDER_ADAPTER_CONTEXT_KEYS`).
- That context is stored via `PaperExecutionAlgorithm.SetCurrentSignalContext`
  as `_current_signal_metadata`, which `_publish()` merges into every
  `OrderEvent.metadata`, which `_handle_order_event()` merges into
  `telemetry_metadata`, which `RuntimeTelemetryEmitter.build_event()` reads
  back out and re-propagates via `propagate_envelope(..., producer=
  "execution.paper_runtime")`. Traced end to end in the source, this is a real
  production call chain, not a test-only simulation.
- `services/capital/risk_policy.py`'s `RiskPolicyEvaluator.evaluate()` now
  propagates the incoming envelope into `RiskPolicyEvaluation.correlation_envelope`
  and `to_dict()`.
- `services/reconciliation-drift/consumer.py:build_drift_report_from_event()`
  now propagates `correlation_envelope` when present on the source event.
- New unit tests exercise the real functions directly (`_signal_context_metadata`,
  `RuntimeTelemetryEmitter.build_event`, `RiskPolicyEvaluator.evaluate`,
  `build_drift_report_from_event`) rather than only calling
  `propagate_envelope()` in the test body — this fixes the round-1 test-fidelity
  complaint for these four producers.
- The delivered-contract doc update accurately describes what changed.

Verification: full focused suite passes.

```
python3 -m pytest -q \
  services/control-plane/bff/test_trade_journey_correlation_envelope.py \
  services/execution/lean_runtime/test_signal_producer.py \
  services/execution/lean_runtime/test_executor.py \
  services/execution/lean_runtime/test_paper_runtime.py \
  services/broker/sinopac/test_adapter.py \
  services/capital/test_risk_policy.py \
  services/reconciliation-drift/tests/test_reconciliation_drift_consumer.py \
  services/control-plane/bff/test_trade_journey_contract.py
# 122 passed
```

## Round 2 Blocking Finding: Broker Boundary Wired To The Wrong Adapter

Round 1 named `services/broker/sinopac/adapter.py` (`ShioajiBrokerAdapter`) as
"the" broker adapter per TJ-E2E-001's inventory, and commit `8a1a94ad6` made
its `submit()` accept and propagate `client_order_id` +
`correlation_envelope`. That change is real and the new unit test
(`test_adapter.py::TestCorrelationEnvelope`) proves the library-level logic is
correct.

However, `ShioajiBrokerAdapter` is **not on the real signal-driven paper
order path** in this codebase:

- Grep confirms `ShioajiBrokerAdapter` / `services.broker.sinopac.adapter` is
  never imported anywhere under `services/execution/` or `services/capital/`.
  Its only callers are `services/broker/sinopac/facade.py`
  (`ShioajiSandboxFacade.place_test_order`, a manual/OODA test-order facade
  that does not pass `client_order_id`/`correlation_envelope`),
  `services/broker/sinopac/sandbox_smoke.py`, and its own test file.
- The actual production Taiwan paper-order path is
  `executor.py:_execute_taiwan()` → `algo.SubmitTaiwanBrokerOrder()`
  (`paper_runtime.py:244-291`), which builds a plain `payload` dict
  (`capital_pool_id`, `strategy_id`, `symbol`, `qty`, `side`, `order_type`,
  optional `limit_price` — no `signal_id`, no `client_order_id`, no
  `correlation_envelope`) and POSTs it via
  `PANTHEON_BROKER_PAPER_URL` to `services/broker/main.py:submit_paper_order()`.
- That endpoint's `PaperOrderRequest` model, `simulate_paper_order()` and
  `PaperSimulationStore` (`services/broker/paper_simulation.py`) have no
  `client_order_id` or `correlation_envelope` field at all. The broker
  sidecar's own persisted order record for a real paper trade still has no
  journey correlation.
- The one executable "broker-sandbox lifecycle" proof named in TJ-E2E-001
  (`tests/e2e/test_shioaji_adapter_filled_readback_memory_e2e.py`) does call
  `ShioajiBrokerAdapter.submit()` directly, but it was not updated to pass
  `client_order_id`/`correlation_envelope`, so even that proof path does not
  yet demonstrate the envelope surviving broker submit → readback → memory
  writeback.

Net effect: the fill/telemetry side of a paper trade now carries the
envelope (via the executor → `_current_signal_metadata` → telemetry chain
verified above), but the broker sidecar's own order record — the thing a
human or reconciliation job would actually query by `order_id` — still has no
journey/correlation identity for the flow that real signals use today. The
acceptance bar ("no manual join from signal to reconciliation") is not fully
met yet because the two broker-adjacent surfaces (`sinopac/adapter.py` vs.
`broker/main.py` + `paper_simulation.py`) are architecturally distinct and
only one of them — the one not on the live path — was wired.

## Required Changes (round 2)

1. Add `client_order_id` and `correlation_envelope` to
   `services/broker/main.py`'s `PaperOrderRequest`, thread them into
   `simulate_paper_order()` and `PaperOrder`
   (`services/broker/paper_simulation.py`), using `propagate_envelope` with
   a `producer` such as `"broker.paper_sidecar"`.
2. Update `paper_runtime.py:SubmitTaiwanBrokerOrder()` to include
   `signal_id`-derived `client_order_id` and `correlation_envelope` (from
   `self._current_signal_metadata`) in the HTTP payload it posts to the
   broker sidecar.
3. Either wire `ShioajiSandboxFacade.place_test_order()` to pass through an
   optional envelope too, or note in the delivered-contract doc that it is
   an unwired manual test path by design — do not leave the doc implying the
   Shioaji adapter change covers the live paper-order broker boundary when it
   does not today.
4. Update `tests/e2e/test_shioaji_adapter_filled_readback_memory_e2e.py` (or
   add a focused sidecar test) to prove `client_order_id` +
   `correlation_envelope` survive submit → readback for the real broker
   sidecar path (`broker/main.py` + `paper_simulation.py`), not only the
   disconnected `ShioajiBrokerAdapter` direct-call path.

Everything else from round 1 (executor signal-context copy, risk evaluation,
paper telemetry, reconciliation-drift) is accepted and does not need rework.
The reconciliation-drift wiring is accepted as the closest existing analog;
TJ-E2E-001's separate note about a net-new "trade reconciliation producer
with `reconciliation_id`, compared ledger/broker refs" describes work that
does not exist anywhere in the codebase yet under any name, and building that
from scratch is out of scope for an envelope-propagation task — flagging it
here as a gap for a future task, not as a blocker on TJ-E2E-003.

## Scope Reviewed

- `services/control-plane/specs/trade_journey/correlation_envelope.py`
- `services/trade_journey/__init__.py`
- `services/trade_journey/correlation_envelope.py`
- `services/execution/lean_runtime/signal_producer.py`
- `services/control-plane/bff/test_trade_journey_correlation_envelope.py`
- `docs/bff/execution-tasks/2026-07-11-trade-journey-e2e/TJ-E2E-003-correlation-envelope-propagation.md`
- commits `0301ac4a8` (anchor) and `d3975601f` (finalize)
- dependency input: `docs/04/pantheon_trade_journey_e2e_observability_gap_2026-07-11/TJ-E2E-001-PRODUCER-CORRELATION-INVENTORY.md`

The `trade-journey-envelope/1` contract module itself is solid: `mint_trade_envelope`,
`propagate_envelope`, `assert_no_field_loss` and `validate_envelope` correctly mint one
`journey_id` at the signal boundary, reject schema drift, and fail closed on stable-field
loss or a broken causal chain (`causation_event_id` must equal the upstream `event_id`).
The 19 new/exercised contract tests and the 23 existing e2e tests all pass:

```
python3 -m pytest -q services/control-plane/bff/test_trade_journey_correlation_envelope.py \
  services/execution/lean_runtime/test_signal_producer.py \
  services/control-plane/bff/test_trade_journey_contract.py
# 19 passed

python3 -m pytest -q tests/e2e/test_allocation_policy_to_paper_run.py \
  tests/e2e/test_shioaji_adapter_filled_readback_memory_e2e.py
# 23 passed, 4 skipped
```

`DecisionSignalProducer` / `build_decision_signals` correctly mints or validates the
envelope at the signal boundary (`services/execution/lean_runtime/signal_producer.py:170-186`).
That half of the task is done well.

## Blocking Finding

The task title and required work are "**Propagate** the versioned correlation envelope
through **every P0 producer from strategy/signal origin to broker, ledger and
reconciliation**", and TJ-E2E-003's acceptance bar in the source gap spec (§16) is:

> 驗收：新 paper flow 從 signal 到 recon 無人工 join；缺欄位會被 contract test 擋下
> (Acceptance: a new paper flow requires no manual join from signal to reconciliation;
> missing fields are blocked by a contract test.)

TJ-E2E-001 (this task's own approved dependency) explicitly names the concrete production
files each downstream producer must be changed in:

- risk: "Emit one immutable risk evaluation event with policy/version, checks, terminal
  result and the incoming journey envelope."
- execution router: "Copy full envelope into order tags/context and every execution
  callback."
- broker adapter (`services/broker/sinopac/adapter.py`): "Submit API does not accept the
  envelope or client order ID. Require `client_order_id`, journey envelope and attempt ID."
- ledger / reconciliation: "Emit immutable `ledger_entry_id` ... and envelope" /
  "Add trade reconciliation producer with `reconciliation_id`, compared ledger/broker
  refs, terminal status, revisions and envelope."

None of that happened. Grepping the whole tree for the new module outside tests and the
three new files shows only `services/execution/lean_runtime/signal_producer.py` importing
it:

```
grep -rl "propagate_envelope\|correlation_envelope" services/ --include="*.py" | grep -v test_
# services/control-plane/specs/trade_journey/correlation_envelope.py
# services/execution/lean_runtime/signal_producer.py
# services/trade_journey/correlation_envelope.py
```

Concretely, the envelope minted at the signal boundary is dropped before it reaches any
downstream producer:

- `services/execution/lean_runtime/executor.py:_signal_context_metadata()` (~line
  286-331) and `_ORDER_ADAPTER_CONTEXT_KEYS` (~line 54-90) build the order context copied
  from the signal into `SetCurrentSignalContext`. Their key allow-list does not include
  `correlation_envelope`, so the field set by `signal_producer.py` never reaches the
  broker boundary.
- `services/broker/sinopac/adapter.py:submit()` (line 459) mints its own `order_id =
  uuid.uuid4().hex` and has no `client_order_id`/envelope parameter; `ShioajiOrder` has no
  journey/correlation/trace field.
- `services/execution/lean_runtime/paper_runtime.py:RuntimeTelemetryEmitter.build_event()`
  (~line 760-845), which produces the payload that stands in for ledger/telemetry today,
  only sets a separate, pre-existing `trace_id` sourced from `RuntimeIdentity`
  (`PANTHEON_TRACE_ID`), not `correlation_envelope`.
- `services/capital/risk_policy.py` (`RiskPolicyEvaluation.to_dict()`, ~line 276-393)
  carries its own ad hoc `trace_id` fallback, not the new envelope.
- `services/reconciliation-drift/consumer.py` and
  `services/execution/runtime-manager/paper_fleet_reconciler.py` reference neither
  `correlation_envelope` nor `journey_id`.

The new contract test
(`test_correlation_survives_p0_terminal_paths_without_temporal_join`) calls
`envelopes.propagate_envelope(...)` directly inside the test body to *simulate*
`risk.accepted` / `broker.accepted` / `ledger.booked` / `reconciliation.completed` — it
proves the library is correct, not that any real producer in the codebase calls it. There
is no production code path today where a paper signal's envelope survives to risk,
broker, ledger, or reconciliation, so the stated acceptance bar ("no manual join from
signal to recon") is not met, and TJ-E2E-004 (materializer, which depends on this task)
would have nothing but the signal event to index.

## Required Changes

1. Copy `correlation_envelope` through the signal → order context path: add it to
   `executor.py`'s copied signal keys (`_ORDER_ADAPTER_CONTEXT_KEYS` /
   `_signal_context_metadata()`), so it reaches `SetCurrentSignalContext` and order
   events.
2. Accept and propagate the envelope at the broker boundary
   (`services/broker/sinopac/adapter.py:submit()`): add `client_order_id` +
   envelope-carrying fields to `ShioajiOrder`, using `propagate_envelope` with
   `producer="broker.sinopac"`.
3. Emit the envelope on the risk evaluation payload
   (`services/capital/risk_policy.py`, `RiskPolicyEvaluation`), using
   `propagate_envelope` with `producer="risk.evaluation"`.
4. Carry the envelope through order/fill telemetry that stands in for ledger booking
   today (`paper_runtime.py:RuntimeTelemetryEmitter.build_event()`), alongside or
   replacing the current ad hoc `trace_id`.
5. Wire the reconciliation producers (`reconciliation-drift/consumer.py`,
   `paper_fleet_reconciler.py`) to read and propagate the envelope on their emitted
   records.
6. Extend or add a contract test that exercises the real production call path
   (`signal_producer` → `executor` → `paper_runtime`/broker adapter → reconciliation)
   instead of only calling `propagate_envelope()` directly in the test, so a future
   change that drops the envelope at a real boundary fails CI.
7. Update the delivered-contract doc
   (`docs/bff/execution-tasks/2026-07-11-trade-journey-e2e/TJ-E2E-003-correlation-envelope-propagation.md`)
   to describe what is wired versus library-only, and update the verification command to
   prove the "no manual join, signal to recon" acceptance claim against a real flow.

The mint-point integration and the shared `trade-journey-envelope/1` library are a good
foundation and do not need to be redone; the gap is that "propagation" stopped at the
signal boundary instead of reaching broker, ledger, and reconciliation as the task's own
title and acceptance bar require.

## Verification

```
python3 -m pytest -q services/control-plane/bff/test_trade_journey_correlation_envelope.py \
  services/execution/lean_runtime/test_signal_producer.py \
  services/control-plane/bff/test_trade_journey_contract.py
# 19 passed

python3 -m pytest -q tests/e2e/test_allocation_policy_to_paper_run.py \
  tests/e2e/test_shioaji_adapter_filled_readback_memory_e2e.py
# 23 passed, 4 skipped

grep -rl "propagate_envelope\|correlation_envelope" services/ --include="*.py" | grep -v test_
# only signal_producer.py and the two new contract-library files
```
