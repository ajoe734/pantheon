# PFG-AGORA-RECON-WORKER-20260820 implementation evidence

This directory records the owner-side implementation evidence for wiring
Workshop conversation to the existing Strategy Reconstruction engine through
a durable worker. It is a task-scoped record only: it does not change the
reconstruction algorithm, the frontend, or Registry authority.

Before this task, `reconstruct_strategy_from_events` had exactly one caller —
a synchronous `/reconstruct` HTTP handler that returned the engine result and
persisted nothing. Message posting and reconstruction were entirely
disconnected.

This task adds `services/control-plane/bff/agora/strategy_workshop/runner.py`
(`run_reconstruction_worker`) as the single worker path both the message-post
handler and the `/reconstruct` endpoint now call. It persists exactly one
effective result per workshop by reusing the existing dual-backend
`record_workshop_card` upsert primitive (no new tables), so replay, a stale
prior result, and a crash mid-flight all converge on the same effective
result and Next-Best-Question. When the workshop already has an active
StrategySpec, the worker also creates or updates a canonical Registry draft
via the existing `WorkshopCanonicalOperations.create_strategy_spec` call,
annotated with reconstruction lineage rather than a synthesized document.

See `evidence.json` for the full caller inventory, code disposition, and
validation evidence. The engine itself
(`reconstruction.py:reconstruct_strategy_from_events`) is unchanged.
