# LOOP-PROD-AUTH-BOOT-001 — Authorized dev auth credential bootstrap

Status: requires ordinary fleet preparation plus authorized Human/Ops provisioning

Canonical catalog: `tasks.json`

Canonical contract SHA-256: `6df5f2994ba9b9eb740ebfdb41dacf3f2b4c0c3320ed43f498a314b5de345fed`
The complete catalog task contract is machine-authoritative;
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
Human/Ops 可以建立、撤銷或觀察外部 secret。`LOOP-PROD-AUTH-001` 的 strict-auth
code 與其既有非 pristine 狀態可獨立交付或保留；缺少 provisional bootstrap
record 時，hosted qualification、lease issuance、credential lifecycle 與 browser
activation 必須維持 BLOCKED。

這只產生 external provider/environment-protected 的 provisional bootstrap
record；它不依賴 lease、program attestation、AUTH-OPS 或 final verdict，也不能
用下游 attestation 回頭證明自己的 ancestor。

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
- rollback, revocation, restart, external protected provisional record,
  independent review, and Human/Ops authorization

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
- downstream program attestation is forbidden as bootstrap proof; AUTH-OPS must
  later consume and rotate or revoke this provisional lineage
