# Provenance review: OSS-STORAGE-INVENTORY-20261002

Task: REVIEW-PROVENANCE-CORRECTION-20261007, 2026-10-07. Subject: `evidence.json` and `merged-review.md` in this directory, as archived. Neither is edited.

Sources: coordinator raw material copied to `/tmp/pantheon-review-provenance-20261007/oss-storage` (not in the repository); PR #6105 comments via `gh`; nonprod-deploy run 37035071230 and its job log via `gh`; the deployed compose at `f3e267c1` via `git show`. The original 2026-10-02 MinIO command outputs were not saved, so claims that rest only on them are marked unsupported. Unsupported means "not checkable from available material", not "false".

Classes: **confirmed** (matches a source), **unsupported** (no source available), **contradicted** (a source says otherwise).

## Evidence claims

| # | Claim | Class | Basis |
|---|---|---|---|
| 1 | Authorization granted 2026-10-02 23:28-23:32Z in chat, before the first command | unsupported | Chat attestation; no saved record. The 2026-10-02 reviewer round 2 flagged an earlier date inconsistency (10-03 vs 10-02); the file now says 10-02 |
| 2 | `worker_access: none` | unsupported | Coordinator attestation |
| 3 | VM `pantheon-dev-deploy`, project `pantheon-dev-20260902` | confirmed | Asserted from GCE metadata in the 10-04T05:05Z PR comment |
| 4 | Deployed backend `f3e267c1` at 2026-10-02T23:35Z | confirmed (circumstantial) | Job log of run 37035071230 names it as observed live BFF at 16:37Z on 10-02. The nonprod-deploy run list shows no successful deploy between 16:36Z on 10-02 and 04:03Z on 10-03 |
| 5 | MinIO containers, states ("Up 4 weeks (healthy)"), `pantheon-minio-1` / `minio-init-1` | unsupported | State is VM output not saved. Image digest prefix `14cea493d9a3`, init image `minio/mc:RELEASE.2024-01-16T16-06-34Z` and volume `minio-data` are confirmed in the compose at `f3e267c1` |
| 6 | Volume 188390 bytes, 30 files, 0 files outside `.minio.sys` | unsupported | Original output not saved. 0 non-system files and 30 `.minio.sys` files are corroborated by the 10-03T02:17Z PR comment (30 files, 32761 bytes by stat). The byte totals differ (188390/180198 here, 32761 there); likely du vs stat, not reconciled by any source |
| 7 | Bucket `pantheon-artifacts` created 2026-09-02T11:47Z, 0 objects, 0 versions, 4096 bytes | unsupported for the 10-02 observation | Creation time and 4096 bytes have no source. Zero objects/versions are corroborated at 10-03T02:17Z and 23:58Z |
| 8 | 18 compose services set `PANTHEON_S3_ENDPOINT`; 17 also set `PANTHEON_ARTIFACT_BUCKET`; persona endpoint only; the 18 names | confirmed | Parsed from the compose at `f3e267c1`: 18 endpoint, 17 bucket, difference is `persona`, names identical |
| 9 | Running stack resolves the same 18 settings (printenv across 54 containers, 10-03T00:06Z) | unsupported | Raw output not saved. The PR comment of 10-03T02:20Z independently reports 18 configured consumers |
| 10 | 0 established sockets on port 9000 at 10-03T00:06Z | unsupported | Raw output not saved |
| 11 | Workflow input `DEV_MANAGEMENT_AI_ATTACH_BUCKET` empty, location asia-east1 in run 37035071230 | confirmed, with caveat | Job log lines 12819-12820. That run failed and was for release `6bf0f719`; `f3e267c1` appears in it only as the previous/live SHA. It supports the input value, not "the run that deployed f3e267c1" |
| 12 | Compose key `PANTHEON_MGMT_AI_ATTACH_BUCKET` defaults empty | confirmed | `${PANTHEON_MGMT_AI_ATTACH_BUCKET:-}` at `f3e267c1`, docker-compose.yml:1351 |
| 13 | Running operator-bff attach bucket unset at 10-03T00:00Z | unsupported | Raw output not saved. Corroborated for 10-04T05:05Z by the third PR comment |
| 14 | `gcloud storage buckets list` from VM SA `429489276340-compute` returned 0 buckets, rc=0 | unsupported | Original output not saved; SA id has no source. 0 buckets via the VM identity at 10-03T23:55Z is confirmed by the 10-04T00:03Z comment. The 10-03T02:17Z comment says a local attempt returned 403 and did not confirm the count |
| 15 | Paper binding projections stored inline in runtime-manager `/data/runtime/runtime_bindings.json`, not MinIO | unsupported | Only the file path appears in source (`scripts/tw_signal_producer.py`); the inline-projection claim itself is unchecked |
| 16 | The first round pulled one read-only alpine image | unsupported | No raw output. Not contradicted: the 10-03T02:20Z comment says "no image pulls" for that later round |
| 17 | Supplementary round 1 (10-03T02:17:13Z): 0 live objects/bytes/versions/delete markers, 0 non-system files, 30 `.minio.sys` files 32761 bytes, 18 consumers, no GCS binding, local gcloud 403 | confirmed | Matches comment 2026-10-03T02:20:29Z |
| 18 | Supplementary round 2 (10-03T23:55:59Z-23:58:22Z): metadata identity first, 0 buckets, MinIO 0, 36 system files 37401 bytes, 20 consumers, no GCS binding | confirmed | Matches comment 2026-10-04T00:03:54Z |
| 19 | Supplementary round 3 (10-04T05:05:18Z): identity, Compute Engine ADC, `devstorage.read_only`, no attachment bucket, fallback dir absent, source scan at `0e520cb` | confirmed against the comment | Matches comment 2026-10-04T05:07:43Z. The source scan itself was not re-run here |
| 20 | `supplementary_rounds_note`: raw files were in coordinator `/tmp`, not in the repository | confirmed | Consistent with the comments naming `/tmp` and home paths |
| 21 | Operator decision text, `decided_at` 2026-10-05, accepted as complete for MinIO retirement | confirmed (text and date) | Text matches `original_next.txt`. The exact 12:36:47Z write time cited in `source` is not in the saved material: unsupported |
| 22 | `not_done`: a bucket provisioning plan authorized in chat on 2026-10-02 was never executed | unsupported | Labelled as a coordinator attestation in the file. The 10-06 round 1 run rejected it for having no source; it was accepted only after relabelling |
| 23 | Acceptance 3 recorded MET per the operator decision; GCS hosted verification NOT APPLICABLE | confirmed as a faithful record of the decision | The decision text says so. Whether the operator's decision satisfies the original acceptance 3 wording (backup/restore proof, GCS contracts) is a judgment, not a fact check |
| 24 | `independent_review`: rounds and finding counts (Codex2 5 / 3 / ACCEPT; Antigravity2 4 / ACCEPT) | confirmed as to verdicts and counts; contradicted as to "independent" | Round files show REJECT with 5 findings, REJECT with 3 outstanding findings, ACCEPT; agy REJECT with 4 findings, ACCEPT. All were coordinator-started CLI runs with no journal review event |
| 25 | Reviewer identities "Codex2 (codex-cli 0.153.0, read-only)" and "Antigravity2 (agy, gemini-3.8-flash-high ...)" | unsupported | Round files do not record the model name or CLI version. `CODEX_HOME` unrecorded for 10-02. Agy home `~/.gemini-agy2` is from the coordinator's description |
| 26 | Date range "2026-10-02_to_03" for the Codex2 rounds | unsupported | Saved files are dated 2026-10-02 only |
| 27 | `conclusion`: dev object stores hold no application data | confirmed for the observed snapshots | Supported at the 10-03 and 10-04 observations; the file itself says history of writes and deletes is not observable |

## Merged-review.md claims

| Claim | Class |
|---|---|
| Delivery 3817e77ae, brief 6b9487e7c, PR #6105 merge 1db1c6d13 | confirmed (git; PR #6105 merged 2026-10-06T15:47:11Z) |
| Canonical reviewer reassigned from Codex2 to Antigravity2 on 2026-10-06 | unsupported (task record not available here) |
| "Antigravity2 reviewed independently" | contradicted: coordinator-started CLI run, no journal event |
| Round 1 REJECT, 4 findings, next field overwritten; round 2 ACCEPT | confirmed against the 10-06 round files |
| "Earlier rounds on 2026-10-02 and 2026-10-03, by Codex2" | partly unsupported (dates), reviewer label unsupported as a home |
| "MinIO empty in every round; no GCS bucket in the project; local fallback absent" | confirmed for the rounds with raw sources (10-03T02:17Z, 10-03T23:58Z, 10-04T05:05Z); the 10-02 round is unsupported |
| "No hosted change was made" | unsupported beyond the PR comments, which say no writes, deletions, IAM or restart in the later rounds |

## Contradicted claims and follow-ups for the operator

Nothing is fixed in place. The operator decides on each.

1. Review provenance (claims 24, 25 and the brief's wording): the review was not independent or dispatched. Addressed by the appended section in `merged-review.md`. Follow-up: decide whether the archived status should stand on this task's real review.
2. Unsupported 10-02 observations (claims 5-7, 9, 10, 13-16): the original outputs were not saved. Later timestamped rounds corroborate empty state but not the 10-02 values. Follow-up: accept as coordinator attestation or leave marked.
3. Claim 11 cites a failed deploy run for a different release. Follow-up: consider rewording the source citation in a future correction.
4. Byte totals 188390/180198 vs 32761 and 37401: not reconciled by a source.
5. The acceptance-3 MET judgment (claim 23) rests on the operator decision; the original task wording asked for backup/restore and GCS contract proof.
