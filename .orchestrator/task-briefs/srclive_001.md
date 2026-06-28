# Task Brief: SRCLIVE-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: TW 官方源 live 啟用 (twse/tpex/mops)
- Status: todo
- Owner: Claude2
- Reviewer: Claude
- Next: Chair reassigned owner from Codex to Claude2: SRCLIVE-001 is blocked because dev root deploy run #28319397634 failed due to a dirty managed-VM worktree (docker-compose.yml, bff/main.py, and others). Codex cannot self-resolve the VM state. Claude2 is listed as the only viable rescue target (targets=["Claude2"]) and should attempt: (1) investigating whether the dirty worktree files can be stashed or removed at the deploy-script level without direct SSH, (2) finding an alternative fresh-worktree deployment path, or (3) producing a minimal ops repair instruction to unblock acceptance within minutes. Task is not task_class: human_gate. Task returned to todo for a blocked-owner rescue dispatch.

## Summary
啟用 TW 官方 source-ingest 連接器 twse/tpex/mops 的 live dev run，驗證 health-usage-snapshot 回報 ok 與 row_count，並產出可重跑 runbook。

## Claude2 Rescue Dispatch Analysis (2026-06-28)

**Blocker resolution**: dirty managed-VM worktree CAN be fixed at the deploy-script level without direct SSH.

Analysis of `scripts/deploy_nonprod_vm.sh`:
- `require_clean_checkout()` stashes known runtime-state paths first, then fails on remaining dirty tracked files
- `docker-compose.yml` and `bff/main.py` are tracked files NOT in the known runtime-state list → they block the checkout step
- The `--allow-dirty` path (line 449) runs `git stash push --include-untracked` on ALL remaining dirty changes, then proceeds with a clean checkout
- This path is exposed via the `allow_dirty: true` input on the `Pantheon Nonprod Deploy` GitHub Actions workflow

**Repair instruction** (no SSH required):
Re-run `Actions → Pantheon Nonprod Deploy` with `environment=dev`, `component=root`, `allow_dirty=true`.
The workflow will stash dirty files, deploy cleanly, and run the BFF smoke automatically.

**Post-deploy**: source-ingest runs at `http://127.0.0.1:38097` on the VM (not publicly exposed).
Activation commands must be run via `gcloud compute ssh pantheon-lupin-dev --command="..."`.
Full commands are in `docs/05/srclive/tw-activation-runbook.md` § VM Dirty Worktree Repair.

**Remaining for owner**: human/ops must trigger the GitHub Actions repair run; then the activation
curl commands need to run from the VM. AC-1 through AC-6 from the sidecar acceptance packet
(support/sidecars/SRCLIVE-001/SRCLIVE-001-SIDECAR-ACCEPTANCE.md) must all be satisfied.
