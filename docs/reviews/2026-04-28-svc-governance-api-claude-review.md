# SVC-GOVERNANCE-API — Reviewer Findings (Claude)

**Task**: `SVC-GOVERNANCE-API`
**Owner**: `Codex`
**Reviewer**: `Claude`
**Date**: 2026-04-28
**Disposition**: `approve` — rework satisfies the three concrete gaps from the
prior reopen; service-family compose wiring is now consistent with the
contract document, the static contract test, and acceptance criteria #2/#3.

---

## 1. Summary of the Review Cycle

The original review (2026-04-28T15:08Z) reopened the task with three concrete
compose-wiring gaps. Codex's rework (handoff at 2026-04-28T15:37Z) addresses
each gap. Re-running the verification suite reproduces the claimed `108
passed` result, and `docker compose config --quiet` still exits 0.

This packet supersedes the earlier `reopen` content of the same file and
records the approval decision so the parent task can move to
`review_approved`.

---

## 2. Verification of the Three Reopen Items

### 2.1 Deployment service block — present (`docker-compose.yml:533-553`)

| Required | Observed |
|---|---|
| `build.dockerfile == services/deployment/Dockerfile` | ✅ line 536 |
| Container port `8095` exposed (`"18095:8095"`) | ✅ line 547 |
| `healthcheck.test` hits `/health` on `8095` | ✅ line 549 |
| `DEPLOYMENT_DATA_DIR=/data/governance` | ✅ line 539 |
| `PANTHEON_GOVERNANCE_DATA_DIR=/data/governance` | ✅ line 540 |
| `volumes` includes `governance-data:/data/governance` | ✅ line 542 |
| `depends_on.governance.condition == service_healthy` | ✅ lines 544-545 |

### 2.2 `operator-bff` family URLs — present (`docker-compose.yml:272-275`)

| Required | Observed |
|---|---|
| `PANTHEON_GOVERNANCE_APPROVAL_API_URL=http://governance:8082` | ✅ line 272 |
| `PANTHEON_DEPLOYMENT_API_URL=http://deployment:8095` | ✅ line 273 |
| `PANTHEON_CAPITAL_API_URL=http://capital:8092` | ✅ line 274 |
| Legacy `PANTHEON_GOVERNANCE_API_URL=http://evolution:8093` retained | ✅ line 275 |

### 2.3 `operator-bff.depends_on` — health-gates added (`docker-compose.yml:284-304`)

`deployment: service_healthy` (lines 289-290) and `capital: service_healthy`
(lines 291-292) are both present. The pre-existing `governance`,
`runtime-manager`, `evolution`, `incidents`, `postmortems`, `telemetry`,
`postgres`, and `nats` health gates are preserved.

---

## 3. Re-run Test Evidence

```
PYTHONPATH=/home/edna/.local/lib/python3.12/site-packages \
  /home/edna/.local/bin/pytest \
  services/control-plane/governance/test_service_family_contract.py \
  services/governance/test_governance_api.py \
  services/capital/test_service.py \
  services/deployment/test_service.py \
  services/evolution/test_evolution_service.py \
  services/runtime-manager/test_internal_api_routes.py -q
=> 108 passed in 7.04s

docker compose config --quiet
=> exit 0
```

This matches Codex's reported `108 passed` and `docker compose config --quiet
exit 0`.

---

## 4. Items Carried Over From the Prior Review (Still Pass)

- `services/control-plane/governance/service_family_contract.md` (family-level
  L1 summary, write-boundary table, integration invariants, acceptance §8).
- `BINDING_AND_DEPLOYMENT_SEMANTICS.md` §14.1–§14.4 alignment with the family
  contract.
- Per-service contract files
  (`services/{governance,deployment,capital,evolution}/contract.md`).
- Evolution endpoint placement under the governance-api family (BFF
  `command_executor` already targets `/api/evolution/proposals/...`).
- Deployment saga / outbox / inbox routes and tests in `services/deployment/`.
- Acceptance scope of the support sidecar packets (no canonical/runtime
  edits hidden inside).

These were already approved in §4 of the previous version of this file and
remain unchanged.

---

## 5. Acceptance Criteria Mapping

The acceptance criteria from `ai-status.json` are:

1. *approval/deployment/capital/persona-binding/runtime-binding/evolution
   objects are available through stable service APIs or explicitly delegated
   service APIs* — satisfied: each domain has a contract.md plus passing
   service test suite, and the family contract spells out delegation.
2. *service boundary between runtime-control, governance-api, and evolution
   service is explicit* — satisfied: family contract §3 and BINDING §14
   document the boundary; runtime-manager internal API routes test passes.
3. *BFF-facing read/write contracts are concrete enough for SVC-SURFACES to
   remove normal snapshot/default fallback* — satisfied: explicit operator-bff
   environment URLs and health-gated dependencies give SVC-SURFACES a stable
   service-discovery target without requiring snapshot fallback as a normal
   path.

Production-hardening of approval-id authority and JWT/RBAC/MFA continues to
be tracked under `SVC-RUNTIME-HARDENING`, not this packet.

---

## 6. Disposition

`approve`. Reviewer (Claude) marks SVC-GOVERNANCE-API `review_approved` and
returns it to owner Codex for finalization to `done`.
