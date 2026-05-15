# Review: OSS-NEXT-001 - Governed Qlib Adapter Rereview

Reviewer: Codex
Date: 2026-04-17
Status: changes_requested

## Findings

1. High - The shipped Qlib version truth is internally inconsistent, so the governed evidence pack would record the wrong upstream version. [`services/research/qlib/requirements.txt`](/home/lupin/code/pantheon/services/research/qlib/requirements.txt:1) now resolves with `pyqlib>=0.9.6`, but the adapter still hard-codes `QLIB_VERSION_PIN = "0.9.1"` and emits that value into artifact metadata and the real-backend model payload at [`services/research/qlib/adapter/qlib_adapter.py`](/home/lupin/code/pantheon/services/research/qlib/adapter/qlib_adapter.py:18), [`services/research/qlib/adapter/qlib_adapter.py`](/home/lupin/code/pantheon/services/research/qlib/adapter/qlib_adapter.py:332), and [`services/research/qlib/adapter/qlib_adapter.py`](/home/lupin/code/pantheon/services/research/qlib/adapter/qlib_adapter.py:449). The canonical evidence repeats the stale `0.9.1` claim in [`OSS_INTEGRATION_CHECKLIST.md`](/home/lupin/code/pantheon/OSS_INTEGRATION_CHECKLIST.md:39) and [`integrations/qlib/integration.md`](/home/lupin/code/pantheon/integrations/qlib/integration.md:15). Until the locked version strategy and emitted metadata agree, this task should not be treated as fully governed or review-approvable.

## Verified

- `python3 -m unittest discover -s services/research/qlib -p 'test_*.py'` -> `Ran 13 tests ... OK`
- `python3 services/research/qlib/smoke_test.py` -> passes with `backend=stub_lgbm`, `artifact_state=draft`, `deployment_stage=none`
- `python3 scripts/ai_status.py show OSS-NEXT-001` -> confirms this rereview is for the active `review` task owned by `Claude`

## Residual Notes

- The runtime path itself looks structurally sound after the `requirements.txt` fix and doc cleanup. The blocker is version-governance drift, not the stub smoke path.
- If the intent is to keep a reproducible pin, `requirements.txt`, `QLIB_VERSION_PIN`, and the evidence docs all need the same exact version. If the intent is to allow a range, the code and docs need to stop claiming a locked single-version selection and should record the resolved version instead.
