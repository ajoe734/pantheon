# BE Response — Lupin Dev BFF Access (VM 重建後) — 2026-05-31

回覆 FE/Lovable 的 `BE_DEV_ACCESS_REQUEST_2026-05-30 (rev 2)`。

**TL;DR：四項交付物全部完成並 live 驗證通過。dev BFF 已可連。** Agent 可以開始跑
`scripts/probe-*.mjs` 收尾。

## §2.1 — 新 BFF URL（live, verified）

```yaml
bff_dev_url:         https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io
bff_dev_ipv4:        35.201.239.38
bff_dev_health_path: /health
bff_dev_ready_at:    2026-05-31T12:51Z   # verified HTTP 200, live now
```

> URL 結構不變（`pantheon-lupin-dev-bff.<ip>.sslip.io`），只是 IP 從舊的
> `34.81.75.241` 換成新的 `35.201.239.38`。請更新 `.env.development.example` /
> `.env.example` / `src/lib/bff-v1/paths.ts` 的預設值。

### 根因（給 FE 參考）

VM 搬到新 GCP 專案（`pantheon-benjamin-20260528`）後，VM 上的 Caddy 反向代理
`/etc/caddy/Caddyfile` 仍寫著**舊** IP 的 sslip.io hostname，所以 Caddy 對新 SNI
拿不到憑證，TLS handshake 直接回 `tlsv1 alert internal error`（alert 80）——
這就是「連 GET 都掛」的原因。已修正並讓 Caddy 重新簽發 Let's Encrypt 憑證。
為避免每次重建 VM 重演，Caddyfile 已模板化進 repo（`deploy/caddy/`）並接進
cutover 腳本。

## §2.2 — 防火牆入站白名單（已通，無需額外動作）

新 VM 的 :443 inbound 已開放——Let's Encrypt 的 tls-alpn-01 challenge 從多個外部
IP 成功驗證，憑證已簽發。Sandbox egress（`34.147.96.0/24`）走公開 HTTPS，不需要
額外 allow-list（非 IP 限制型 ingress）。

驗證：
```
$ curl -sS -m5 https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/health
{"status":"ok","service":"operator-bff","version":"0.2.0","timestamp":"..."}
```

## §2.3 — CORS Allow-Origin（已綁，verified 204）

`operator-bff` 容器的 `PANTHEON_BFF_CORS_ORIGINS` 已含你列的全部實際 origin，且 dev
tier（`PANTHEON_ENV=dev`）啟用 preview-origin regex（涵蓋 hashed
`id-preview-<commit>--<uuid>.lovable.app` 形式）。實測 preflight OPTIONS：

| Origin | 結果 |
|---|---|
| `https://id-preview--b75d3452-…-1061de45b347.lovable.app` | **204** + ACAO echo + `allow-credentials: true` |
| `https://b75d3452-…-1061de45b347.lovableproject.com` | **204** |
| `https://pantheon-dev.lovable.app` | **204** |
| `https://id-preview-<hash>--b75d3452-…lovable.app`（hashed） | **204**（regex 命中） |
| `https://evil.example.com`（負向） | **400**（正確擋掉，非萬用 `*`） |

回應 header：
```
Access-Control-Allow-Methods: GET, POST, PUT, PATCH, DELETE, OPTIONS
Access-Control-Allow-Credentials: true
Access-Control-Allow-Headers: authorization, content-type, accept, accept-language,
  x-bff-api-version, x-correlation-id, idempotency-key, …
```

> 註：你 wishlist 裡標「可選」的 `*.lovableproject.com` / `*.sandbox.lovable.dev`
> 萬用尚未加（目前是精確 + 已知 UUID regex）。三個必要 origin 都已通；若 sandbox
> 換到 `*.sandbox.lovable.dev` 形式的 origin 需要支援，再開一張單，我加進 regex。

## §2.4 — Auth bearer（仍有效，無需換 token）

dev BFF 在 `permissive` auth mode + stub 開啟，舊 bearer 直接可用：

```
$ curl https://…/bff/me -H 'Authorization: Bearer pantheon-dev-browser:reviewer'
→ 200
  operator_id: pantheon-dev-browser
  roles:       ["reviewer"]
  capabilities:["approval.read","strategy.view","persona.view"]
  auth_mode:   stub   strict_auth: false   tenant: pantheon-dev
$ curl https://…/api/v1/personas -H 'Authorization: Bearer pantheon-dev-browser:reviewer'  → 200
$ curl https://…/bff/me   (無 bearer)                                                       → 401
```

`pantheon-dev-browser:reviewer` 解析為 `reviewer` role（read + dry-run write），不回
401。**不需要換新 token。**

## 給 FE 的收尾清單

- [ ] 更新 `.env.development.example` / `.env.example` / `paths.ts` 的 BFF base URL
- [ ] 跑 `node scripts/probe-bff-write-paths.mjs` / `probe-persona-onboarding-endpoints.mjs` / `probe-create-persona-then-fleet.mjs`
- [ ] 結果寫入 `.lovable/audits/be-write-gap-verification-2026-05-31.md`
- [ ] 全綠後依原計畫撤 `writeFallback.ts` NOT_IMPLEMENTED 分支 / `LiveStatusBanner` writeDegraded strip

---
*BE owner verification timestamp: 2026-05-31T12:51Z (dev `/health` → 200)*
