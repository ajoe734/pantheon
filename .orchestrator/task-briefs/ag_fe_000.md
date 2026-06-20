# Task Brief: AG-FE-000

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Separate Agora/Management entry, build, auth audience
- Status: in_progress
- Owner: Claude
- Reviewer: Codex
- Next: Builds verified: npm run build:agora and build:management both pass. Agora bundle confirmed clean of management UI components and management routes. Auth audiences separated (pantheon-agora / pantheon-management). Pre-existing test failures in client.test.ts and managementAssistant.test.ts not caused by this task. Ready to handoff to Codex for review.

## Summary
依 SD §3.1/§23.1 在 execute-plans 把 Agora 與 Management 拆成兩個獨立 app entry/build:新增 agora-main.tsx/management-main.tsx、agora.html/management.html、vite.agora.config.ts/vite.management.config.ts 與 dev/build/test/gate npm scripts;設定 VITE_APP_KIND 與 VITE_AUTH_AUDIENCE。Agora production bundle 不得含 /management route code。先做 Phase 1 不搬目錄,Phase 2 monorepo 另議。
