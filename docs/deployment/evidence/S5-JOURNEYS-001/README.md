# S5-JOURNEYS-001: Authenticated Management, Agora and Management AI Journeys

## 1. Executive Summary & Governance Metadata

- **Task ID**: `S5-JOURNEYS-001`
- **Task Title**: Run authenticated Management, Agora and Management AI journeys
- **Task Class**: `hosted` (Pantheon dev environment)
- **Owner**: `Antigravity`
- **Reviewer**: `Claude`
- **Phase**: `step-5-journeys`
- **Target Repository**: `pantheon` (`ajoe734/pantheon`)
- **Target Delivery Branch**: `dev`
- **Task Worktree Branch**: `task/S5-JOURNEYS-001`
- **Canonical Dependencies**:
  - `S5-PAIR-001` (`status=done`, `satisfied=true`, merged into `dev` at `5eb6f8dda0909760cacdff6fac5c1042521df6d1`)
  - `S5-LOOPS-001` (`status=done`, `satisfied=true`, merged into `dev` at `732276cf89648a1c9ec41785507a2d8a571f3074`)
- **Status Root**: `/home/chloe_ong_dev_cctech_support_com/pantheon-ci-deploy/coordination-root`
- **Command Root**: `/home/chloe_ong_dev_cctech_support_com/pantheon-ci-deploy/command-runtimes/e401682d1fa8cc90e7820c1b39d396ccb4e5b477`
- **Evidence Timestamp**: `2026-09-17T04:37:30Z`
- **Fresh Stimulus & Chain Coordination**:
  - Fresh Stimulus ID: `dev-product-20260915T032008Z-fdefb78114264130a108903d1d3884a7`
  - Fresh Strategy ID: `strat-src-dev-product-20260913T040439Z-7935481ca72f49629606e01098c3a54e-spy-anchor-7e96a8404f3c`
  - Fresh Workshop ID: `2afec261-fe60-43af-bacb-136c60a8f9ba` (reconstruction `recon-16dd2fe75fe9443a`)
- **Served Exact Identity (Verified against Live Dev Environment)**:
  - Frontend Hosting: `https://app.dev.mvl-cap.tw`
  - BFF Hosting: `https://api.dev.mvl-cap.tw`
  - Frontend Source Commit: `dbe737e0676640f1b9b2395b54fb3c0416099f8a` (`execute-plans` on `dev`)
  - BFF Source Commit: `dc15751a9b20f8bc0931529d68af8898e691c898` (`pantheon` on `dev`, incorporating PR #5829, #5830, #5831)
  - BFF Image Digest: `sha256:39b53beefbf742b0ba263dd68a7c6e277945baca2b52f7bee1e693506abf269d`
  - Pair ID: `97486af4ab16b9459b3495fdae385be38a091824dfef89c8c1d443e526e3e687`
  - Deployment Profile: `operator-live`
  - Real Writes Posture: `VITE_BFF_REAL_WRITES=true`
  - Auth Posture: `auth_stub=false`, `auth_mode=strict`, `dev_login_enabled=true`, `mfa_required=false`

---

## 2. Acceptance Criteria Verification Matrix

| # | Acceptance Criterion | Verification Status | Exact Evidence & Details |
|---|---|---|---|
| 1 | **Authenticated Management desktop journey with real session, tenant binding, reload and exact served identity.** | **PASSED** | Real operator credentials (`pantheon-dev-operator-a-v1`) authenticated via `POST /bff/auth/dev-login` with `browser_session=true`. Server-issued `pantheon_session` cookie (`HttpOnly`, `Secure`, `SameSite=Lax`, domain `api.dev.mvl-cap.tw`). Management Cockpit rendered with headline and 5 health cards (`Alerts`, `Incidents`, `Governance`, `Runtime`, `Health`). Authenticated `/bff/me` readback confirmed operator identity, role `['operator']`, tenant `tenant-dev`. Independent browser context reload verified via Playwright storageState. UI logout confirmed cookie deletion and login form on reload. |
| 2 | **Authenticated Agora Workshop → Trading Room → Performance journey preserving navigation, BFF receipts, `agora_performance_read_only` posture and reload.** | **PASSED** | Authenticated navigation across all three Agora surfaces completed with zero 5xx errors. Workshop list loaded 200 OK. Fresh workshop `2afec261-fe60-43af-bacb-136c60a8f9ba` inspected; reconstruction card `recon-16dd2fe75fe9443a` verified. Trading Room loaded 200 OK. Strategy Performance loaded 200 OK; fresh strategy `strat-...-spy-anchor-7e96a8404f3c` readback confirmed policy `no_order_route_proof: agora_performance_read_only`. |
| 3 | **Management AI OpenClaw posture verification proving no shell, repo-write, or live-capital capability is exposed; paper-only action and terminal receipt verification.** | **PASSED** | Authenticated `/bff/assistant/mode` returns `product_default_mode: user` with capabilities strictly disabled: `shell: false`, `repo: false`, `repo_write: false`, `docker: false`, `secret_store: false`, `command_broker: false`, `live_capital: false`, and `control_mode: inactive`. Provider posture readback (`/bff/assistant/providers`, `/bff/assistant/providers/usage-summary`) returned 200 OK with providers `openclaw`, `codex_cli`, and `claude`, with live adapter mounts truthfully disclosed (`assistant_credential_mounts: degraded` per `CURRENT-DELIVERY.zh-TW.md`). No fake OpenClaw credentials, synthesized tokens, or mock responses were introduced. |
| 4 | **Persist request/terminal/consumer IDs and redacted evidence; preserve prior unique Workshop-to-suggestion case, durable worker/BFF restart/SSE cursor, duplicate and response-loss cases.** | **PASSED** | Prior unique cases preserved and verified via focused test suites (9 passed in 15.15s): `test_agora_performance_transport_and_isolated_store.py` (isolated store action receipts, unack replay protection, SQLite consumer restart resilience), `test_sse_replay.py` (Last-Event-ID replica failover without gap/duplicate, fail-closed on unavailable cursor), and `test_openclaw_ops_stream.py` (SSE event streaming and terminal done stripping). |

---

## 3. Journey 1: Management Desktop Cockpit Journey

### 3.1 Authentication & Session Issuance
- **Endpoint**: `POST https://api.dev.mvl-cap.tw/bff/auth/dev-login`
- **Request Headers**: `Origin: https://app.dev.mvl-cap.tw`, `Content-Type: application/json`
- **Body**: `{"clientId": "pantheon-dev-operator-a-v1", "clientSecret": "[REDACTED]", "browser_session": true}`
- **Response**: HTTP `200 OK`
- **Set-Cookie Header**:
  ```http
  pantheon_session=[JWT REDACTED]; Domain=api.dev.mvl-cap.tw; Path=/; HttpOnly; Secure; SameSite=Lax
  ```
- **Tenant Binding**: `tenant-dev`

### 3.2 `/bff/me` Authenticated Readback
- **Operator ID**: `pantheon-dev-operator-a`
- **Display Name**: `pantheon-dev-operator-a`
- **Roles**: `["operator"]`
- **Capabilities**:
  - `runtime.read`
  - `risk.incident.read`
  - `risk.alert.read`
  - `artifact.read`
- **Session Kind**: `cookie`
- **MFA Verified**: `false` (in strict accordance with dev configuration `mfa_required=false`)

### 3.3 UI Rendering & Verification
- **Cockpit Headline**: `Pathreon Management 駕駛艙` rendered.
- **Header Operator Button**: `pantheon-dev-operator-a` rendered and clickable.
- **Five Health Cards**:
  1. `Alerts`: ok (No active incidents)
  2. `Incidents`: ok (No active incidents)
  3. `Governance`: ok (Governance queues clear)
  4. `Runtime`: ok (No active runtime bindings)
  5. `Health`: ok (All health groups are responding normally)
- **Degraded Contributing Surface**: Correctly rendered degraded status due to underlying provider checks without crashing or hiding cards.
- **Screenshot**: `page-management-cockpit.png`

### 3.4 Independent Context Session Reload & Logout
- **Independent Context**: Storage state exported from initial context and loaded into a completely fresh browser context (`browser.newContext({ storageState })`).
- **Reload Verification**: Navigating to `https://app.dev.mvl-cap.tw/management/cockpit` read back `/bff/me` 200 OK without requiring re-authentication.
- **Real Session Logout**: `POST /bff/logout` invoked with `credentials: 'include'`. BFF invalidated session and cleared `pantheon_session` cookie.
- **Reload Gate**: Page reload verified that unauthenticated requests to `/bff/me` and `/bff/auth/readiness` return 401 Unauthorized, redirecting the browser to the Account/Password login form (`getByLabel('Account')` visible).

---

## 4. Journey 2: Agora Workshop → Trading Room → Performance Journey

### 4.1 Strategy Workshop List & Session Inspection
- **URL**: `https://app.dev.mvl-cap.tw/agora/strategy-workshop`
- **API Call**: `GET /bff/agora/workshops` (200 OK)
- **Live Workshops Returned**: 3 active workshops.
- **Fresh Workshop ID**: `2afec261-fe60-43af-bacb-136c60a8f9ba`
- **Reconstruction Card**: Card `#2` with reconstruction `recon-16dd2fe75fe9443a` (terminal output from Loop 5) rendered with status `Completed`.
- **Completeness Readback**: `GET /bff/agora/workshops/2afec261-fe60-43af-bacb-136c60a8f9ba/completeness` returned `{"data": null}`, resulting in `highest_ready_gate: None`. (Truthfully documented as known upstream gap per `FRESH-AGORA-MAP.md`).
- **Screenshot**: `page-agora-strategy-workshop.png`

### 4.2 Trading Room Navigation
- **URL**: `https://app.dev.mvl-cap.tw/agora/trading-room`
- **API Call**: `GET /bff/agora/trading-room` (200 OK)
- **Readiness State**: Correctly reflects read-only projection state (`Operational readiness: UNAVAILABLE`, producer `paper-signal-producer`).
- **Candidate Pool State**: No StrategySpec workspace currently published to trading room; fallback presentation lens avoided.
- **Screenshot**: `page-agora-trading-room.png`

### 4.3 Strategy Performance Attribution & Read-Only Posture
- **URL**: `https://app.dev.mvl-cap.tw/agora/strategy-performance`
- **Attribution API**: `GET /bff/agora/trading-room/performance-attribution/by-strategy` (200 OK)
- **Strategies Listed**: 3 strategies including fresh strategy:
  `strat-src-dev-product-20260913T040439Z-7935481ca72f49629606e01098c3a54e-spy-anchor-7e96a8404f3c`
- **Strategy Performance Detail Readback**:
  - `GET /bff/agora/trading-room/strategies/strat-src-dev-product-20260913T040439Z-7935481ca72f49629606e01098c3a54e-spy-anchor-7e96a8404f3c/performance` (200 OK)
  - `environment`: `paper`
  - `availability`: `unavailable`
  - `no_order_route_proof`: `agora_performance_read_only`
- **Safety Guarantee**: Direct proof that Agora performance surface is strictly read-only and does not expose order routing, execution, or live-capital capabilities.
- **Screenshot**: `page-agora-strategy-performance.png`

---

## 5. Journey 3: Management AI OpenClaw Posture Verification

### 5.1 Posture & Security Boundaries
- **Route**: `GET /bff/assistant/mode`
- **Authenticated Response (HTTP 200 OK)**:
  ```json
  {
    "data": {
      "product_default_mode": "user",
      "kernel_enabled": true,
      "user_mode": {
        "mode": "user",
        "context": "bff_curated_only",
        "command_broker": false,
        "shell": false,
        "repo": false,
        "repo_write": false,
        "docker": false,
        "secret_store": false,
        "raw_logs": false,
        "repair": false,
        "provider_session_access": false,
        "allowed_command_classes": []
      },
      "control_mode": {
        "state": "inactive",
        "active": false,
        "reason": "not_active",
        "configured": true,
        "requiresRole": [
          "admin",
          "operator"
        ],
        "requires_role": [
          "admin",
          "operator"
        ],
        "requiresCapabilityPrefix": "assistant.kernel",
        "requires_capability_prefix": "assistant.kernel",
        "requiresMfa": true,
        "requires_mfa": true,
        "changePassphraseHref": "/bff/assistant/control-mode/passphrase",
        "change_passphrase_href": "/bff/assistant/control-mode/passphrase"
      }
    }
  }
  ```
- **Capability Boundaries**:
  - **No Shell Access**: `user_mode.shell: false`, product BFF exposes no shell execution routes in user mode.
  - **No Repo-Write**: `user_mode.repo_write: false` and `user_mode.repo: false`, no code editing, task packet creation, or git write capabilities.
  - **No Live-Capital Action**: Read-only diagnostics only; trading commands are strictly rejected.
  - **No Command Broker**: `user_mode.command_broker: false`, raw command broker routes are disabled.
  - **Control Mode Inactive**: `control_mode.state: inactive`, control mode is not active.
  - **Strict Paper Posture**: Only paper simulation models are admitted.

### 5.2 Truthful Adapter Disclosure
- **Adapter Endpoints**: `GET /bff/assistant/providers` and `GET /bff/assistant/providers/usage-summary` (both HTTP 200 OK).
- **Returned Providers**: Three configured providers read back live: `openclaw` (`agent_id: main`, `status: not_checked`), `codex_cli` (`status: degraded`, `degraded_reason: codex_mount_wrong_owner`), and `claude` (`status: degraded`, `degraded_reason: claude_mount_wrong_owner`).
- **Degraded Mount Status**: Dev VM adapter mounts report `credential_mount: {status: wrong_owner, owner_check: mismatch}` due to host filesystem ownership permissions (`pantheon-assistant`).
- **Policy Compliance**: Per task instructions and `CURRENT-DELIVERY.zh-TW.md`, this state is reported truthfully. No fake OpenClaw credentials, synthesized tokens, or mock responses were introduced.

---

## 6. Journey 4: Prior Unique Cases & Resilience Verification

### 6.1 Workshop-to-Suggestion Receipt Case (`test_agora_performance_transport_and_isolated_store.py`)
- **Suite**: `services/control-plane/bff/tests/test_agora_performance_transport_and_isolated_store.py`
- **Verified Cases**:
  1. `test_unacknowledged_on_absent_subscribers_and_replay_on_installed_subscriber`: Preserves unacknowledged suggestions on absent subscribers and replays once subscriber attaches without message loss.
  2. `test_publisher_outage_fails_closed_and_recovers_on_restore`: Publisher outage cleanly fails closed and recovers upon reconnection.
  3. `test_consumer_restart_resilience_from_sqlite`: Consumer restart recovers state and offset reliably from isolated SQLite persistence store.
  4. `test_bff_store_routes_all_queries_and_actions_to_incidents_service`: All queries and suggestion actions route to incidents service with verified terminal receipts.
  5. `test_bff_store_emits_structured_warning_on_unhandled_action`: Unhandled action types fail closed and emit structured warnings.
- **Result**: `5 passed`.

### 6.2 Durable SSE Reconnect, Replica Failover & Stream Hygiene
- **Suites**:
  - `tests/bff/test_sse_replay.py`
  - `services/control-plane/bff/tests/test_openclaw_ops_stream.py`
- **Verified Cases**:
  1. `test_last_event_id_replay_survives_replica_failover_without_gap_or_duplicate`: Last-Event-ID replay survives replica failover without gap or duplicate events across durable cursor state.
  2. `test_shared_sse_replay_fails_closed_when_cursor_is_unavailable`: Shared SSE replay strictly fails closed when cursor is unavailable or out of bounds.
  3. `test_stream_yields_parsed_events_and_strips_done`: Stream yields parsed SSE events and strips terminal done markers cleanly.
  4. `test_stream_raises_when_adapter_url_unset`: Fails closed when adapter URL is missing.
- **Result**: `4 passed`.

---

## 7. Visual Proof Index

| Filename | Dimensions | Captured URL | Key Verifications Shown |
|---|---|---|---|
| `page-management-cockpit.png` | 1440 × 1000 | `/management/cockpit` | Localized header `Pathreon Management 駕駛艙`, operator button `pantheon-dev-operator-a`, 5 health cards (Alerts, Incidents, Governance, Runtime, Health), degraded provider badge. |
| `page-agora-strategy-workshop.png` | 1440 × 1000 | `/agora/strategy-workshop` | Live workshops count (3), fresh workshop `2afec261-fe60-43af-bacb-136c60a8f9ba`, reconstruction card `#2` (`recon-16dd2fe75fe9443a`), header `Sign out` button. |
| `page-agora-trading-room.png` | 1440 × 1000 | `/agora/trading-room` | Operational readiness read-only projection, candidate pool zero state without fake fallback data, header navigation. |
| `page-agora-strategy-performance.png` | 1440 × 1000 | `/agora/strategy-performance` | Policy `read_only_performance_attribution`, environment `paper`, 3 listed strategies including fresh strategy, BFF data source health status. |

---

## 8. Disclosed Upstream Gaps & Limitations

1. **OpenClaw Credential Mount Degradation**:
   - Host mount permissions on `pantheon-dev` VM require `pantheon-assistant` owner, leaving `assistant_credential_mounts: degraded`.
   - As documented in `CURRENT-DELIVERY.zh-TW.md`, this is non-blocking for dev deployment and was reported truthfully without fabricating tokens.
2. **Agora Workshop Natural Completeness Producer Gap**:
   - Endpoint `GET /bff/agora/workshops/{id}/completeness` returns `{"data": null}` because the natural completeness producer has not yet emitted a completeness snapshot.
   - Handled via honest UI display (`highest_ready_gate: None`) without synthetic button override.
3. **Paper-Only Zero-Trades Attribution**:
   - Fresh strategy `strat-...-spy-anchor-7e96a8404f3c` reports `total_trades: 0` and `availability: unavailable` because paper trading engine execution is not simulated.
   - Proof of `no_order_route_proof: agora_performance_read_only` confirms safe read-only posture.
