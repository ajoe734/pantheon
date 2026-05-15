#!/usr/bin/env bash
# File FE-INT-GATE-OIDC-DEV-LOGIN.
# Background: lupin dev BFF was flipped to strict JWT auth (likely via
# BFF-CONSOL-022/023 cutover). The previously valid build-time fallback
# "pantheon-dev-browser:reviewer" now returns INVALID_TOKEN /
# AUTH_TOKEN_FORMAT and breaks: (a) CI auth_smoke probe; (b) hosted Lovable
# bundle BFF calls; (c) e2e specs that need /bff/me to return a valid
# MeResponse. Need a CI-friendly path to mint a short-lived JWT.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

TASK_PHASE="Pantheon FE Integration Gate 2026-05-13" \
TASK_BRANCH="bff-luv-fe-006-dev-deploy" \
TASK_DEPENDS_ON="BFF-CONSOL-022" \
TASK_SUMMARY_ZH="lupin dev BFF (https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io) 已切到 strict JWT auth。當前狀態：(1) curl -H \"Authorization: Bearer pantheon-dev-browser:reviewer\" /bff/me 回 401 INVALID_TOKEN/AUTH_TOKEN_FORMAT；(2) openapi 只暴露 /bff/auth/refresh，沒有 /login 或 /token endpoint；(3) hosted Lovable bundle 用 VITE_BFF_DEV_BEARER_TOKEN=pantheon-dev-browser:reviewer 也全 401；(4) CI 的 PANTHEON_BFF_SMOKE_BEARER_TOKEN 失效，auth_smoke step 全 fail。需求：設計 CI-friendly dev OIDC login flow 讓 hard-gate auth_smoke 重新綠。實作方案請 backend owner 在以下 3 條路徑選或混合：(A) BFF 新增 /bff/auth/dev-login endpoint，接 client_id+secret 換短期 JWT (5min~1hr)，CI 之前 step 先 fetch 一次塞到 env；(B) OIDC issuer (Keycloak/Auth0) staging instance 跑 client_credentials grant，CI 拿 issuer URL + dev client secret 自己換 token；(C) BFF 提供 pre-minted long-lived test JWT (例：90 天)，當 GitHub repo secret，CI 直接用。優先順序 A > B > C（A 最安全 + 短 TTL + 可 revoke）。同時要更新：(i) execute-plans/.github/workflows/pantheon-integration-gate.yml 加 'Acquire BFF JWT' step (在 auth_smoke / e2e 前)；(ii) Lovable dev project 環境變數從 VITE_BFF_DEV_BEARER_TOKEN=stub 改成 VITE_BFF_OIDC_CLIENT_ID+CLIENT_SECRET 或 runtime fetch；(iii) GitHub repo secret 新增 PANTHEON_BFF_OIDC_CLIENT_ID / CLIENT_SECRET (替換 PANTHEON_BFF_SMOKE_BEARER_TOKEN)；(iv) execute-plans/scripts/probe-bff-authenticated-live.mjs 讀新 env var；(v) docs/deployment/lovable-dev-staging-operating-rules.md 更新 dev auth 方案說明。Verification: 重跑 PR CI auth_smoke + browser_probe step outcome=success；hosted bundle 對 /bff/me 不再 401；chair Claude 收 JWT 在 staging-live 環境驗證不可用 (security boundary)。" \
TASK_ARTIFACTS="services/control-plane/bff/auth.py,services/control-plane/bff/main.py,execute-plans/.github/workflows/pantheon-integration-gate.yml,execute-plans/scripts/probe-bff-authenticated-live.mjs,execute-plans/.env.integration.example,docs/deployment/lovable-dev-staging-operating-rules.md,docs/deployment/bff-oidc-staging-auth.md" \
TASK_ACCEPTANCE="dev BFF 提供 client_credentials 或等效 dev-login flow,CI workflow 在 auth_smoke 前自動取得 JWT 並注入 env,GitHub secret 改用 client_id/client_secret 而非 long-lived bearer,Lovable hosted bundle 能取得 valid JWT (build-time secret 或 runtime fetch),auth_smoke step CI outcome=success,browser_probe step CI outcome=success,e2e F01 MeResponse assertion 可拿到 valid MeResponse,security: JWT TTL <=1hr 並可 revoke,security: dev JWT 在 staging-live BFF 拒絕 (環境隔離),docs 更新 dev auth 方案" \
python3 scripts/ai_status.py assign FE-INT-GATE-OIDC-DEV-LOGIN Codex2 Claude "Dev BFF OIDC short-lived JWT for CI + hosted Lovable"

echo "Done: FE-INT-GATE-OIDC-DEV-LOGIN assigned."
