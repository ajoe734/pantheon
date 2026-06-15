# Task Brief: CONSOLE-DATA-REGISTRY

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Populate /bff/skills,/bff/tools,/bff/mcp-servers,/bff/mcp-tools
- Status: done
- Owner: Claude
- Reviewer: Claude2
- Next: Owner finalized. PR #1689 merged into dev (CI 3/3 green). 4 BFF surfaces populated: skills=5, tools=4, mcp-servers=1, mcp-tools=4. 16 contract tests pass. ServiceBackedReadAdapter wiring, projection script, and governance dispatch stub confirmed clean.

## Summary
用 registry create API 註冊真 skill/tool/mcp-server/mcp-tool;接 PANTHEON_BFF_*_STORE 讀路徑。用該 domain 的真實 producer 產生真資料(禁止捏造);再重接 BFF 讀路徑(設 PANTHEON_BFF_*_STORE / 指向 live service / 加投影,如 scripts/project_research_to_bff_surfaces.py);驗收:live curl(Bearer op-dev:admin:mfa)該 /bff 面回 count>0 且 surface status=ok;在 services/control-plane/bff/tests 加/更新 contract test;stub dispatch 為 dev 安全姿態。範式見 docs/05/system-verification-rounds/console-population-research-slice.md。
