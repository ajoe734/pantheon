# LOOP-PROD-ATTEST-001 — Protected product attestation trust root

Status: ready for fleet dispatch after dependencies are done

Canonical catalog: `tasks.json`

Canonical contract SHA-256: `e46fe78933e33193b57ed1b3e5067c5a7e75ed559adf12a3d30549cdad569930`
The catalog acceptance, proof, and dispatch arrays are machine-authoritative;
the prose sections below are explanatory renderings.

Source addendum:
`docs/04/pantheon_loop_product_level_remediation_2026-07-13/archive/REMEDIATION_GAP_ADDENDUM_2026-07-13.md`

## Assignment

| Field | Value |
| --- | --- |
| Owner | Codex2 |
| Reviewer | Codex |
| Wave | 1 |
| Fleet lane | `protected-product-attestation` |
| Repository | `pantheon` |
| Merge target | `dev` |
| Current maturity | candidate-authored assertions can masquerade as controller evidence |
| Target maturity | product-level |

## Product outcome

產品 evidence 的 trust root 由 protected controller 產生；candidate 只能提交原始
結果，不能自寫 pass boolean 或 zero-count assertion。Attestation 必須以 candidate
拿不到的 asymmetric key 或 platform-protected keyed identity 簽署，並綁定 exact
FE/BFF SHA、run/job、target、lease、artifact digest 與 verification policy。

## Dependencies

- `LOOP-PROD-002`
- `LOOP-PROD-WORKER-001`
- `LOOP-PROD-LEASE-001`

## Loop scope

- `bff_health_monitoring`
- `per_persona_ooda`

## Declared artifacts

- `schemas/protected-product-attestation.schema.json`
- `scripts/verify_protected_product_attestation.py`
- `.github/workflows/loop-product-attestation.yml`
- `scripts/test_verify_protected_product_attestation.py`
- `docs/deployment/evidence/loop-product-level/LOOP-PROD-ATTEST-001`

## Acceptance

- protected controller derives assertions from immutable raw artifacts, canonicalizes the manifest, and authenticates it with an asymmetric signature or platform-protected keyed identity unavailable to candidate processes
- unkeyed checksums are content-integrity digests inside the authenticated envelope and are never accepted as provenance or authorization
- manifest binds repository, exact candidate/base/deployed SHAs, run/job/attempt, target, lease, policy version, artifact digests, timestamps, and expiry
- candidate-controlled fields cannot override verdict, expected counts, policy, signer, or protected provenance
- verifier fails closed on tamper, replay, omission, wrong SHA/run/job/lease/target, stale expiry, unknown key/policy, duplicate assertion, and contradicted evidence
- signing material never enters candidate processes, browser bundles, logs, or committed evidence
- key/policy rotation supports overlap and revocation without accepting an expired trust root
- controller and consumer produce redacted append-only verification records with exact failure reason IDs
- target-dev negative matrix and independent security review pass

## Required proof

- schema and canonicalization vectors
- tamper/replay/omission/rotation negative suite
- protected workflow and lease binding evidence
- merged PR, merge SHA, checks, reviewer verdict, and checksummed evidence

Reviewer approval must set `review_file` under:

`docs/deployment/evidence/loop-product-level/LOOP-PROD-ATTEST-001/`

## Non-goals

- No panel-only closure
- No seed fixture as live proof
- No approval gate bypass
- No synthetic receipt as terminal execution proof
- No live-capital or live-broker side effect

## Dispatch and closeout rules

- no candidate-generated boolean or counter is accepted as a protected verdict
- use repository/environment protection for trust material; never create secrets in code
- a valid signature over stale or incorrectly bound content still fails
- reviewer independently mutates every bound identity class
