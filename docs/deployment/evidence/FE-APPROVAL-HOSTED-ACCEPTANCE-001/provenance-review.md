# Provenance review: FE-APPROVAL-HOSTED-ACCEPTANCE-001

Task: REVIEW-PROVENANCE-CORRECTION-20261007, 2026-10-07. Subject: `evidence.json` and `merged-review.md` in this directory (the pantheon mirror of the execute-plans record), as archived. Neither is edited. The execute-plans copy (PR #810) is not touched.

Sources: probe scripts and verbatim outputs, `identity_capture.txt`, `ha_404.out` and the three round files in `/tmp/pantheon-review-provenance-20261007/fe-approval` (not in the repository); deploy run 37105169338 and its job 111152143624 log, run 37123107209, execute-plans integration gate run 37106391202, PR metadata, and execute-plans source at `86a4314b`, all via `gh`; pantheon source at `9304d19c` via `git show`.

Classes: **confirmed**, **unsupported** (not checkable from available material, not "false"), **contradicted**.

## Evidence claims

| # | Claim | Class | Basis |
|---|---|---|---|
| 1 | Authorization: operator chat 2026-10-03, scope list, final-vote decision | unsupported | Chat attestation. Timing is consistent with outputs: persona create snapshot 09:57:33Z, final vote `decided_at` 10:38:44Z |
| 2 | `worker_access: none`; transport (in-container loopback, secrets not printed) | unsupported for access; confirmed for transport | `ha_common.py` calls `127.0.0.1:8001` and reads secrets from env without printing |
| 3 | Deploy run 37105169338, `nonprod-deploy.yml`, backend `9304d19c`, FE `86a4314b` | confirmed | Run succeeded, title and head SHA match |
| 4 | Integration gate run 37106391202 | confirmed | execute-plans run, success, head `86a4314b` |
| 5 | Profile `operator-live`, pair id, `accepted` at 2026-10-03T08:09:05Z | confirmed against the coordinator capture | `identity_capture.txt` served manifest; consistent with the run's final job completing 08:09:35Z. Live URLs were not re-fetched; the release has since moved (run 37123107209 deployed `5743158b0` at 12:30Z) |
| 6 | Served BFF identity: commit `9304d19c`, image digest `193acb2e...`, `auth_mode` strict | confirmed against the coordinator capture | Same capture; not independently re-observable now |
| 7 | Governance image id `42ef75c3...`, empty revision label, manifest export at job log line 13788 07:10:43Z, TARGET_REF line 53, checkout ref line 149 | confirmed | Re-read from the job 111152143624 log: lines 53, 149 and 13788 match. Image id and empty label are from the capture |
| 8 | Observation window 11:00:47Z to 11:05:03Z | confirmed | Matches the two capture timestamps |
| 9 | Fixture: persona, pool, binding ids, name, tenant, paper mode, HTTP 201 | confirmed | `ha_capital_create.out` |
| 10 | Existing dev pools unchanged; no real-money effects | unsupported | Coordinator attestation, labelled as such. The create response shows `live_capital_side_effects: false` |
| 11 | Fixture shared with CAPITAL-FORWARD-HOSTED-ACCEPTANCE-001 | confirmed | Persona mandate text says so |
| 12 | Binding "already active" per the provisioning receipt | unsupported | The step name `persona_capital_binding_active` appears in the receipt step list, but the BFF binding list was unavailable and readiness showed `active_binding_count` 0 |
| 13 | Approval ids, target type/id, risk medium, subject | confirmed | `ha_approval.out` |
| 14 | Only capital_pool_activation, capital_binding_activation and rebalance_apply with increase need two deciders | confirmed | `approval_targets.py` `required_deciders` at `9304d19c` |
| 15 | After each accepted vote the BFF publishes `approval.stage.changed` / `approval.decided` | confirmed | `router.py` `_publish_decision` at `9304d19c`, called at line 1126 |
| 16 | No consumer applies this decision | unsupported | Stated in the file as an unverified coordinator assessment |
| 17 | Steps: propose 201 v1; readback pending/proposed; stale 409 (expected 0, observed 1); first vote 202 under_review v2; retry 202 identical; changed memo under K1 409; same decider 400 | confirmed | `ha_approval.out`: codes, messages, versions, event ids match. The state after the same-decider vote matches the final GET |
| 18 | Final vote 202 by risk_owner: decided/approved v3, event `fe804bac`, `decided_at` 10:38:44Z, two approvers; identical retry equal; readback on both routes; vote after finality 400 | confirmed | `ha_approval_final.out` |
| 19 | Unknown approval id returned 409 stale at the released pair | confirmed | `ha_approval.out` |
| 20 | Contract says 404 for unknown ids (contract.md 376-377) | confirmed | `contract.md` at `9304d19c`, line 376 |
| 21 | By source the command is rejected before any write; other-tenant not tested | partly unsupported | Not tested in the outputs, source not re-read here; the file already says the write absence was not observed |
| 22 | Frontend `decideApproval` posts `{decision, memo, stageName, expected_version}` to `/approvals/{id}/decide`, reusing the key on retry (`writes.ts:1158-1180`, `paths.ts:88`) | confirmed | Fetched from execute-plans at `86a4314b` |
| 23 | Capital reads unavailable on this release (pool 404, binding list unavailable) | confirmed | `ha_capital_readback1.out`. The task name `BFF-CAPITAL-OWNER-READS-20261003` is unchecked |
| 24 | Acceptance results "met" and the listed limitations (no browser run, failed readback not exercised) | confirmed as consistent with outputs | Derived statements; all recorded readbacks succeeded |
| 25 | Review: "independent review by Codex2 (codex-cli 0.153.0, read-only, run by the coordinator)", rounds REJECT 7, REJECT 3, ACCEPT | confirmed as to verdicts and counts; contradicted as to "independent" | Round files show REJECT with 7 numbered findings, REJECT with remaining findings, ACCEPT. Coordinator-started runs, no journal review event, `CODEX_HOME` unset so likely `~/.codex` |
| 26 | PR and commit identifiers: pantheon #6117 (`1f61ecc29`, `d6bcdce07`), #6119 (`f4836aab3`), execute-plans #810 (`5d230426`) | confirmed | `gh` PR commit lists |

## Additional observation: unknown-id behaviour on a later build

`ha_404.out` records an unknown approval id returning **404** `RESOURCE_NOT_FOUND` on both decide and GET, on 2026-10-03 against `5743158b0` (nonprod-deploy run 37123107209, 12:30-13:32Z). The evidence's 409 claim is correct for the released pair `9304d19c`/`86a4314b`, so this is not a contradiction. It does mean the "recorded only" deviation (follow-up in `evidence.json`) may already be gone on later builds. The output has no timestamp or SHA inside the file; the build is taken from the coordinator's description.

## Contradicted claims and follow-ups for the operator

Nothing is fixed in place.

1. Review provenance (claim 25 and the brief wording "Codex2 reviewed independently"): not independent or dispatched. Addressed by the appended section in `merged-review.md`.
2. The unknown-id 409-vs-404 follow-up is recorded only; later-build evidence suggests it was fixed. Operator decides whether to close or open a task.
3. Claims 1, 2, 10, 12, 16 and 21 stay coordinator attestations.
4. Served identities (claims 5-8) are coordinator captures and cannot be re-observed because the deployed pair has changed.
