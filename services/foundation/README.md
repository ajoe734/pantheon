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
- supporting refs and enums such as `ActorRef`, `EnvironmentScope`, and
  `AuthorityScope`
- deterministic canonical JSON and SHA-256 checksum helpers

Out of scope for this boundary:

- durable storage for traces, audit rows, idempotency records, outbox, or DLQ
- policy-engine execution beyond serializable `PolicyDecision` records
- raw secret resolution or secret value transport
- service-specific HTTP middleware and command handlers

Those adoption and persistence paths belong to later SD-FND tasks.
