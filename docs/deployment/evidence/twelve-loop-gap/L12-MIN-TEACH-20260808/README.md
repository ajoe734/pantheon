# L12-MIN-TEACH-20260808 evidence

This task extends the existing `training-session` preview/evaluation worker; it
does not add a second Teaching controller. A queued preview job may explicitly
set `terminalize_session=true`. After a successful evaluation, the worker then:

1. calls the existing session completion route;
2. reads the session back through the existing session route;
3. accepts the result only when the persisted identity, terminal status, and
   `ended_at` are present; and
4. emits the stable identifier in `terminal_session_ids` for the downstream
   Learning verifier.

Normal preview jobs default `terminalize_session` to false, so interactive
trainer previews do not close their sessions.

## Bounded real-framework proof

From the repository root, with the checkout-scoped Python distribution
provisioned:

```bash
.venv-pantheon/bin/python -m pip install -r services/research/vectorbt/requirements.txt
.venv-pantheon/bin/python docs/deployment/evidence/twelve-loop-gap/L12-MIN-TEACH-20260808/real_vectorbt_probe.py
```

The captured result is in `real-vectorbt-terminal-readback.json`. It proves a
real `vectorbt==0.26.2` evaluation run (`vbt-real-*`), a passed evaluation gate,
worker-driven completion, persisted terminal readback, and exposure of the same
session identifier.

This is bounded local-development evidence using ephemeral tenant-safe source
authority and a fake persona-target pre-read boundary. It does not claim hosted
deployment, Postgres HA, actual downstream Learning consumption, shared catalog
registration, or Compose activation. Those remain owned by the M2/M3/M4 tasks.
