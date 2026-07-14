# LOOP-PROD-SIGNOFF-001 — Protected Human/Ops completion verdict enforcement

Status: ready for fleet dispatch after dependencies are done

Canonical catalog: `tasks.json`

Canonical contract SHA-256: `ac738326c6a24d398989c6d49ee4d5880333b16780d079e430d14d273f1d0ea5`

Canonical contract SHA-256: `042d854bb5280f788c4f62b082c370c41f419e3ec39e9c06692855696f36f707`
The complete catalog task contract is machine-authoritative;
the prose sections below are explanatory renderings.

Source addendum:
`docs/04/pantheon_loop_product_level_remediation_2026-07-13/archive/REMEDIATION_GAP_ADDENDUM_2026-07-13.md`

## Assignment

| Field | Value |
| --- | --- |
| Owner | Codex |
| Reviewer | Codex2 |
| Wave | 4 |
| Fleet lane | `protected-human-ops-signoff` |
| Repository | `pantheon` |
| Merge target | `dev` |
| Current maturity | signoff metadata exists but no protected transition-time enforcement consumes it |
| Target maturity | product-level |

## Product outcome

在 final closeout 前安裝機器守門：所有 `requires_human_ops_signoff`
任務都必須有受保護、可撤銷、不可重播且綁定 exact catalog、manifest、
target 與部署 identity 的 Human/Ops 判決；fleet 只能組裝請求，不能簽發判決。

## Dependencies

- `LOOP-PROD-CLOSE-001`
- `LOOP-PROD-WORKER-001`
- `LOOP-PROD-ATTEST-001`

## Loop scope

- `bff_health_monitoring`
- `per_persona_ooda`

## Declared artifacts

- `services/control-plane/governance/product_closeout_verdict.py`
- `services/control-plane/governance/product_closeout_verdict.schema.json`
- `services/control-plane/governance/test_product_closeout_verdict.py`
- `services/control-plane/bff/product_closeout_verdict.py`
- `services/control-plane/bff/test_product_closeout_verdict.py`
- `scripts/loop_done_guardrail.py`
- `scripts/test_loop_done_guardrail.py`
- `scripts/ai_status.py`
- `scripts/test_ai_status.py`
- `docs/deployment/evidence/loop-product-level/LOOP-PROD-SIGNOFF-001`

## Acceptance

- catalog validation requires whole-object equality with the versioned completion
  authority, exact guard/final direct-dependency arrays, and the immutable
  signoff-ID list; extras, duplicates, substitutions, and omissions fail
- a catalog-bound live overlay marks CLOSE-001 `checkpoint_only`, SIGNOFF-001
  `guard_installer`, and CLOSE-002 `final_authority`; foreign overlay or
  conflicting task-local role fails closed
- a protected server-side verdict can be created only by an authenticated authorized Human or Ops actor and never by a candidate artifact, fleet worker, repository secret, or self-authored JSON file
- each verdict binds program, exact catalog digest, task, protected closeout-manifest digest, target, FE and BFF identities, attestation policy, actor, role, decision, issued time, expiry, and nonce
- review-approved to done and final program completion fail closed when any required verdict is missing, rejected, revoked, replayed, stale, unauthorized, or bound to another task, catalog, manifest, target, or deployment
- pre-guard done remains checkpoint-only for program-completion semantics pending
  exact final re-verification;
  post-install transitions are guarded and final authority re-verifies the
  immutable signoff-ID set, including baseline checkpoints
- LOOP-PROD-CLOSE-001 is exposed only as a checkpoint and cannot become program completion authority
- tamper, replay, wrong actor or role, wrong task or SHA, stale, revoked, rejection, duplicate nonce, concurrent decision, and direct state-edit negative tests pass
- append-only audit records identify actor, authorization decision, exact bindings, revocation state, and stable redacted failure reason

## Required proof

- schema, authorization, signature, binding, expiry, revocation, and concurrency tests
- negative direct state-transition and candidate self-signing evidence
- exact authority/overlay/checkpoint-consumption/guard-installer/signoff-set and
  unique final-authority readback
- merged PR, merge SHA, checks, independent review, and checksummed evidence

Reviewer approval must set `review_file` under:

`docs/deployment/evidence/loop-product-level/LOOP-PROD-SIGNOFF-001/`

## Non-goals

- No panel-only closure
- No seed fixture as live proof
- No approval gate bypass
- No synthetic receipt as terminal execution proof
- No live-capital or live-broker side effect

## Dispatch and closeout rules

- start only after every dependency is done; superseded does not satisfy a dependency
- use one clean task worktree in the declared repository and merge to dev through a reviewed PR
- fleet workers may assemble a verdict request but cannot issue, approve, revoke, or forge the protected decision
- missing Human or Ops authority remains an explicit blocker and never falls back to metadata or a candidate file
- reviewer must prove every negative against the exact protected transition path
- never derive authority from mutable live task flags
