# LOOP-PROD-AUTH-BOOT-001 — Authorized dev auth credential bootstrap

Status: requires ordinary fleet preparation plus authorized Human/Ops provisioning

Canonical catalog: `tasks.json`

Canonical contract SHA-256: `de2a56c768b7ccd680ece495ea1f81ce0ca246c239c220ae89f377a413f1151e`
The catalog acceptance, proof, and dispatch arrays are machine-authoritative;
the prose sections below are explanatory renderings.

Source addendum:
`docs/04/pantheon_loop_product_level_remediation_2026-07-13/archive/REMEDIATION_GAP_ADDENDUM_2026-07-13.md`

## Assignment

| Field | Value |
| --- | --- |
| Owner | Codex2 |
| Reviewer | Codex |
| Wave | 0 |
| Fleet lane | `dev-auth-credential-bootstrap` |
| Repository | `pantheon` |
| Merge target | `dev` |
| Current maturity | strict auth code work began without completed governed credential bootstrap |
| Target maturity | product-level |
| Human/Ops final sign-off | required |

## Product outcome

在 strict hosted auth 驗收之前建立受保護的外部 credential bootstrap。
Fleet 可實作 validator、runbook 與 redacted negative tests，但只有獲授權
Human/Ops 可以建立、撤銷或觀察外部 secret。缺少該授權時，本任務及其下游
`LOOP-PROD-AUTH-001` 都維持 BLOCKED。

## Dependencies

- `LOOP-PROD-002`
- `LOOP-PROD-DELIVERY-001`

## Loop scope

- `bff_health_monitoring`
- `per_persona_ooda`

## Declared artifacts

- `docs/deployment/dev-auth-bootstrap-authorization.md`
- `scripts/validate_dev_auth_bootstrap_record.py`
- `scripts/test_validate_dev_auth_bootstrap_record.py`
- `docs/deployment/evidence/loop-product-level/LOOP-PROD-AUTH-BOOT-001`

## Acceptance

- authorized Human/Ops provisions protected dev signing, dev-login client, and
  scoped identity material before hosted strict-auth qualification
- fleet and candidate contexts cannot create, read, print, export, rotate, or
  weaken credential values
- each role, tenant, capability, MFA, issuer, audience, TTL, and environment
  binding is explicit and distinct where separation is required
- the canonical bootstrap record contains only protected redacted identifiers,
  authorization, policy, time, expiry/review, and revocation metadata
- partial, expired, duplicate-subject, cross-boundary, excessive-role,
  missing-MFA, and unauthorized records fail before mutation
- rollback, revocation, restart, and no-leak checks pass
- no stub, repository secret, fixed bearer, or synthetic proof can substitute
  for missing external provisioning
- independent security review and Human/Ops verdict bind the exact task,
  catalog, target, bootstrap record, and policy

## Required proof

- protected redacted authorization and secret-version metadata
- distinct identity and least-capability matrix
- boundary and authorization negative evidence
- source, argv, environment, log, browser, staging, and candidate no-leak scan
- rollback, revocation, restart, protected attestation, review, and Human/Ops verdict

Reviewer approval must set `review_file` under:

`docs/deployment/evidence/loop-product-level/LOOP-PROD-AUTH-BOOT-001/`

## Non-goals

- No panel-only closure
- No seed fixture as live proof
- No approval gate bypass
- No synthetic receipt as terminal execution proof
- No live-capital or live-broker side effect

## Dispatch and closeout rules

- the planner authors and dispatches this contract only; a supervisor-admitted fleet worker implements the validator and a distinct runtime identity reviews it
- fleet work may prepare validation and redacted evidence surfaces only
- only authorized Human/Ops may provision or revoke external credentials
- secret values never enter source, task state, logs, evidence, argv, browser,
  staging, or candidate processes
- missing protected provisioning remains a blocker
