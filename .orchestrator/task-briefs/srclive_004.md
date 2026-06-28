# Task Brief: SRCLIVE-004

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: 疊加層回歸測試 + 三 persona e2e live 驗收
- Status: review_approved
- Owner: Codex
- Reviewer: Claude2
- Next: Supervisor resumed SRCLIVE-004 for finalize after successful dispatch.

## Summary
替整個 live readback 機制上回歸護欄並做端到端 live 驗收。工作:(1) BFF 合約測試:當 source-ingest 健康快照含某 connector status:ok 時_overlay_source_health_truth 必把對應 provider 翻 read_ok 並設 source_health_source=source_ingest;當快照缺該 connector 時必維持靜態 read_unavailable/credential_unavailable(不可假綠)——TW/US/Crypto 三類各一案;(2) e2e:對 dev BFF /bff/management/persona-fleet 實打,斷言 persona-tw-equity=5/5、persona-us-equity ibkr+4free=read_ok 且 polygon/alphavantage=credential_unavailable、persona-crypto coingecko=read_ok;(3) 把驗收腳本收進 scripts/ 供日後一鍵重驗。依賴 001/002/003 完成。

[設計規則] 唯讀疊加層 _overlay_source_health_truth 是真相來源:provider 翻 read_ok 的唯一合法路徑是(1) BFF _SOURCE_PROVIDER_CONNECTOR_CANDIDATES 有 provider_key→connector_id 對照,且(2) source-ingest /api/source-ingest/health-usage-snapshot 回報該 connector status:ok。嚴禁硬寫 read_ok 或假綠;沒有即時健康就誠實顯示 credential_unavailable / read_unavailable 並附 reason。

## Closeout Evidence
- Implementation PRs:
  - https://github.com/ajoe734/pantheon/pull/2539 - BFF overlay regression tests and initial readback verifier; merged at `87c382c779869c8920a73aa794f308c9acb8046c`.
  - https://github.com/ajoe734/pantheon/pull/2548 - source-ingest job-parameter entrypoint needed by live public-source activation; merged at `80ae5544591dad98d2fb1a25fe45fcb9f5abbb26`.
  - https://github.com/ajoe734/pantheon/pull/2554 - verifier tolerance for source-only Stooq proof; merged at `f353139ed446d97946a7745a3aaf0a5ca8a634b6`.
- Owner closeout PR: https://github.com/ajoe734/pantheon/pull/2557
- Delivered implementation commits:
  - `f8ffc8e3` `SRCLIVE-004: repair readback verifier and public source fetch`
  - `b7d05568` `SRCLIVE-004: accept source ingest job parameters`
  - `6124a70b` `SRCLIVE-004: tolerate source-only Stooq readback`
- Reviewer approval: Claude2 moved SRCLIVE-004 to `review_approved` in task state at `2026-06-28T17:48:34Z`. Review notes approve the 5-case BFF overlay tests, design-rule enforcement, `credential_unavailable` guard coverage, and verifier script placement under `scripts/`.
- Owner verification on 2026-06-28:
  - `python3 -m py_compile scripts/verify_srclive_readback.py` passed.
  - `python3 -m pytest services/control-plane/bff/test_srclive_overlay_contract.py -q` passed: 5 passed, 4 warnings.
  - `python3 scripts/verify_srclive_readback.py --json` passed against the dev BFF. Observed BFF source-health source was `source_ingest` for `persona-tw-equity`, `persona-us-equity`, and `persona-crypto`; TW providers were 5/5 `read_ok`; US had `ibkr`, `sec_edgar`, `finra`, and `fred` as `read_ok`, `polygon` and `alphavantage` as `credential_unavailable`, and an additional non-required `yahoo` row as `read_unavailable`; crypto had `coingecko` as `read_ok`.
  - `python3 scripts/verify_srclive_readback.py --source-ingest-base http://35.201.239.38:38097 --json` did not complete because the direct public source-ingest port timed out. This was treated as a diagnostic endpoint reachability note, not a SRCLIVE-004 acceptance failure, because SRCLIVE-004's required e2e surface is the dev BFF `/bff/management/persona-fleet` projection and that projection reported source-ingest-backed truth.
