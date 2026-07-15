# Task Brief: EVOCHAIN-010

Generated in the worker workspace because the supervisor root did not have a task brief file.
This file records the task-scoped wakeup context used for Codex2 closeout.

## Task
- Title: Producer-chain live verifier
- Status at dispatch: review_approved
- Owner: Codex2
- Reviewer: Claude
- Next at dispatch: Auto-reassigned ownership from Codex to Codex2 after repeated Codex auth: Authentication failure

## Summary
寫 producer-chain live 驗證（test the verb）：對 dev 注入或觸發一筆真 threshold breach，斷言 incident（含 dedupe key）出現、sweep 產出 proposal、journal 出現 formal entry、Persona Fleet 最近 MUTATION 連到該 entry。納入 scripts/run_e2e_verifiers.sh。驗證失敗要能分辨斷在哪一段。

## Closeout Evidence

- Approved implementation is already merged to `dev` by PR #3716
  (`20d4a61a0`).
- Codex2 finalization re-read the task brief, approved artifacts, and
  runbook before creating the closeout commit.
- Focused regression passed:
  `python3 -m pytest -q scripts/test_verify_e2e_producer_chain.py`
  (11 tests).
