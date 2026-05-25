# OPS-BFF-LUPIN-DEV-REDEPLOY-20260525 Redeploy Evidence

Recorded: 2026-05-25T03:20Z
Owner: Codex
Reviewer: Claude

## Target

- Public BFF: `https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io`
- VM: `pantheon-lupin-dev`
- GCP project: `pantheon-lupin-20260502`
- VM service account: `292583053341-compute@developer.gserviceaccount.com`
- Deploy worktree: `/home/lupin/pantheon-ci-deploy/dev-root`
- Deployed git SHA: `9304c09cd84cbd2f1bf7a1f7fc5f0e6b21c89a21`

## IAM / v2 Blocker Check

The v2 failure was reported as missing `compute.instances.get` while trying to
reach the lupin project through `gcloud compute ssh`.

This worker is already running on the target VM, so the successful path was a
local Docker Compose redeploy from the managed deploy worktree. No `gcloud
compute ssh` or project-level self-grant was needed for this run.

Observed credential state from this worker:

- `gcloud config list` selected `lupinchen@cctech-support.com` and project
  `pantheon-lupin-20260502`.
- `gcloud projects describe pantheon-lupin-20260502` and
  `gcloud compute instances list --project=pantheon-lupin-20260502` failed
  before IAM evaluation because the user credential requires interactive
  reauthentication.
- Metadata ADC was available for
  `292583053341-compute@developer.gserviceaccount.com`, but
  `gcloud --access-token-file=<metadata-token> compute instances list` reported
  missing `compute.instances.list`.

If an off-VM operator needs to repair only the observed `compute.instances.get`
gap, the minimal viewer grant is:

```bash
gcloud projects add-iam-policy-binding pantheon-lupin-20260502 \
  --member='user:lupinchen@cctech-support.com' \
  --role='roles/compute.viewer'
```

SSH may additionally require the project's chosen OS Login or SSH-metadata IAM
posture, as documented in `docs/deployment/nonprod-ci-cd.md`.

## Redeploy

Commands run locally on `pantheon-lupin-dev`:

```bash
cd /home/lupin/pantheon-ci-deploy/dev-root
git fetch --recurse-submodules=no origin dev --quiet
git checkout --detach 9304c09cd84cbd2f1bf7a1f7fc5f0e6b21c89a21
git submodule update --init --recursive
COMPOSE_PROFILES=activation-ready-smoke,dormant-smoke,openclaw,openclaw-activation-ready-e2e,search-index-scheduler,smoke,source-ingest-scheduler,source-search-bounded \
  docker compose -p pantheon -f docker-compose.yml config --quiet
COMPOSE_BAKE=false \
COMPOSE_PROFILES=activation-ready-smoke,dormant-smoke,openclaw,openclaw-activation-ready-e2e,search-index-scheduler,smoke,source-ingest-scheduler,source-search-bounded \
PANTHEON_ENV=dev \
PANTHEON_LIVE_BROKER_ENABLED=false \
PANTHEON_BFF_CORS_ORIGINS=https://pantheon-dev.lovable.app,https://pantheon-ai-system-front-dev.lovable.app,https://preview--pantheon-dev.lovable.app,https://b75d3452-f667-4cf4-893a-1061de45b347.lovableproject.com,https://id-preview--b75d3452-f667-4cf4-893a-1061de45b347.lovable.app,https://140c41d5-9cd8-4d6b-ba02-66d5941d0dbe.lovableproject.com \
PANTHEON_BFF_AUTH_STUB=true \
PANTHEON_BFF_AUTH_MODE=permissive \
  docker compose -p pantheon -f docker-compose.yml up -d --build operator-bff
```

Post-deploy runtime:

```text
container_id=714f3d309e70ad82a8e7991bbb7e8495ec4167a1f229e413ec4abf67deb379f5
image=pantheon-operator-bff
image_id=sha256:c67c65fe07a46de96adbdad494665a48bd897a0c6f8c6174318a03cb3d4c2cfa
created=2026-05-25T03:18:12.011115306Z
health=healthy
started=2026-05-25T03:18:43.899589686Z
```

Local health checks:

- `http://127.0.0.1:18001/health` returned 200.
- `http://127.0.0.1:18001/readyz` returned 200 with runtime-manager,
  governance, and deployment dependencies `ok`.

## CORS

Command shape:

```bash
curl -D - -o /tmp/body -X OPTIONS "$BASE/bff/me" \
  -H "Origin: $ORIGIN" \
  -H "Access-Control-Request-Method: GET" \
  -H "Access-Control-Request-Headers: Authorization, Content-Type, X-BFF-Api-Version, X-Request-Id"
```

Preflight results:

| Origin | Status | ACAO | ACAM | ACAH | ACEH |
|---|---:|---|---|---|---|
| `https://pantheon-dev.lovable.app` | 204 | exact origin | includes `GET, POST, PUT, PATCH, DELETE, OPTIONS` | includes `Authorization`, `Content-Type`, `X-BFF-Api-Version`, `X-Request-Id` | missing on OPTIONS |
| `https://pantheon-ai-system-front-dev.lovable.app` | 204 | exact origin | includes `GET, POST, PUT, PATCH, DELETE, OPTIONS` | includes `Authorization`, `Content-Type`, `X-BFF-Api-Version`, `X-Request-Id` | missing on OPTIONS |
| `https://b75d3452-f667-4cf4-893a-1061de45b347.lovableproject.com` | 204 | exact origin | includes `GET, POST, PUT, PATCH, DELETE, OPTIONS` | includes `Authorization`, `Content-Type`, `X-BFF-Api-Version`, `X-Request-Id` | missing on OPTIONS |
| `https://140c41d5-9cd8-4d6b-ba02-66d5941d0dbe.lovableproject.com` | 204 | exact origin | includes `GET, POST, PUT, PATCH, DELETE, OPTIONS` | includes `Authorization`, `Content-Type`, `X-BFF-Api-Version`, `X-Request-Id` | missing on OPTIONS |

Actual authenticated `GET /bff/me` response results:

| Origin | Status | ACAO | ACEH |
|---|---:|---|---|
| `https://pantheon-dev.lovable.app` | 200 | exact origin | `X-BFF-Api-Version, X-Correlation-Id, X-Request-Id` |
| `https://pantheon-ai-system-front-dev.lovable.app` | 200 | exact origin | `X-BFF-Api-Version, X-Correlation-Id, X-Request-Id` |
| `https://b75d3452-f667-4cf4-893a-1061de45b347.lovableproject.com` | 200 | exact origin | `X-BFF-Api-Version, X-Correlation-Id, X-Request-Id` |
| `https://140c41d5-9cd8-4d6b-ba02-66d5941d0dbe.lovableproject.com` | 200 | exact origin | `X-BFF-Api-Version, X-Correlation-Id, X-Request-Id` |

Note: Starlette's preflight response does not emit
`Access-Control-Expose-Headers`; the BFF emits it on actual CORS responses.

## Audit Path Curl Results

Base command shape:

```bash
curl "$BASE/<path>" -H "Authorization: Bearer pantheon-dev-browser:reviewer"
```

| Label | Method | Path | Auth | Status | Result |
|---|---|---|---|---:|---|
| `reviewer_batch_decide` | POST | `/bff/approvals/batch-decide` | `pantheon-dev-browser:reviewer` | 403 | Route is live; reviewer lacks `approver` or `admin` role. Error code is Pack D `FORBIDDEN`. |
| `approver_batch_decide` | POST | `/bff/approvals/batch-decide` | `pantheon-dev-browser:approver` | 207 | Route is live; request reached per-item validation but dev read surface has no approval record `appr-dec-c5a9f11e`. Error code is Pack D `RESOURCE_NOT_FOUND`. |
| `command_confirmation_status` | GET | `/bff/command-confirmations/confirm-gap-005` | reviewer | 200 | Returned `status=available`. |
| `management_cockpit` | GET | `/bff/management/cockpit` | reviewer | 200 | Returned `data.id=management-cockpit`. |
| `persona_league_rankings` | GET | `/bff/management/persona-league/rankings` | reviewer | 200 | Returned ranking blocks. |
| `persona_league_movers` | GET | `/bff/management/persona-league/movers` | reviewer | 200 | Returned `data.id=management-persona-league-movers`. |
| `quarterly_ranking` | GET | `/bff/management/quarterly-ranking?quarter=2026-Q2` | reviewer | 200 | Returned `data.id=pm12-quarterly-ranking-2026-q2`. |
| `performance_attribution` | GET | `/bff/management/performance-attribution` | reviewer | 200 | Returned `data.id=pm12-performance-attribution`. |
| `portfolio_book` | GET | `/bff/management/portfolio-book` | reviewer | 200 | Returned portfolio summary. |

No target audit path returned HTTP 404 or 500.

## Pack D Live Check

Because `origin/dev` advanced while this task was running, the final redeploy
includes PR #559 (`BFF-INFRA-ERRORCODE-PACKD-001`). Live check:

```bash
curl "$BASE/bff/strategies/__nonexistent__" \
  -H "Authorization: Bearer pantheon-dev-browser:reviewer"
```

Result:

```text
HTTP 404
error.code=RESOURCE_NOT_FOUND
```

Container import check:

```text
ErrorCode.RESOURCE_NOT_FOUND exists
ErrorCode.OBJECT_NOT_FOUND absent
```

## Disposition

The stale 2026-05-22 BFF image was replaced with an image built from
`origin/dev` `9304c09cd84cbd2f1bf7a1f7fc5f0e6b21c89a21`, and the public lupin
dev BFF is healthy.

Reviewer caveats:

- The task text asked for ACEH on OPTIONS preflight. The deployed code emits
  ACEH on actual CORS responses, not preflight responses.
- The task text asked for `POST /bff/approvals/batch-decide` with reviewer auth
  to return 200. The deployed route is live, but existing RBAC requires
  `approver` or `admin`; the dev approval dataset is empty, so even an approver
  token reaches route validation and returns 207 rather than an accepted command.
