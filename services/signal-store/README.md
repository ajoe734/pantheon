# Signal Store Contract

This directory locks `P1-001` for the Pantheon collaboration sprint.

## Purpose

`SignalStoreClient` is the shared transport contract between:

- research workers that write signals
- the LEAN execution plane that reads them
- control-plane services that inspect them

The contract is intentionally narrow:

- accept the full shared signal payload
- validate the write boundary
- reject duplicate `signal_id`
- return stored payloads unchanged
- support lookups by `signal_id` and `run_id`

The transport layer now also tolerates schema aliases while the shared signal
format is still converging:

- `schema_version` or `version`
- `ts` or `timestamp`
- structured `symbol` object or flat `symbol` string
- `size` or `quantity`

It is intentionally not responsible for:

- broker execution
- symbol translation into LEAN objects
- confidence-to-size conversion
- governance approvals
- consumer-specific payload enrichment

## Locked Assumptions

The schema draft in `services/research/signal_schema_v1.md` depended on four store assumptions.
After reviewing Gemini's current `services/research/schema.json`, the store confirms
three assumptions and narrows one:

| ID | Status | Decision |
|---|---|---|
| `A-1` | confirmed | `write_signal(payload)` accepts the full signal object defined by the shared schema. |
| `A-2` | confirmed | `signal_id` is the idempotency key. Duplicate writes raise `DuplicateSignalError`. |
| `A-3` | store-ready, schema-pending | Query-by-`run_id` is a first-class store capability through `get_run_signals()` and `list_signals(SignalQuery(run_id=...))`, but Gemini's current `schema.json` omits `run_id`, so atomic rebalance grouping is still blocked at the schema layer. |
| `A-4` | confirmed | Store metadata lives in `SignalRecord`; the `payload` returned to consumers is the exact stored signal object, not a transformed fork. |

## Contract Surface

Base methods from [`client.py`](/home/ajoe734/code/pantheon/services/signal-store/client.py):

```python
class SignalStoreClient(ABC):
    def write_signal(self, payload: Mapping[str, JSONValue]) -> SignalRecord: ...
    def get_signal(self, signal_id: str) -> SignalRecord | None: ...
    def list_signals(self, query: SignalQuery | None = None) -> list[SignalRecord]: ...
    def get_run_signals(self, run_id: str, *, newest_first: bool = False) -> list[SignalRecord]: ...
```

`SignalRecord` keeps transport metadata next to the payload:

- `signal_id`
- `schema_version`
- `strategy_id`
- `signal_timestamp`
- `run_id`
- `source_worker`
- `payload`
- `ingested_at`
- `status`

That separation is deliberate. It lets the store add ingestion metadata without mutating the signal fields shared across planes.

## Validation Strategy

The base contract uses `validate_signal_payload_minimal()` to guard the transport boundary:

- required business identifiers exist
- `schema_version/version` is present
- `ts/timestamp` is present
- `symbol` is present as either a string or structured object
- `size/quantity` is numeric and non-negative

This is the minimum store contract, not the full schema.
Production implementations should compose it with the canonical validator from [schema.json](/home/ajoe734/code/pantheon/services/research/schema.json) instead of duplicating every enum rule in a second place.

## Example Payload

Current schema example lives in [signal_example.json](/home/ajoe734/code/pantheon/services/research/signal_example.json).
The richer multi-plane draft scenarios remain in [example_payload.json](/home/ajoe734/code/pantheon/services/research/example_payload.json).

Minimal example accepted by the store:

```json
{
  "version": "1.0",
  "signal_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "timestamp": "2026-04-01T14:30:00Z",
  "strategy_id": "momentum-us-eq-v1",
  "symbol": "AAPL.US",
  "action": "BUY",
  "quantity": 10
}
```

## Downstream Consumer Notes

### For Gemini (`P2-001`)

- The store contract accepts the full schema payload as-is, so schema design does not need a store-specific wrapper object.
- The store now tolerates alias pairs (`version/schema_version`, `timestamp/ts`, `quantity/size`) while schema convergence is still in flight.
- `run_id` remains queryable at the store layer, but the current schema file must add it back if portfolio-level batch semantics are still required.
- Storage metadata is kept outside `payload`, which protects schema stability.
- If the schema adds optional fields in `1.0.x` or `1.1.x`, the store contract does not need to change as long as `payload` remains a JSON object.

### For Claude (`P3-001` / `P4-001`)

- LEAN and control-plane consumers should read `record.payload`, not infer meaning from storage metadata.
- Duplicate-signal handling is store-side idempotency, not execution logic; consumers should treat a missing second write as expected behavior.
- Rebalance flows should group by `run_id`, using `get_run_signals(run_id)` when a full batch view is required. If `run_id` remains absent from the schema, that capability is unavailable by design rather than by store limitation.
- The store does not reorder payload timestamps. If consumers care about signal time, they should sort or compare `payload["ts"]` or `payload["timestamp"]` explicitly.

## Failure Modes

| Failure | Store behavior |
|---|---|
| Missing required field | raise `SignalValidationError` |
| Duplicate `signal_id` | raise `DuplicateSignalError` |
| Unknown `signal_id` on read | return `None` from `get_signal()` |
| Schema uses alias field names | accept via transport aliases |
| Invalid query limit | raise `ValueError` from `SignalQuery` |
| Caller mutates original payload after write | stored copy remains unchanged |

## Versioning and Extension Points

Base contract stability rules:

- additive query filters are allowed
- additive `SignalRecord` metadata is allowed
- the meaning of `write_signal`, `get_signal`, `list_signals`, and `get_run_signals` is locked for v1

Preferred extension path:

1. Keep the base client read/write/query contract stable.
2. Add optional companion interfaces for advanced behavior such as leasing, acknowledgements, or delivery receipts.
3. Keep shared signal semantics in the schema, not in store-specific metadata fields.

## Intentionally Out of Scope for `P1-001`

- stream subscriptions
- distributed locks or claim/lease semantics
- execution receipts
- retry policy
- dead-letter queues
- broker-specific order shaping

Those can be added later as separate interfaces without breaking the base transport contract.
