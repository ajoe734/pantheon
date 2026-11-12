# Agora human learning evidence

Design: `docs/04/pantheon_persona_cycle_reconnect_2026-11-12/SD_PERSONA_CYCLE_RECONNECT_2026-11-12.md` §6.7 / D11, implementing the teaching-evidence boundary in blueprint axiom 12, §6.5 and §9.4.3.

The BFF forwards human evidence to `services.agora.service.AgoraEvidenceWriteService`. Its `AgoraEvidenceStore` persists directly into the existing Agora dataset inbox. The inbox is both the original evidence record and the durable extraction queue: admission is one transaction, with no second notes/feedback write to reconcile. Existing standalone Agora session/note APIs are unchanged.

The four POST routes are:

- `/bff/agora/signal-feedback` (learn)
- `/bff/agora/notes` (observe)
- `/bff/agora/insights` (observe)
- `/bff/agora/training-examples` (learn)

Each requires existing operator authorization, an `Idempotency-Key`, `evidence_id`, `persona_id`, `subject_ref`, nonempty `content`, an aware `captured_at` timestamp and explicit `consent_granted`. Tenant and user come from authenticated scope; payload fields cannot override them. Retention is 1–365 days (default 30). `environment_ceiling` only accepts `paper`. Subject references remain attached to the governed DatasetVersion. No model call, memory mutation, promotion or execution authority is granted.

The existing generic interaction-evidence endpoint rejects the reserved `agora_human_evidence` provenance namespace. Only the Agora owner can stamp it, so generic submissions cannot impersonate authenticated human-evidence writes.

The private read routes are `GET /bff/agora/human-evidence/{evidence_id}` and `GET /bff/agora/personas/{persona_id}/human-evidence?since=<aware ISO timestamp>`. The latter is C1 Observe's human-feedback input, including pending and failed extraction. Its `since` predicate uses owner admission time (`created_at`), so a newly submitted historical correction is visible. It is strictly scoped to the authenticated tenant and user, as Agora's existing private-data policy requires. An empty result explicitly reports `status=unavailable`, `reason=no_new_human_evidence`; missing detail returns 404 with `agora_human_evidence_missing`. Revoked or expired records are excluded. Sensitive dictionary keys are redacted before admission; unconsented raw transcripts are rejected.

Production composition mounts the routes and installs the extraction loop through `core/app_factory.py`. The loop wraps the app's existing lifespan, processes at most 25 scopes × 25 records per tick, waits five seconds between ticks, and awaits its current batch at shutdown. It discovers pending/expired-lease scopes from the durable inbox, so restart recovery does not depend on an in-memory queue. Claims and DatasetVersion/handoff publication use the existing lease-fenced extraction transactions. Exceptions leave durable work recoverable and record explicit unavailable worker state in `app.state.agora_evidence_worker`.

Durable writes require the existing `AGORA_DATASET_STORE_BACKEND=postgres` and its existing DSN/schema configuration. An unconfigured or unreachable owner fails closed (503). Ephemeral storage is only an explicit test injection. No new credential or service route is introduced. L6 uses its existing scheduled Agora handoff drain and acknowledgement boundary; there is no manual dataset construction step.

Validation includes each real human-evidence BFF route, direct owner readback, scoped reads, idempotent replay/conflict, consent/privacy/retention, paper ceiling, a PostgreSQL owner restart with an expired claim, automatic lifespan extraction, and the real policy-learning scheduler intake/ack. The integration test replaces only scheduler HTTP transports with FastAPI TestClients and uses actual POST-created evidence, actual PostgreSQL DatasetVersions and actual policy-learning intake/storage. Text feedback without observation/action trajectories reaches L6 and records `degraded` with its real reason and `seed_fallback_used=false`; these records do not prove successful training or persona mutation.

This is source/service-boundary proof. Hosted deployment, frontend delivery and independent review are not claimed by this record. The assigned reviewer supplies the independent exact-head decision through the canonical task workflow.

Reproduce the durable integration test with a disposable PostgreSQL 16 instance:

```bash
AGORA_EVIDENCE_TEST_DSN='<disposable local PostgreSQL DSN>' \
  .venv-pantheon/bin/python3 -m pytest -q \
  services/control-plane/bff/tests/test_agora_human_evidence.py
```

Without `AGORA_EVIDENCE_TEST_DSN`, only the durable integration case skips; the other six cases still run. Exact validation commands and terminal outputs are recorded in `evidence.json` and the adjacent logs.

Current-dev composition (`8f0445559a19474d7e7da29ed3f175c0800df558`, merge anchor `cb628e9bc686207975c38be533e2ea52a421a20c`): resolved PR #7261's sole conflict by regenerating the BFF route manifest. The 621 routes include all six human-evidence routes and the incoming persona pool route; other incoming mainline files were retained unchanged. This merge preserves published history and uses a normal non-force push.

Final composition regression: 188 passed, no skips, exit 0 in 101.19 seconds, with both PostgreSQL settings enabled in one foreground batch bounded to 240 seconds. It covers provenance rejection, owner availability, durable extraction/handoff and BFF composition/lifespan/route uniqueness. Route manifest, compileall, diff checks and diff budget also passed. `dev-composition-tests.txt` is the final combined output; earlier logs, including `refreshed-regression-tests.txt` on base `0c1e435c691d6ea8c73cf48fa5026dbad94b9275`, remain historical validation. Renewed independent exact-head review is required before integration.
