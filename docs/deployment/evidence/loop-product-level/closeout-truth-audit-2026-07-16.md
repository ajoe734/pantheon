# Loop Product Closeout Truth Audit

- Audit ID: `closeout-truth-audit-2026-07-16`
- Generated at: `2026-07-16T13:27:00Z`
- Mode: `read_only_archive_snapshot_replay`
- Source root: `/home/lupin/code/pantheon/ai-task-archive/tasks`
- Archive mutation: `none`
- Scanned: 18
- Passed: 2
- Failed: 16

## Results

| Task | Verdict | Classification | Follow-up | Gaps |
|---|---:|---|---|---|
| `LOOP-PROD-AGORA-001` | `fail` | `false_closure` | `LOOP-PROD-AGORA-001-FALSE-CLOSEOUT-REPAIR` | missing reviewer verdict: no approved formal reviewer verdict recorded in record_log |
| `LOOP-PROD-AGORA-002` | `fail` | `false_closure` | `LOOP-PROD-AGORA-002-FALSE-CLOSEOUT-REPAIR` | evidence overall admission is not done-eligible: review_required_evidence_only<br>missing reviewer verdict: no approved formal reviewer verdict recorded in record_log |
| `LOOP-PROD-ALPHA-001` | `fail` | `false_closure` | `LOOP-PROD-ALPHA-001-FALSE-CLOSEOUT-REPAIR` | missing reviewer verdict: no approved formal reviewer verdict recorded in record_log |
| `LOOP-PROD-AUTH-001` | `fail` | `false_closure` | `LOOP-PROD-AUTH-001-FALSE-CLOSEOUT-REPAIR` | evidence overall admission is not done-eligible: blocked_pending_human_provisioned_deploy_secrets_and_hosted_redeploy<br>blocking residual risk 'RISK-LOOP-PROD-AUTH-001-REDEPLOY-PENDING' remains open<br>blocking residual risk 'RISK-LOOP-PROD-AUTH-001-DEV-LOGIN-CREDENTIALS' remains open<br>blocking residual risk 'RISK-LOOP-PROD-AUTH-001-IMAGE-DIGEST' remains open |
| `LOOP-PROD-CAP-001` | `fail` | `false_closure` | `LOOP-PROD-CAP-001-FALSE-CLOSEOUT-REPAIR` | product evidence schema validation failed: 'mutation_rule' is a required property<br>missing reviewer verdict: no approved formal reviewer verdict recorded in record_log |
| `LOOP-PROD-CONS-001` | `fail` | `false_closure` | `LOOP-PROD-CONS-001-FALSE-CLOSEOUT-REPAIR` | evidence overall admission is not done-eligible: review_required_evidence_only |
| `LOOP-PROD-DEP-001` | `fail` | `stale_evidence` | `LOOP-PROD-DEP-001-STALE-EVIDENCE-REPAIR` | product-level closeout review_file must be an evidence.json manifest: docs/deployment/evidence/loop-product-level/LOOP-PROD-DEP-001/artifact-index.json |
| `LOOP-PROD-DIST-001` | `fail` | `false_closure` | `LOOP-PROD-DIST-001-FALSE-CLOSEOUT-REPAIR` | evidence overall admission is not done-eligible: review_required_evidence_only |
| `LOOP-PROD-GAP-ADDENDUM-001` | `fail` | `stale_evidence` | `LOOP-PROD-GAP-ADDENDUM-001-STALE-EVIDENCE-REPAIR` | product-level closeout requires a review_file evidence manifest |
| `LOOP-PROD-GAP-ADDENDUM-002` | `fail` | `stale_evidence` | `LOOP-PROD-GAP-ADDENDUM-002-STALE-EVIDENCE-REPAIR` | product-level closeout requires a review_file evidence manifest |
| `LOOP-PROD-IMIT-001` | `fail` | `false_closure` | `LOOP-PROD-IMIT-001-FALSE-CLOSEOUT-REPAIR` | evidence overall admission is not done-eligible: review_required_evidence_only<br>missing reviewer verdict: no approved formal reviewer verdict recorded in record_log |
| `LOOP-PROD-MAI-001` | `fail` | `false_closure` | `LOOP-PROD-MAI-001-FALSE-CLOSEOUT-REPAIR` | evidence manifest owner mismatch: expected Antigravity, got Codex<br>evidence overall admission is not done-eligible: blocked_strict_auth_posture_and_hosted_proof_not_available<br>blocking acceptance requirement ID 'AC-01': status is 'blocked_hosted_not_run'<br>blocking acceptance requirement ID 'AC-02': status is 'blocked_local_contract_only'<br>blocking acceptance requirement ID 'AC-03': status is 'blocked_hosted_chain_not_run'<br>blocking acceptance requirement ID 'AC-04': status is 'blocked_hosted_negative_matrix_not_run'<br>blocking acceptance requirement ID 'AC-05': status is 'blocked_hosted_restart_proof_not_run'<br>blocking acceptance requirement ID 'AC-06': status is 'pending_pr_checks_merge_and_independent_review'<br>blocking acceptance requirement ID 'AC-07': status is 'blocked_authoritative_terminal_readback_absent'<br>blocking acceptance requirement ID 'AC-08': status is 'blocked_hosted_rpo0_and_rollback_proof_absent'<br>blocking acceptance requirement ID 'AC-09': status is 'blocked_task_candidate_not_deployed'<br>blocking acceptance requirement ID 'AC-10': status is 'blocked_hosted_security_matrix_incomplete'<br>blocking residual risk 'RISK-LOOP-PROD-MAI-001-STRICT-SECRETS-DEPLOY' remains open<br>blocking residual risk 'RISK-LOOP-PROD-MAI-001-HOSTED-LIFECYCLE' remains open<br>blocking residual risk 'RISK-LOOP-PROD-MAI-001-RESTART-RPO0' remains open<br>blocking residual risk 'RISK-LOOP-PROD-MAI-001-DELIVERY-REVIEW' remains open<br>blocking residual risk 'RISK-LOOP-PROD-MAI-001-DEPLOYMENT-IDENTITY' remains open<br>missing security evidence: rbac status is not pass/not_applicable<br>missing security evidence: tenant_isolation status is not pass/not_applicable<br>missing security evidence: mfa status is not pass/not_applicable<br>missing security evidence: no_live_capital status is not pass/not_applicable<br>missing security evidence: two_person_approval status is not pass/not_applicable<br>missing reviewer verdict: no approved formal reviewer verdict recorded in record_log |
| `LOOP-PROD-OODA-001` | `fail` | `false_closure` | `LOOP-PROD-OODA-001-FALSE-CLOSEOUT-REPAIR` | missing reviewer verdict: no approved formal reviewer verdict recorded in record_log |
| `LOOP-PROD-REC-001` | `pass` | `valid_closure` | `` | none |
| `LOOP-PROD-RUNTIME-BOOT-001` | `fail` | `stale_evidence` | `LOOP-PROD-RUNTIME-BOOT-001-STALE-EVIDENCE-REPAIR` | product-level closeout review_file must be an evidence.json manifest: docs/deployment/evidence/loop-product-level/LOOP-PROD-RUNTIME-BOOT-001/evidence.premerge.json |
| `LOOP-PROD-SRC-001` | `fail` | `false_closure` | `LOOP-PROD-SRC-001-FALSE-CLOSEOUT-REPAIR` | evidence manifest owner mismatch: expected Antigravity, got Codex2<br>evidence manifest reviewer mismatch: expected Claude, got Codex<br>evidence overall admission is not done-eligible: review_required_evidence_only<br>blocking acceptance requirement ID 'AC-05': status is 'pending_independent_review_and_merge'<br>missing reviewer verdict: no approved formal reviewer verdict recorded in record_log |
| `LOOP-PROD-TEACH-001` | `pass` | `valid_closure` | `` | none |
| `LOOP-PROD-TEL-001` | `fail` | `false_closure` | `LOOP-PROD-TEL-001-FALSE-CLOSEOUT-REPAIR` | missing reviewer verdict: no approved formal reviewer verdict recorded in record_log |

## Excluded Sources

- `/home/lupin/code/pantheon/ai-task-archive/tasks/LOOP-PROD-000.json`: governance/meta task excluded from frozen product-closure replay set
- `/home/lupin/code/pantheon/ai-task-archive/tasks/LOOP-PROD-001.json`: governance/meta task excluded from frozen product-closure replay set
- `/home/lupin/code/pantheon/ai-task-archive/tasks/LOOP-PROD-002.json`: governance/meta task excluded from frozen product-closure replay set
