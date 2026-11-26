# OSS Actions and Container-Image Version Evidence — 2026-11-12

Task: OSS-ACTIONS-IMAGES-EVIDENCE-20261112 · Owner: Claude · Reviewer: Codex
Supplements merged PR7208 / `OSS-CURRENT-VERSION-EVIDENCE-20261112` (`docs/reviews/2026-11-12-oss-current-version-audit.md`), which explicitly excluded GitHub Actions and container-image declarations. The original report and its package results are unchanged.

This is **documentation only**. Everything below is a *declaration* observed in frozen Git blobs compared with public official release observations. It is not proof of active compose profiles, active workflows, installed/deployed versions, or compatibility. Nothing was installed or upgraded, no workflow/dependency/application file was edited, no credentials/provider/VM was used, and no hosted completion is claimed. The seven original retired catalog tasks stay retired; this is not a new release prerequisite or gate.

## Inputs and identity

Five nonsecret inputs under `/tmp/pantheon-goal-resume-20261111/` (read-only). SHA256 values were recomputed and match the supplied values:

| File (`…-20261112.json`) | SHA256 |
|---|---|
| `oss-actions-images-current-comparison` | `6f58cf2c311a1d99d403d9d69c4c6b2c7bd31c6a1087919b008f7efe7e251c52` |
| `oss-actions-images-declarations` | `83345992a5ea9d25047839fe5446396d02528a5da59906e612a5956b7d76352c` |
| `oss-actions-images-official-releases` | `b136c2a91e9edc87bbe143fa0f47665abd47b57725cd22b7658e179dda54ce12` |
| `oss-fe-checkout-41hex-candidates` | `d33b4d27ad2db85d5f478f0e254cc04a8741d348650a5679ddec8d767be1b57e` |
| `oss-fe-checkout-upstream-resolution` | `9914a3e5c1413554d7faf260464720037f54a6f0bf2ac353490098b376844772` |

Every one of the 194 action and 123 image declaration lines was re-read from the exact Git blob (`git show <commit>:<path>`) of its snapshot and matched the declared text (0 mismatches); the four checkout candidate lines also matched.

## Bounded snapshots and counts

| Repository | Commit | Candidate files | Action decl. | Image decl. |
|---|---|---|---|---|
| `ajoe734/pantheon` | `8999306d1cc30e3fa36b09ee7cdccb1c5431aa52` | 89 | 135 | 123 |
| `ajoe734/execute-plans` | `aa17cd4fc390e2c311ef50cbd5f7d2160faea775` | 13 | 59 | 0 |
| **Total** | | **102** | **194** | **123** |

- Candidate files: tracked workflows, Dockerfiles and compose files only (backend 89 / frontend 13).
- Actions: 194 declarations, all remote (**0 local actions** in these snapshots); 9 distinct remote Action repositories; 18 distinct repository/ref groups.
- Images (backend only): 70 compose `image:` + 53 Dockerfile `FROM` = 123. Classes: **55 dynamic variables** (27 distinct `${PANTHEON_*_IMAGE}` variables, resolved at deploy time), **3 internal images**, **8 external digest-pinned**, **57 external tag-only**; 41 distinct declared strings.

## Official release observations

16 public requests (`releases/latest` per repository, observed 2026-11-12T04:34:01Z, no retry): 15 confirmed, 1 not confirmed. All confirmed entries are stable (`prerelease=false`, `draft=false`). Full URLs and publication timestamps are in the audit JSON and evidence.json.

| Upstream | Latest stable tag | Published (UTC) |
|---|---|---|
| actions/checkout | v7.0.1 | 2026-07-20 |
| actions/dependency-review-action | v5.0.0 | 2026-05-08 |
| actions/github-script | v9.0.0 | 2026-04-09 |
| actions/setup-node | v7.1.0 | 2026-10-08 |
| actions/setup-python | v7.1.0 | 2026-10-22 |
| actions/upload-artifact | v7.0.2 | 2026-10-07 |
| google-github-actions/auth | v3 | 2025-09-03 |
| google-github-actions/setup-gcloud | v3.0.1 | 2025-08-28 |
| jlumbroso/free-disk-space | v2.0.0 (URL resolves under `lumbroso-commons`) | 2026-09-17 |
| caddyserver/caddy | v2.11.9 | 2026-11-05 |
| DIYgod/RSSHub | v2026.11.12-8633.c86f1d5 | 2026-11-12 |
| openclaw/openclaw | v2026.11.3 | 2026-11-10 |
| nats-io/nats-server | v2.15.3 | 2026-11-11 |
| qdrant/qdrant | v1.20.0 | 2026-10-22 |
| redis/redis | 8.10.3 | 2026-10-26 |
| **pgvector/pgvector** | **not confirmed** — `releases/latest` returned HTTP 404 | — |

pgvector: the official README install tag `v0.8.7` is recorded as documentation evidence only, not as a release observation. `pg16` in `pgvector/pgvector:pg16` is the PostgreSQL major, not the extension version.

Other official sources: Python 3.15.2 is latest stable and 3.11/3.12 remain security-maintained branches; PostgreSQL 18.6 is latest stable, 19 is RC1 (not stable), and PostgreSQL 16 remains supported (current minor 16.15, support ends 2028-11-09); BusyBox homepage marks 1.38.0 unstable, latest explicit stable announcement is 1.36.1.

## Disposition: Actions (18 repository/ref groups)

Dispositions: **candidate** = declared release family differs from observed latest (compatibility/runner requirements unevaluated); **unknown** = commit-SHA or `main` pin whose equivalence to a release tag was not resolved; **current-supported-baseline** = declared version is on a supported branch. No action group is classed current-supported-baseline, because no official support policy was observed for them.

| Repo | Upstream | Declared ref | Count | Latest | Disposition |
|---|---|---|---|---|---|
| pantheon | actions/checkout | `v6` | 50 | v7.0.1 | candidate |
| pantheon | actions/checkout | `v4` | 2 | v7.0.1 | candidate |
| pantheon | actions/checkout | `34e11487…` | 10 | v7.0.1 | unknown |
| pantheon | actions/upload-artifact | `v4` | 9 | v7.0.2 | candidate |
| pantheon | actions/upload-artifact | `ea165f8d…` | 15 | v7.0.2 | unknown |
| pantheon | actions/setup-python | `v6` | 32 | v7.1.0 | candidate |
| pantheon | actions/setup-node | `v4` | 1 | v7.1.0 | candidate |
| pantheon | actions/dependency-review-action | `v4` | 1 | v5.0.0 | candidate |
| pantheon | google-github-actions/auth | `c200f369…` | 4 | v3 | unknown |
| pantheon | google-github-actions/setup-gcloud | `e427ad8a…` | 4 | v3.0.1 | unknown |
| pantheon | jlumbroso/free-disk-space | `main` | 7 | v2.0.0 | unknown |
| execute-plans | actions/checkout | `v4` | 21 | v7.0.1 | candidate |
| execute-plans | actions/checkout | `34e11487…` (40-hex) | 1 | v7.0.1 | unknown |
| execute-plans | actions/checkout | `34e11487…a` (41-hex) | 4 | v7.0.1 | unknown |
| execute-plans | actions/setup-node | `v4` | 9 | v7.1.0 | candidate |
| execute-plans | actions/setup-node | `49933ea5…` | 5 | v7.1.0 | unknown |
| execute-plans | actions/upload-artifact | `v4` | 12 | v7.0.2 | candidate |
| execute-plans | actions/github-script | `v7` | 7 | v9.0.0 | candidate |

Counts: 10 candidate groups, 8 unknown groups. File/line locations for every declaration are in the audit JSON (`action_groups[].locations`).

Remaining checks (not done here): resolve each pinned SHA to a tag, read upstream release notes for breaking changes (e.g. Node runtime, runner minimum version, artifact v4→v7 behavior), and confirm which workflows are actually enabled and triggered.

## Disposition: container images

| Declared | Count | Observed latest | Disposition | Remaining checks |
|---|---|---|---|---|
| `python:3.11-slim`, `python:3.12-slim` | 51 | Python 3.15.2 | current-supported-baseline | exact patch/digest; app compatibility with newer minors |
| `postgres:16-alpine@sha256:cf78e766…` | 2 | PostgreSQL 18.6 | current-supported-baseline | digest content/minor; no major upgrade or migration implied |
| `pgvector/pgvector:pg16` | 1 | not confirmed | unknown | actual extension version in image |
| `busybox:1.36@sha256:73aaf090…` | 1 | 1.36.1 stable; 1.38.0 unstable | unknown | digest-to-patch/platform; 1.38.0 is not a target |
| `caddy:2.8-alpine` | 1 | v2.11.9 | candidate | active use, config compatibility, image content |
| `nats:2.11-alpine@sha256:e4bf19f1…` | 2 | v2.15.3 | candidate | active profile, config compatibility, digest content |
| `redis:7-alpine@sha256:ff02b58f…` | 3 | 8.10.3 | candidate | active profile, client/persistence compatibility |
| `qdrant/qdrant:v1.13.4` | 1 | v1.20.0 | candidate | active profile, storage/client compatibility |
| `diygod/rsshub:chromium-bundled-2026-11-01` | 1 | v2026.11.12-8633.c86f1d5 | unknown | dated image tag vs release mapping |
| `ghcr.io/openclaw/openclaw:2026.7.1` | 2 | v2026.11.3 | candidate | adapter compatibility, active use |
| internal: `pantheon-collector-worker:1.0.0`, `pantheon-openclaw-gateway:2026.7.1`, `pantheon-openclaw-activation-ready-e2e-adapter:latest` | 3 | n/a | unknown | owner classification; no upstream |
| 27 `${PANTHEON_*_IMAGE}` variables | 55 | n/a | unknown | resolved identities not in declarations |

Totals 123. Image content and digests were not inspected; no upgrade or retirement is inferred from these differences.

## Rejected hypothesis: 41-hex checkout refs

Four `execute-plans` declarations use `actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5a` (41 hex; `agora-hosted-acceptance.yml:70`, `pfg-mgmt-journey-e2e-hosted-acceptance.yml:58`, `ppl-alloc-009-hosted-acceptance.yml:65`, `srcm-p1-mgmt-ui-hosted-acceptance.yml:61`). The unauthenticated upstream commits API returned **HTTP 200 for both** the 41-hex and the 40-hex ref and resolved both to `34e114876b0b11c390a56381ad16ebd13914f8d5` (no retry). So the hypothesis that these workflows fail because of ref length is **not demonstrated**; this report does not assert they fail and creates no repair task. How the Actions runner itself treats a 41-hex ref was not tested.

Three of the four workflows carry default input URLs on `*.sslip.io` hosts and one on `*.dev.mvl-cap.tw` hosts. These are declaration evidence only; actual caller/owner classification is required before any deletion.

## Missing coverage and limits

- Declarations ≠ active profiles, enabled workflows, installed or deployed versions.
- SHA-pinned actions were not resolved to tags; release-only lookup.
- Image digests/manifests/content, internal images and dynamic variable values unverified; compatibility unevaluated.
- pgvector latest release unconfirmed (404, not retried).
- Only tracked Dockerfile/compose/workflow files at the two snapshots; other CI or image sources not scanned.
- Release differences are candidates, not proof that upgrades are safe or that implementations may be deleted. No compatibility wrapper, scanner framework, or per-package upgrade task is added.

## Artifacts

- `docs/reviews/2026-11-12-oss-actions-images-audit.json` — declaration-level detail
- `docs/deployment/evidence/OSS-ACTIONS-IMAGES-EVIDENCE-20261112/evidence.json` — compact evidence

Validation results are recorded in evidence.json (`validation`).
