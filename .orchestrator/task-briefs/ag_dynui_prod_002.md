# Task Brief: AG-DYNUI-PROD-002

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Agora standalone workbench shell
- Status: review_approved
- Owner: Claude
- Reviewer: Claude2
- Next: Cycle-break closeout update: execute-plans PR #171 (67c0b0480d0999a2b8318c3d9ad44366f5b2f768) is merged via 467d930957bf109405fa50a5bc252ff66ec3a7ee, PR #173 is merged/deployed via hosted FE commit 691f2ec56af9bbc592814563558c001860d8bc7f, and pantheon PR #2955 merged hosted default-route desktop/mobile shell evidence under docs/deployment/evidence/ag-dynui-prod-003/20260704T123434Z/. That hosted evidence satisfies AG-DYNUI-PROD-002's shell screenshot closeout condition: it proves /agora/trading-room no longer lands in the old embedded Management/Trading Desk empty shell. AG-DYNUI-PROD-006 remains responsible for the full V11 hosted E2E workflow, but must not be used as a prerequisite for closing AG-DYNUI-PROD-002, because AG-DYNUI-PROD-006 depends on AG-DYNUI-PROD-005 and AG-DYNUI-PROD-005 depends on AG-DYNUI-PROD-002 done.

## Summary
修 execute-plans Agora shell 架構：/agora/* 不應只是被包在 Management PlatformShell 裡的 tab skeleton；建立符合設計稿的 Agora workbench shell 或提交明確批准的例外，並保留 auth/live 狀態。
