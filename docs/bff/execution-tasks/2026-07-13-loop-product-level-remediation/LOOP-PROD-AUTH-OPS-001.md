# LOOP-PROD-AUTH-OPS-001 — Governed dev credential and privileged-capability lifecycle

Status: requires ordinary fleet work plus authorized Human/Ops provisioning

Canonical catalog: `tasks.json`

Canonical contract SHA-256: `cf098d11410d13f68d512a25373a01f623fa8a6dc50a33dbb03146c35d1cfb05`
The catalog acceptance, proof, and dispatch arrays are machine-authoritative;
the prose sections below are explanatory renderings.

Source addendum:
`docs/04/pantheon_loop_product_level_remediation_2026-07-13/archive/REMEDIATION_GAP_ADDENDUM_2026-07-13.md`

## Assignment

| Field | Value |
| --- | --- |
| Owner | Codex |
| Reviewer | Codex2 |
| Wave | 1 |
| Fleet lane | `dev-auth-credential-lifecycle` |
| Repository | `pantheon` |
| Merge target | `dev` |
| Current maturity | strict code path with unprovisioned secrets and incomplete kernel capability issuance |
| Target maturity | product-level |
| Human/Ops final sign-off | required for external secret and capability policy changes |

## Product outcome

建立可稽核的 dev JWT signing、dev-login client、short-lived role/tenant identity
及 `assistant.kernel.*` capability 生命週期；缺少授權或 secret 時維持 BLOCKED，
不得由 fleet 自行建立、顯示或弱化憑證。

## Dependencies

- `LOOP-PROD-AUTH-001`
- `LOOP-PROD-LEASE-001`
- `LOOP-PROD-ATTEST-001`

## Loop scope

- `bff_health_monitoring`
- `per_persona_ooda`

## Declared artifacts

- `docs/deployment/dev-auth-credential-lifecycle.md`
- `scripts/qualify_dev_auth_credential_lifecycle.py`
- `scripts/test_qualify_dev_auth_credential_lifecycle.py`
- `docs/deployment/evidence/loop-product-level/LOOP-PROD-AUTH-OPS-001`

## Acceptance

- an authorized operator provisions the three environment secrets outside source and records only redacted identifiers and timestamps
- direct and workflow dev deploy fail before cloud access when any credential is blank, partial, expired, or unavailable
- local symmetric verifier keys are distinct from external JWKS roots; algorithm/issuer routing is one-way and cannot fall back from external/asymmetric trust to the local verifier
- protected source repository, branch, commit, workflow, environment, and actor authorization occurs before any secret, cloud identity, or target access is requested
- normal deployment preserves and reads back the complete running trust contract: issuer, audience, algorithm allowlist, key/JWKS version, tenant, TTL, cookie posture, environment, and revocation policy
- application secrets use mode-0600 protected files or equivalent child-scoped channels, never broad environment exports, argv, shell traces, logs, artifacts, or unintended child processes
- staging/control/exec/all and public/browser lanes receive no dev signing, client, database, or privileged token material
- dev-login issues short-lived role/tenant identities with explicit TTL, audience, issuer, and replay protection
- privileged Management AI qualification uses an independently authorized identity carrying only required `assistant.kernel.debug` or `assistant.kernel.repair` capability
- token validation happens before any container, VM, worktree, control-mode, or file mutation; cleanup deactivates control mode on success and failure
- rotation drill proves old/new overlap, cutover, expiry/revocation, restart, and rollback without evidence/log/browser leakage
- Human/Ops approves capability policy and residual risks; missing approval keeps the task blocked
- PR `#3572` and earlier strict-auth worktrees are non-authoritative inputs; the admitted fleet audits the exact head and may adopt, rewrite, or discard them

## Required proof

- redacted provisioning and access-control record
- positive and negative hosted `/bff/auth/dev-login`, `/bff/me`, control-mode, and expiry evidence
- argv/environment/log/browser/target-isolation scan
- rotation, restart, rollback, and deactivation drill
- merged PR, merge SHA, checks, independent security review, and protected attestation

Reviewer approval must set `review_file` under:

`docs/deployment/evidence/loop-product-level/LOOP-PROD-AUTH-OPS-001/`

## Non-goals

- No panel-only closure
- No seed fixture as live proof
- No approval gate bypass
- No synthetic receipt as terminal execution proof
- No live-capital or live-broker side effect

## Dispatch and closeout rules

- planner does not implement these artifacts; PR `#3572` is input only and requires a fresh admitted fleet owner plus a distinct formal exact-head reviewer
- fleet work may implement validation and runbooks but cannot invent or rotate external secrets without authorization
- evidence must redact values and retain only non-sensitive fingerprints
- no temporary stub/all-role bearer is an acceptable unblock
- Human/Ops verdict is mandatory before done
