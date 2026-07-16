# EVOCHAIN-011: Dev Deploy + Packet Closeout

Owner: Antigravity · Reviewer: Codex

Task: `docs/bff/execution-tasks/2026-07-13-evolution-journal-producer-gap/INDEX.md`
(Wave 3, packet-level closeout for the Evolution Journal Producer Gap)

Gap spec: `docs/04/pantheon_evolution_journal_producer_gap_2026-07-13/EVOLUTION_JOURNAL_PRODUCER_GAP.md`

Archived evidence directory: `docs/04/pantheon_evolution_journal_producer_gap_2026-07-13/archive/`

## Summary

This is the packet-level "prove it live" closeout for `EVOCHAIN-001..010`.
All dependency tasks (`EVOCHAIN-001` through `EVOCHAIN-010`) are `done` and merged into `dev`. This task captured fresh hosted-dev evidence on `2026-07-16` directly against the live BFF and FE.

**Result: the packet's functional Definition of Done is proven live.**
The full producer chain — real threshold breach, deduped incident, daily-sweep proposal, formal Evolution Journal entry, Persona Fleet formal-mutation link, and `freeze_orders`/`rollbacks`/journal-aggregate surfaces all `ok` — is observed on hosted dev with real data.

The dev BFF was successfully deployed under the required **strict auth posture** (`auth_stub: false`, `auth_mode: "strict"`), running commit `aa68f7508fcb58d403a1f845fa1d6a8f5a3fe748` which contains all EVOCHAIN final PR merge SHAs. All 12 E2E business-flow verifiers pass cleanly in this posture using OIDC-derived credentials.

## Live Curl Evidence (2026-07-16, hosted dev)

All requests below were run directly against the public hosted hosts:

- FE: `https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io`
- BFF: `https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io`

```bash
# Obtain Bearer JWT Token using OIDC Client Credentials
curl -s -k -X POST -H "Content-Type: application/json" \
  -d '{"grant_type": "client_credentials", "client_id": "pantheon-dev-operator", "client_secret": "<secret>"}' \
  https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/bff/auth/dev-login

# Query protected endpoints with retrieved JWT token
curl -s -H 'Authorization: Bearer <JWT-token>' \
  https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/bff/management/evolution-journal
curl -s -H 'Authorization: Bearer <JWT-token>' \
  https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/api/v1/freeze-orders
curl -s -H 'Authorization: Bearer <JWT-token>' \
  https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/api/v1/rollbacks
curl -s -H 'Authorization: Bearer <JWT-token>' \
  https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/bff/incidents
curl -s -H 'Authorization: Bearer <JWT-token>' \
  "https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/bff/management/persona-fleet?page_size=100"
```

Raw responses archived at:

- `archive/evochain011_version_curl_2026-07-16.json`
- `archive/evochain011_journal_curl_2026-07-16.json`
- `archive/evochain011_freeze_orders_curl_2026-07-16.json`
- `archive/evochain011_rollbacks_curl_2026-07-16.json`
- `archive/evochain011_incidents_curl_2026-07-16.json`
- `archive/evochain011_persona_fleet_formal_mutations_2026-07-16.json`
- `archive/evochain011_shell_summary_curl_2026-07-16.json`
- `archive/evochain011_container_health_2026-07-16.json`
- `archive/evochain011_daily_sweep_status_2026-07-16.json`
- `archive/evochain011_e2e_cooldown_output_2026-07-16.json`

### Journal aggregate surfaces — all `ok`

`GET /bff/management/evolution-journal` `meta.surfaces` at `2026-07-16T20:50:36Z`:

```json
{
  "management_evolution_journal": {"status": "ok", "source": "bff_composed"},
  "mutation_review": {"status": "ok", "source": "bff_composed"},
  "evolution_decisions": {"status": "ok", "source": "service_client"},
  "postmortems": {"status": "ok", "source": "service_client"},
  "freeze_orders": {"status": "ok", "source": "service_client"},
  "rollbacks": {"status": "ok", "source": "service_client"},
  "approval_decisions": {"status": "ok", "source": "canonical"},
  "personas": {"status": "ok", "source": "bff_local_dev_store"},
  "persona_bindings": {"status": "ok", "source": "service_client"},
  "runtime_bindings": {"status": "ok", "source": "service_client"},
  "incidents": {"status": "ok", "source": "service_client"}
}
```

Every surface reports `ok` with a live source — none report `missing` or `local_snapshot`. This directly closes root cause 4 from the gap spec (`freeze_orders` / `all_rollbacks` reporting `missing`, forcing the aggregate permanently `degraded`).

`GET /api/v1/freeze-orders` and `GET /api/v1/rollbacks` both return `{"items": [], "meta": {"snapshot_at": "..."}}`: zero active freeze orders or rollbacks exist yet, but the canonical store responds normally with no error, no `unavailable` status, and no fallback-to-snapshot marker.

### Journal content — real, non-seed, formal entries

`summary` block from the `2026-07-16` response:

```json
{
  "total_items": 70,
  "returned_items": 20,
  "decision_count": 27,
  "mutation_review_count": 27,
  "postmortem_count": 16,
  "rollback_count": 0,
  "freeze_order_count": 0,
  "pending_review_count": 0,
  "active_freeze_count": 0,
  "completed_rollback_count": 0,
  "latest_at": "2026-07-16T16:40:54Z",
  "by_type": {
    "mutation_review": 27,
    "evolution_decision": 27,
    "postmortem": 16
  }
}
```

70 real journal items exist in total (vs. the 2 seed-only items recorded at gap-spec time). The default page view returns the first 20 items (returned_items: 20), all of which carry `"origin": "live"` (not `seed`) — real threshold breaches, sweep-derived proposals, and postmortems, driven by real paper-trading incidents such as `inc-threshold-50f2e21f161c`.

### Persona Fleet → formal journal entry link

`GET /bff/management/persona-fleet` shows 12 fleet personas with `last_mutation_kind: "formal_mutation"` and `mutation_confidence: "formal"` out of 24 total personas in the fleet. Each has an `evolution_href` that resolves to the corresponding `mutation_review` journal entry. Example: `persona-tw-equity` → `mutation_entry_id: "evo-sweep-inc-threshold-50f2e21f161c"` → `/management/evolution-journal?persona=persona-tw-equity&mutation_review=evo-sweep-inc-threshold-50f2e21f161c`, which is the exact entry produced by the incident above. This closes the packet's "Persona Fleet 最近 MUTATION links to that formal entry" DoD line.

### Full producer chain, observed end to end

```text
real threshold breach (inc-threshold-50f2e21f161c, rolling_drawdown_multiple)
  -> incident, deduped, status "open"
  -> daily sweep -> decision "evo-sweep-inc-threshold-50f2e21f161c" (proposed, retrain, low risk)
  -> formal Evolution Journal entry (mutation_review + evolution_decision rows, origin: live)
  -> Persona Fleet "persona-tw-equity" last_mutation_kind=formal_mutation, links to that entry
  -> freeze_orders / rollbacks surfaces: ok (service_client, empty but not missing)
  -> Evolution Journal aggregate surface: ok (bff_composed)
```

This matches the packet's "Definition of Done (packet-level)" block in `EVOLUTION_JOURNAL_PRODUCER_GAP.md` verbatim.

## Hosted Screenshot Evidence

Captured via a headless Playwright script driven against the live hosted FE (`https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/management/evolution-journal`), authenticated with a dev bearer session written into the FE's expected keys:

![Evolution Journal hosted evidence, 2026-07-15](../../../04/pantheon_evolution_journal_producer_gap_2026-07-13/archive/evochain011_journal_hosted_evidence.png)

The page renders real formal entries with action/risk/target/approval-status fields populated and no fixture badge. This is the same live producer output the curl evidence above proves.

### TopBar badge: honest finding, not a regression

The TopBar's global data-source badge still reads **SNAPSHOT DATA** (or **LIVE (PARTIALLY DEGRADED)** depending on `running_jobs` state).
`GET /bff/management/shell-summary` `meta.surfaces` at capture time (`archive/evochain011_shell_summary_curl_2026-07-16.json`):

```json
{
  "shell_summary": {"status": "degraded", "source": "bff_composed"},
  "pending_approvals": {"status": "ok", "source": "canonical"},
  "open_alerts": {"status": "ok", "source": "bff_cheap_count"},
  "running_jobs": {"status": "unavailable", "source": "missing"}
}
```

The TopBar badge is driven by the *global shell-summary* surface set (`pending_approvals` / `open_alerts` / `running_jobs`), not by `freeze_orders` / `rollbacks` / the journal aggregate this packet targets.
Since `running_jobs` is genuinely `unavailable`/`missing` (no execution job-tracking backend is wired to that count yet, which is an unrelated, out-of-packet surface under `services/deployment` scope), the TopBar badge honestly reports a degraded/snapshot state.
This is the correct safety behavior established in `EVOCHAIN-008`'s classifier contract and is not a regression of this packet.

## PR Merge SHAs (all packet tasks)

| Task | Repo | Final PR | Merge SHA |
|---|---|---|---|
| EVOCHAIN-001 | pantheon | #3620 (9 rounds) | `4c96fe9edc93954afe6be0427b2cfe5f7d2491c5` |
| EVOCHAIN-002 | pantheon | #3516 | `4e8291ef120b1f440794a9ea5b00bc1ed112d07e` |
| EVOCHAIN-003 | pantheon | #3702 (9 rounds) | `fd75ee2f77495964031a84c3cd6aac3dac966e51` |
| EVOCHAIN-004 | pantheon | #3538 | `af5ef1a06283a80219abca512b47e1b635390f67` |
| EVOCHAIN-005 | pantheon | #3624 | `852a9469ab5fde916174e04ede0b8c7468dadd9c` |
| EVOCHAIN-006 | pantheon | #3534 (+ #3512) | `24dd23294fa6afdac55119d2bc86ec78040c74d4` |
| EVOCHAIN-007 | pantheon | #3595 (3 rounds) | `a44cfc2443dba45d52889fa53a896a0121b86cdc` |
| EVOCHAIN-008 | pantheon (evidence) | #3522 | `83ee887630d6eebb7b0bf6dd5f8ce1e0486df57f` |
| EVOCHAIN-008 | execute-plans | #298 | `89515d82f087bf10363b3a949868c480f2c15cda` |
| EVOCHAIN-009 | pantheon (evidence) | #3685 | `1976b5bb814e437161571ff4ae86ea0f4c7eac7b` |
| EVOCHAIN-009 | execute-plans | #354 (6 rounds) | `404411d203f3b8a7f17b83e2f4e9a3b14bec45d5` |
| EVOCHAIN-010 | pantheon | #3716 + #3720 | `20d4a61a00870b2a21797f7d206ff392410d9f2d` / `e4c3ce68bc4df389288d428bbd5fb1d3869a2112` |

All pantheon SHAs above are confirmed ancestors of current `origin/dev` (verified via `git merge-base --is-ancestor <sha> origin/dev` on `2026-07-16`).

## Deployment State

### FE (`execute-plans`) — current

`GET https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/deployment.json`:

```json
{
  "commit": "b352faa087e6e1bd6087c619d6e9d99a35fbca41",
  "sourceBranch": "dev",
  "deployedAt": "20260715T072629Z",
  "buildMode": {"VITE_BFF_MODE": "live", "VITE_BFF_FALLBACK": "strict"}
}
```

The deployed FE commit includes all EVOCHAIN FE PR modifications.

### BFF/root (`pantheon`) — deployed and current

`GET https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/bff/version`:

```json
{
  "service": "operator-bff",
  "version": "1.0.0",
  "commit": "aa68f7508fcb58d403a1f845fa1d6a8f5a3fe748",
  "auth_stub": false,
  "auth_mode": "strict"
}
```

BFF/root is fully current and running under `strict` auth posture.

### Live re-verification (2026-07-16)

Re-checked directly against the public hosted hosts in this session:
- The currently-live BFF commit is `aa68f7508fcb58d403a1f845fa1d6a8f5a3fe748`.
- It runs under `strict` auth posture.
- All 12 E2E business-flow verifiers (`scripts/run_e2e_verifiers.sh`) pass cleanly.

## Residual Risks

1. **TopBar global SNAPSHOT DATA badge persists** due to the unrelated `running_jobs` shell-summary surface reporting `unavailable`/`missing`. This is correct, honest badge behavior per `EVOCHAIN-008`'s classifier contract. Tracked as a pre-existing, out-of-scope gap. Owner: execution-environment / deployment service. Expiry: none.
2. **Zero freeze orders / rollbacks recorded to date.** `freeze_orders` and `rollbacks` surfaces report `ok` with an empty canonical store. This is expected and not a defect. Owner: operator/task first exercising an approve→execute path. Expiry: none.

## Verification Commands Run

```bash
# Verify PR ancestry
git merge-base --is-ancestor <sha> origin/dev

# Run E2E business verifiers in strict auth posture
BFF_BASE=https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io BFF_TOKEN=<JWT-token> scripts/run_e2e_verifiers.sh
```
