# Execution Telemetry: Fill and Slippage Capture Strategy

## Overview

This document describes how the telemetry capture module (`services/telemetry/`) 
handles fill and slippage observations, which are critical metrics for evaluating 
execution quality and strategy performance across paper and live trading modes.

## Definitions

### Fill (Fill Observation)

A **fill** represents an actual order execution with:
- **Quantity**: The number of shares or units actually filled
- **Price**: The actual execution price at which the fill occurred
- **Timestamp**: When the fill was recorded

Fills are the ground truth for what actually executed, as opposed to what was intended.

#### Fill Event Structure

```json
{
  "event_type": "fill_observation",
  "execution_mode": "paper|live",
  "metrics": {
    "fill_quantity": 1000.0,
    "fill_price": 50.25
  },
  "signal_id": "sig_123",       // The signal that triggered this order
  "run_id": "run_456",          // The execution run
  "broker": "interactive_brokers",
  "account_ref": "acc_001"
}
```

### Slippage (Slippage Observation)

**Slippage** measures the difference between:
- **Expected execution price**: The price at order submission time
- **Actual execution price**: The price at which the order filled

Slippage is expressed in **basis points (bps)**, where:
- 1 bps = 0.01% price difference
- 10 bps = 0.1% price difference
- 100 bps = 1% price difference

Slippage is typically caused by:
- Market movement between order submission and execution
- Liquidity constraints (large orders moving market price)
- Broker/venue processing delays
- Bid-ask spread on entry/exit

#### Slippage Event Structure

```json
{
  "event_type": "slippage_observation",
  "execution_mode": "paper|live",
  "metrics": {
    "slippage_bps": 2.5
  },
  "signal_id": "sig_123",
  "run_id": "run_456",
  "broker": "interactive_brokers"
}
```

## Capture Strategy

### 1. Integration with Executor

The execution module (`services/execution/lean_runtime/executor.py`) places orders 
via the LEAN runtime. Telemetry capture hooks into the execution pipeline:

```python
from services.telemetry import TelemetryCapture, ExecutionMode

# Initialize capture (typically in algorithm initialization)
capture = TelemetryCapture(
    schema_path="services/feedback/schema/execution_telemetry_event.schema.json",
    storage_dir="telemetry_events"
)

# When order is placed (in executor)
capture.capture_fill(
    mode=ExecutionMode.PAPER,  # or LIVE
    strategy_id="momentum_strategy",
    fill_quantity=100.0,
    fill_price=50.25,
    signal_id=signal["signal_id"],
    run_id=execution_run_id,
    broker="interactive_brokers"
)
```

### 2. Paper vs Live Separation

The telemetry module maintains strict separation between modes:

```python
# Paper trading events
paper_events = capture.get_paper_events()

# Live trading events
live_events = capture.get_live_events()

# Query by mode
paper_fills = [e for e in paper_events if e["event_type"] == "fill_observation"]
```

This separation allows:
- Evaluators to benchmark paper performance independently
- Evolution plane to tag events with promotion state (candidate→paper→live)
- Feedback to be correlated with actual execution state

### 3. Slippage Calculation

Slippage should be calculated at order fill time:

```python
# Example: Calculate slippage from expected vs actual
expected_price = signal["metadata"]["price_at_signal"]
actual_price = fill_price

# Calculate basis points
price_diff = abs(actual_price - expected_price)
price_diff_pct = (price_diff / expected_price) * 100
slippage_bps = price_diff_pct * 100

capture.capture_slippage(
    mode=mode,
    strategy_id=strategy_id,
    slippage_bps=slippage_bps,
    signal_id=signal_id
)
```

### 4. Linking to Feedback Store

The `FeedbackStoreAdapter` links captured telemetry to the feedback store:

```python
from services.telemetry import FeedbackStoreAdapter

adapter = FeedbackStoreAdapter()

# Ingest events with promotion state context
for event in capture.get_events():
    adapter.ingest_telemetry_event(
        event=event,
        strategy_id="momentum_strategy",
        promotion_state="paper"  # or "live", "candidate", "retired"
    )

# Query correlations between fills and trader feedback
correlated = adapter.correlate_with_feedback(fill_event, feedback_events)
```

## Evolution Plane Integration

### Evaluators

The evolution plane's evaluators can now:

1. **Assess execution quality** by analyzing fills:
   ```python
   fills = adapter.get_telemetry_for_strategy("strategy_id", mode="live")
   avg_fill_price_deviation = calculate_deviation(fills)
   ```

2. **Calculate slippage impact** on returns:
   ```python
   slippages = [e for e in events if e["event_type"] == "slippage_observation"]
   total_slippage_cost = sum(e["metrics"]["slippage_bps"] for e in slippages)
   ```

3. **Compare paper vs live** execution:
   ```python
   paper = adapter.get_telemetry_for_strategy(strat_id, mode="paper")
   live = adapter.get_telemetry_for_strategy(strat_id, mode="live")
   # Compare fill prices, slippage distributions
   ```

### Feedback Integration

Trader feedback can be correlated with execution events:

```python
# When trader approves or rejects a trade
trader_feedback = {
    "event_type": "approve",
    "target": {"strategy_id": "strat_id"},
    "created_at": "2026-04-06T15:00:00Z"
}

# Find related fills and slippage
correlation = adapter.correlate_with_feedback(fill_event, [trader_feedback])
# Result includes time delta, related feedback events
```

## Storage and Persistence

### In-Memory Buffering

Events are buffered in memory by execution mode:

```python
capture.events[ExecutionMode.PAPER]   # List of paper events
capture.events[ExecutionMode.LIVE]    # List of live events
```

### Persistent Storage

Events are optionally written to disk in a structured hierarchy:

```
telemetry_events/
├── paper/
│   ├── evt_001.json
│   ├── evt_002.json
│   └── ...
└── live/
    ├── evt_003.json
    ├── evt_004.json
    └── ...
```

Each file contains a complete telemetry event with schema validation.

### Export

The adapter can export the entire telemetry log:

```python
# JSONL format (one event per line)
adapter.export_telemetry("telemetry.jsonl", format="jsonl")

# JSON format (array of events)
adapter.export_telemetry("telemetry.json", format="json")
```

## Acceptance Criteria

✓ **Execution telemetry schema linked to feedback store**
  - `execution_telemetry_event.schema.json` in `services/feedback/schema/`
  - `TelemetryCapture` validates all events against schema
  - `FeedbackStoreAdapter` ingests into feedback store with lineage

✓ **Paper and live telemetry distinguished**
  - `ExecutionMode` enum enforces paper/live separation
  - Events stored in separate buffers and directories
  - Adapter queries support mode filtering
  - Promotion state tracks progression (candidate→paper→live)

✓ **Fill and slippage capture documented**
  - `capture.capture_fill()` records quantity and price
  - `capture.capture_slippage()` records basis points
  - Integration guide above shows calculation and usage
  - Examples demonstrate correlation with trader feedback

## Testing

Run the full test suite:

```bash
cd services/telemetry/
python3 -m unittest discover -s . -p 'test_*.py'
```

Run smoke test:

```bash
cd services/telemetry/
python3 smoke_test.py
```

## Future Enhancements

1. **Real-time Aggregation**: Calculate running averages of slippage and fill quality
2. **Alerting**: Trigger alerts if slippage exceeds thresholds
3. **Backpressure**: Handle high-frequency event streams efficiently
4. **State Machine**: Track progression of orders (placed→filled→settled)
5. **Attribution**: Decompose PnL into components (signal quality, execution quality)
