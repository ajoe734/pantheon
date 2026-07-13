# LOOP-PROD-CLOSE-002 — Additive final 44-task product closeout

Status: final program gate; starts only after every dependency is done

Canonical catalog: `tasks.json`

Source addendum:
`docs/04/pantheon_loop_product_level_remediation_2026-07-13/archive/REMEDIATION_GAP_ADDENDUM_2026-07-13.md`

## Assignment

| Field | Value |
| --- | --- |
| Owner | Codex2 |
| Reviewer | Codex |
| Wave | 5 |
| Fleet lane | `additive-global-product-closeout` |
| Repository | `pantheon` |
| Merge target | `dev` |
| Current maturity | baseline closeout omits additive execution controls |
| Target maturity | product-level |
| Human/Ops final sign-off | required |

## Product outcome

以 clean target-dev 重跑完整四大 scenarios 與 additive safety matrix；只有 44 個
primary tasks、所有 external dependencies、protected attestation、strict auth ops、
fleet fairness、worker/lease integrity 與 warning-free frontend 全部通過，program
才可宣告完成。

## Dependencies

- `LOOP-PROD-CLOSE-001`
- `LOOP-PROD-WORKER-001`
- `LOOP-PROD-LEASE-001`
- `LOOP-PROD-FLEET-001`
- `LOOP-PROD-ATTEST-001`
- `LOOP-PROD-AUTH-OPS-001`
- `LOOP-PROD-FE-EVID-001`
- `LOOP-PROD-FE-BUILD-001`

## Loop scope

- `source_ingestion`
- `strategy_distillation`
- `alpha_replication`
- `persona_teaching`
- `agora_interaction_evidence`
- `human_imitation_shadow_evaluation`
- `consultation`
- `promotion_deployment`
- `capital_pool_execution`
- `telemetry_reconciliation`
- `evolution`
- `bff_health_monitoring`
- `per_persona_ooda`

## Declared artifacts

- `docs/04/pantheon_loop_product_level_remediation_2026-07-13/archive/closeout`
- `docs/deployment/evidence/loop-product-level/LOOP-PROD-CLOSE-002`

## Acceptance

- all 44 primary tasks and every required external dependency are done; superseded, cancelled, missing, or weaker substitute outcomes fail
- baseline Knowledge, Execution, Human Interaction, and Management Repair scenarios pass from a clean target-dev baseline
- worker exact-CAS, process/payload zero-member, environment lease, scheduler starvation, and corrupt-state matrices pass
- every accepted assertion verifies the protected trust root and exact FE/BFF/run/job/target/lease identities
- governed auth provisioning, scoped privileged capability, expiry, rotation, deactivation, and secret-isolation evidence passes with Human/Ops approval
- final execute-plans build and hosted desktop/mobile qualification are warning-free and within explicit bundle/performance/accessibility budgets
- twelve canonical loops plus OODA overlay have fresh controller truth, real terminal readback, restart/duplicate/failure evidence, and no registry-only maturity
- exact PRs, checks, merge/deploy identities, manifests, attestations, reviewer verdicts, residual risks, owners, and expiries are archived
- independent Human/Ops accepts zero unresolved blocking product risk

## Required proof

- clean target-dev full scenario rerun
- all additive adversarial and recovery matrices
- exact FE/BFF/image/lease/attestation identities
- checksummed evidence inventory and independent review
- explicit Human/Ops final verdict

Reviewer approval must set `review_file` under:

`docs/deployment/evidence/loop-product-level/LOOP-PROD-CLOSE-002/`

## Non-goals

- No panel-only closure
- No seed fixture as live proof
- No approval gate bypass
- No synthetic receipt as terminal execution proof
- No live-capital or live-broker side effect

## Dispatch and closeout rules

- `LOOP-PROD-CLOSE-001` alone is not program completion
- start only after every dependency is exactly done
- any stale, indirect, candidate-authored, or contradicted evidence fails closed
- only independent Human/Ops acceptance can complete the program
