# CROSS-REPO-SD-VERIFY-001 Review Packet Sidecar

**Parent Task**: `CROSS-REPO-SD-VERIFY-001` - Verify multi-repo SD boundary  
**Parent Status at Preparation**: `review_approved`  
**Parent Owner / Reviewer**: `Codex2` / `Claude2`  
**Sidecar Task**: `CROSS-REPO-SD-VERIFY-001-SIDECAR-REVIEW`  
**Sidecar Owner / Reviewer**: `Codex` / `Claude`  
**Helper Kind**: `review_packet`  
**Prepared**: `2026-04-28`  
**Mutates canonical**: `no`

> Support artifact only. This packet does not modify L1 canonical truth, core
> contract truth, runtime / registry / governance implementation, frontend code,
> or the LEAN bridge. It summarizes the already-prepared parent evidence and
> gives the sidecar reviewer a bounded checklist for accepting or rejecting this
> support slice.

## 1. Disposition Summary

The parent task is already in `review_approved` and is waiting for parent owner
finalization by `Codex2`.

The sidecar review packet should therefore be treated as retrospective support:
it packages the evidence trail for reviewer consumption and identifies what the
parent owner can carry into final closeout. It is not a second canonical review,
not a request to reopen parent scope, and not authorization for EP5 live /
canary execution.

Recommended sidecar disposition: approve this sidecar if the packet accurately
summarizes the parent evidence, keeps the support-only boundary intact, and
hands off the bounded caveat about frontend `detail.foundation_error` typing.

## 2. Source Trail

| Source | Role in this packet |
|---|---|
| `ai-status.json` | Durable state: parent `review_approved`; sidecar owner `Codex`, reviewer `Claude`; support-only acceptance |
| `.orchestrator/task-briefs/cross_repo_sd_verify_001_sidecar_review.md` | Task-scoped instruction: prepare review packet / evidence summary without canonical edits |
| `docs/reviews/2026-04-27-sd-materializable-execution-task-packet.md` | Original materialized task definition and acceptance shape |
| `support/sidecars/CROSS-REPO-SD-VERIFY-001/CROSS-REPO-SD-VERIFY-001-SIDECAR-ACCEPTANCE.md` | Dependency / acceptance sidecar used by the parent run |
| `docs/reviews/2026-04-28-cross-repo-sd-verify-001-codex2-handoff.md` | Parent owner evidence packet and commands run |
| `docs/reviews/2026-04-28-cross-repo-sd-verify-001-claude2-review.md` | Parent reviewer approval, rerun tests, and non-blocking caveat |

## 3. Parent Evidence Snapshot

| Acceptance target | Parent result | Evidence summary |
|---|---|---|
| Frontend command authority | PASS | `operatorApi` write helpers in `../front-ai-trading-system/src/lib/bffClient.ts` converge on `POST /api/v1/operator/commands`; parent handoff cites generic commands, incident actions, deployment diff escalation, rollback approval / rejection, and mutation approval / rejection. |
| BFF command boundary | PASS | `services/control-plane/bff/main.py` exposes `POST /api/v1/operator/commands`, accepts trace / correlation / request / idempotency headers, builds a foundation command context, persists command/audit context, and exposes receipt polling. |
| Error UX | PASS with caveat | Frontend `BffError` preserves structured BFF `detail.error.code` and `detail.error.message`; the full `detail.foundation_error` object is not yet exposed as a typed frontend field. Parent reviewer accepted this as non-blocking because operator-visible code / message survive and BFF tests cover foundation envelopes. |
| Lineage / trace UX | PASS | Frontend lineage APIs consume BFF lineage routes. Parent evidence did not find local frontend reconstruction of lineage truth. |
| Runtime telemetry hook | PASS | Telemetry exposes `GET /api/telemetry/lineage/traces/<trace_id>/source-runtime-telemetry`, backed by `source_runtime_telemetry_trace`. |
| Derived lineage boundary | PASS | `services/registry/lineage/read_model_contract.md` keeps `source_runtime_telemetry_trace` derived-only and explicit about missing edges. |
| LEAN bridge authority boundary | PASS | `lean/Algorithm.Python/pantheon_algo/base.py` wires `SignalConsumer` / `SignalStoreClient`, schedules `SignalConsumer.drain()`, and exposes `flush_rebalance(run_id)` only. No governance, rollback, kill-switch, deployment approval, or broker authority API is exposed from the bridge. |
| No parallel frontend / LEAN authority | PASS | Parent searches found governed frontend writes converging on the BFF command route and LEAN limited to execution-side signal consumption. |

## 4. Reviewer-Rechecked Evidence

Claude2 rechecked the parent claims directly and approved the parent task. The
review file records these concrete checks:

| Rechecked area | Reviewer result |
|---|---|
| BFF command route and headers | Confirmed route accepts `X-Trace-Id`, `X-Correlation-Id`, `X-Request-Id`, and `X-Idempotency-Key`; foundation context and error wrapper are present. |
| Telemetry source-runtime route | Confirmed route registration and backing read-model function. |
| Derived lineage contract | Confirmed derived-only invariant and missing-edge semantics. |
| Frontend `BffError` | Confirmed structured code / message preservation; carried typed `detail.foundation_error` omission as non-blocking caveat. |
| Frontend lineage APIs | Confirmed BFF lineage route consumption. |
| Frontend governed writes | Confirmed governed helpers post to `/api/v1/operator/commands`. |
| LEAN bridge | Confirmed execution-side signal consumer wiring only. |

Reviewer reruns recorded in
`docs/reviews/2026-04-28-cross-repo-sd-verify-001-claude2-review.md`:

```bash
PYTHONPATH=/home/edna/.local/lib/python3.12/site-packages \
  python3.12 -m pytest \
  services/control-plane/bff/test_governance_command_submission.py \
  services/runtime-manager/test_runtime_manager.py \
  services/foundation/tests -q
# 59 passed

PYTHONPATH=/home/edna/.local/lib/python3.12/site-packages \
  python3.12 -m pytest \
  services/telemetry/lineage_read/test_service.py \
  services/telemetry/test_main_routes.py -q
# 40 passed
```

The 40-test lineage count is one higher than the parent owner handoff because a
later SD-RECON-001 reviewer fix added the telemetry-only `position_snapshot`
case. Claude2 noted this as consistent with current dependency state, not a
regression.

## 5. Boundary Guardrails

This sidecar does not:

- finalize the parent task to `done`; only parent owner `Codex2` can do that,
- change L1 architecture or policy files,
- change BFF, telemetry, registry, frontend, or LEAN runtime implementation,
- claim live / canary readiness,
- expand `SD-RECON-001` reconciliation scope,
- promote Qlib, TRL, or any research backend to production activation.

The only material output of this sidecar is this support packet and the
corresponding status handoff to `Claude`.

## 6. Sidecar Reviewer Checklist

| Check | Expected result |
|---|---|
| Artifact scope | This file is under `support/sidecars/CROSS-REPO-SD-VERIFY-001/` and is a support artifact only. |
| Canonical mutation | No L1 canonical truth, core contracts, runtime implementation, frontend source, or LEAN bridge files are modified by this sidecar. |
| Parent state reflected | Parent task is represented as already `review_approved`, not as work still needing this sidecar to prove the boundary. |
| Evidence summary quality | The parent handoff and Claude2 review evidence are summarized with concrete file / route / behavior references. |
| Caveat preserved | Frontend `detail.foundation_error` typed-field absence is carried forward as bounded and non-blocking, not hidden. |
| Handoff target | Status handoff goes to assigned sidecar reviewer `Claude`. |

## 7. Handoff to Reviewer (`Claude`)

Please review this sidecar as a support packet, not as the parent boundary
verification itself.

Recommended disposition:

1. approve if the packet is accurate, support-only, and useful for parent
   closeout context,
2. request changes only for missing evidence-summary details or boundary
   language,
3. do not reopen parent canonical scope from this sidecar unless the packet
   misstates the parent evidence.

If approved, return `CROSS-REPO-SD-VERIFY-001-SIDECAR-REVIEW` to `Codex` for
formal finalization to `done`.
