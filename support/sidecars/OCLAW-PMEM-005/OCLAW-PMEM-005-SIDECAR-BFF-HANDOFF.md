# OCLAW-PMEM-005 BFF and Frontend Handoff Packet

**Sidecar Task ID**: `OCLAW-PMEM-005-SIDECAR-BFF-HANDOFF`
**Parent Task**: `OCLAW-PMEM-005`
**Parent Owner**: `Codex`
**Parent Reviewer**: `Claude`
**Sidecar Owner**: `Codex2`
**Sidecar Reviewer**: `Antigravity`
**Helper Kind**: `bff_handoff_packet`
**Generated**: 2026-07-11
**Mutates Canonical**: `no`

This packet is support material only. It does not implement a BFF route,
frontend surface, dev gate, provider probe, Memory Plane contract, OpenClaw
materializer, registry, or governance rule. The parent owner decides whether
and how to absorb it into `OCLAW-PMEM-005`.

## 1. Closeout Boundary

The parent closeout must prove four independent facts end to end:

1. provider credentials are not merely mounted; the intended provider path
   completes a live, sanitized smoke;
2. a persona-addressed BFF query returns canonical Memory Plane entries, not a
   desired profile or workspace-file approximation;
3. OpenClaw materialization retains canonical source IDs and is explicitly a
   derived cache;
4. private persona memory cannot appear in another persona's BFF response or
   workspace materialization.

No single `ready` flag is sufficient evidence. The gate should retain the
underlying observations and fail closed when any required observation is
missing, stale, unavailable, or contradictory.

## 2. Existing Evidence and Query Gaps

| Surface | Repository evidence available to parent | Gap the closeout must resolve | Required failure behavior |
|---|---|---|---|
| Canonical retrieval | `GET /api/memory/retrieve` in `services/memory/main.py`; retrieval and authorization tests in `services/memory/test_main.py` | Prove the BFF persona request reaches this authority with the requested persona identity and preserves canonical IDs/scope. | Memory Plane timeout or invalid response is `unavailable`, never an authoritative empty list. |
| Persona memory BFF | `GET /bff/personas/{persona_id}/memory` in `services/control-plane/bff/main.py` currently discovers optional `list_memory_updates_for_persona`. | Replace or bind the optional read to an explicit Memory Plane-backed query and expose source/observation metadata. | Missing reader/source fails the gate; it must not pass because an empty array was returned. |
| Persona runtime route | `GET /bff/personas/{persona_id}/runtime-profile` and its contract tests. | Correlate the persona, model route, workspace ref, and observed OpenClaw identity used by the smoke. | Desired routing without observed identity/convergence is degraded, not ready. |
| Materialized context | `integrations/openclaw/persona_memory_bridge.py` and `test_persona_memory_bridge.py` preserve source IDs, label cache truth, and reject foreign private memory. | Capture hosted/dev evidence that the actual persona workspace contains the expected canonical IDs from the same retrieval snapshot. | Workspace existence or a generated file without matching source IDs fails. |
| Provider readiness | `/bff/assistant/providers` and adapter-backed readiness/probe paths. | Record auth observation separately from live invoke outcome for every required provider path. | Mounted/authenticated plus failed, absent, or stale live smoke fails. |
| Provider invocation | OpenClaw/Codex invoke paths in `openclaw_ops_client.py`; Claude provider readiness/reauth coverage exists in assistant tests. | Prove the exact provider/model path required by the persona, with sanitized request/response correlation. | A fallback provider response cannot silently satisfy a primary-provider gate. |

## 3. Suggested Operator-Safe Gate Snapshot

This is a projection sketch, not a canonical schema. The parent should reuse
existing BFF envelopes and enum conventions.

```json
{
  "persona_id": "persona-123",
  "snapshot_at": "2026-07-11T00:00:00Z",
  "provider": {
    "provider_id": "claude",
    "model_ref": "anthropic/claude-sonnet",
    "auth": {"status": "ready", "observed_at": "..."},
    "live_smoke": {"status": "passed", "observed_at": "...", "probe_id": "redacted-safe-id"}
  },
  "runtime": {
    "status": "ready",
    "observed_persona_id": "persona-123",
    "workspace_ref": "openclaw/persona-123",
    "sync_generation": "generation-ref"
  },
  "canonical_memory": {
    "status": "available",
    "source": "memory_plane",
    "items": [{"memory_id": "pmem-123", "scope": "persona"}]
  },
  "materialization": {
    "status": "matched",
    "source_memory_ids": ["pmem-123"],
    "generated_at": "...",
    "cache_truth": "derived"
  },
  "isolation": {
    "status": "passed",
    "foreign_persona_id": "persona-456",
    "foreign_private_ids_observed": []
  },
  "gate": {"status": "passed", "failed_checks": []}
}
```

The BFF or gate runner owns correlation and the final gate result. The browser
must not infer a pass from a subset of green fields.

## 4. Operator Journey

1. Select a persona and start a fresh verification run; display its opaque run
   ID and snapshot time.
2. Resolve the canonical runtime profile and show desired model route beside
   observed OpenClaw persona identity, workspace, and generation.
3. Run provider auth/readiness and live smoke separately. Keep the gate pending
   until the smoke completes; auth readiness alone never advances it.
4. Query canonical memory through the BFF. Render `available + zero items`
   differently from `unavailable` and show canonical IDs for returned entries.
5. Read materialization evidence and compare its source IDs with the canonical
   retrieval snapshot. Label the workspace output as derived cache.
6. Execute a negative isolation probe using a second persona/private-memory
   fixture. Show only a pass/fail result and safe identifiers; do not render the
   foreign private content.
7. Publish the gate pass only when all required observations belong to the same
   run/generation and are fresh. Otherwise show the failed check and its owning
   remediation surface.
8. Archive sanitized evidence, exact validation commands, child PR/merge SHAs,
   deployed commit IDs, and residual risks for parent closeout.

## 5. Frontend Handoff Rules

| Observed state | Required UI behavior |
|---|---|
| Auth ready; smoke pending/missing | Show `verification required`; gate remains pending/failed. |
| Auth ready; smoke failed | Show provider-path failure with sanitized reason; never show usable. |
| Canonical query available and empty | Show a valid empty-memory state with source and observation time. |
| Canonical query unavailable | Show source unavailable and block the gate; never use empty-state copy. |
| Canonical IDs and materialized IDs differ | Show materialization mismatch and both safe ID sets; block the gate. |
| Runtime desired/observed generations differ | Show drift/stale evidence and require a fresh run. |
| Isolation probe fails | Show a critical isolation failure without exposing leaked content; block publication. |
| All checks pass for one fresh run | Render the BFF/gate-runner pass and evidence links; do not recompute pass client-side. |

Frontend work belongs in `ajoe734/execute-plans`. It must call Pantheon BFF
only, use live BFF mode with strict fallback for hosted dev proof, and must not
read Memory Plane, OpenClaw adapter, provider APIs, or VM workspace files
directly.

## 6. Gate and Test Handoff

The parent should include positive and negative cases for:

- canonical persona memory returned through BFF with stable memory IDs, scope,
  source, and observation metadata;
- an available empty canonical result versus a Memory Plane outage;
- rejection of another persona's private memory at retrieval and
  materialization boundaries;
- canonical source-ID equality between the BFF snapshot and derived workspace
  context;
- workspace present but missing source IDs;
- provider mount/auth ready while live smoke fails;
- primary provider failure followed by fallback success, proving the primary
  gate still fails when the primary path is required;
- desired runtime profile present while observed OpenClaw persona/model or
  generation is absent/drifted;
- stale responses from an earlier run/generation rejected by the UI and gate;
- redaction of provider output, credentials, private memory content, and VM
  paths from archived evidence.

Suggested focused repository checks to compose with hosted probes:

```text
pytest -q services/memory/test_main.py
pytest -q integrations/openclaw/test_persona_memory_bridge.py
pytest -q integrations/openclaw/test_persona_agent_sync.py
pytest -q services/control-plane/bff/tests/test_bff_b2_list_detail_facade.py
pytest -q services/control-plane/bff/test_bff_strategy_persona_contract.py
```

These local checks are component evidence only. They do not substitute for the
parent's live provider, hosted BFF, OpenClaw identity, or workspace readback.

## 7. Closeout Evidence Checklist

- child task PR numbers and merge SHAs for `OCLAW-PMEM-002`, `003`, and `004`;
- deployed Pantheon/BFF and frontend commit IDs;
- exact local and hosted validation commands with timestamps;
- one sanitized passing gate snapshot per required provider/persona path;
- one negative provider-smoke result proving mount readiness cannot pass;
- one negative cross-persona isolation result proving no private IDs/content
  reached the foreign BFF response or workspace;
- canonical-to-materialized source-ID comparison from the same generation;
- explicit residual risks, including probe freshness and any provider path not
  exercised.

## 8. Non-Claims and Composition

This packet does not claim that the optional persona-memory BFF reader is
already canonical, that existing unit tests are hosted proof, that provider
auth is provider usability, or that any frontend has been implemented or
deployed. It also does not authorize exposing private memory content or raw
provider output as evidence.

`OCLAW-PMEM-005` should compose this support packet with reviewer-accepted
outputs from `OCLAW-PMEM-002`, `OCLAW-PMEM-003`, and `OCLAW-PMEM-004`, then own
the exact executable gates and archive. The sidecar reviewer should verify that
the packet remains support-only, preserves Memory Plane authority, treats
workspace memory as derived cache, and requires live smoke plus private-memory
isolation before parent closeout.

## 9. Owner Finalization Checkpoint

- Packet commit: `5369551b7`
- Reviewer approval commit: `31e960970`
- Reviewer verdict: approved by `Antigravity`; no requested changes
- Final owner check: the packet remains support-only and introduces no BFF,
  frontend, runtime, registry, governance, or canonical-truth implementation
- Focused verification: repository-reference and boundary checks documented in
  `support/reviews/OCLAW-PMEM-005-SIDECAR-BFF-HANDOFF-review-antigravity.md`,
  plus whitespace validation with `git diff --check`
- Composition owner: `Codex`, owner of parent task `OCLAW-PMEM-005`, decides
  whether and how to absorb this packet into the executable closeout gates
