# Pantheon Foundation Package

`services/foundation` is the canonical Python package for Pantheon shared
foundation primitives.

SD-00 names the singular path `services/foundation`; SD-12's draft module list
used `services/foundations`. For implementation and imports, normalize on the
singular package and import it as:

```python
from services.foundation import TraceContext, CommandEnvelope
```

## Boundary

This package owns side-effect-free value objects and deterministic helpers for
cross-plane command, event, audit, policy, idempotency, error, and secret-ref
contracts. It must remain safe to import from BFF, runtime-manager, telemetry,
governance, and tests without starting network clients, database connections,
or framework runtimes.

In scope for this boundary:

- `TraceContext`
- `CommandEnvelope`
- `ErrorEnvelope`
- `IdempotencyRecord`
- `PolicyDecision`
- `AuditAction`
- `SecretRef`
- `EventEnvelope`
- `OutboxRecord`
- `InboxReceipt`
- `DeadLetterEntry`
- `DeadLetterQueue`
- `SchemaRegistryEntry`
- `SchemaRegistry`
- `DeadLetterReplayProcessor`
- supporting refs and enums such as `ActorRef`, `EnvironmentScope`, and
  `AuthorityScope`
- deterministic canonical JSON and SHA-256 checksum helpers
- optional append-only JSONL helpers for shared outbox / DLQ records

Out of scope for this boundary:

- database, broker, or network-backed durable storage for traces, audit rows,
  idempotency records, outbox, or DLQ
- policy-engine execution beyond serializable `PolicyDecision` records
- raw secret resolution or secret value transport
- service-specific HTTP middleware and command handlers

Those adoption and persistence paths belong to later SD-FND tasks.

The shared outbox / DLQ helpers are deliberately storage-light. They provide
record shapes, deterministic serialization, optional local JSONL append/read
helpers, schema-registry validation, and audited idempotent replay primitives.
Service-owned stores such as telemetry ingest remain authoritative for their
domain-specific buffering, retry, and DLQ policies.
