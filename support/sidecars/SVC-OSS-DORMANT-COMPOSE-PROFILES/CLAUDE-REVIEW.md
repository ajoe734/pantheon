# Claude Review: SVC-OSS-DORMANT-COMPOSE-PROFILES

Reviewer: Claude
Owner: Codex2
Date: 2026-04-29
Outcome: APPROVED

---

## Acceptance Criteria Verdict

| Criterion | Result | Evidence |
|---|---|---|
| Dormant profiles not in default compose startup | PASS | All 6 services carry `profiles: ["dormant-smoke"]`; verified via `docker compose config --services` (PARENT-VERIFICATION.md) |
| Profile commands require explicit deferred-prep flags or env vars | PASS | FinRL/RLlib/Ray Tune: `--enable-deferred-prep --backend stub`; Qlib/TRL: `--backend stub`; experiments: `--backend memory` |
| No ports, secrets, or live broker routes exposed | PASS | No `ports:`, no `secrets:`, no depends_on to runtime stack, no broker env vars on any dormant smoke service |
| Compose config and smoke commands documented and testable | PASS | PARENT-VERIFICATION.md records all commands and observed results |

---

## Dockerfile Changes

FinRL, RLlib, and Qlib Dockerfiles now use repo-root build context, fixing the
build-context mismatch flagged by the sidecar (SVC-OSS-DORMANT-COMPOSE-PROFILES-SIDECAR-ACCEPTANCE).

- `COPY services/research/<svc>/requirements.txt .` — correct root path
- `ARG PANTHEON_INSTALL_UPSTREAM_DEPS=true` with conditional `pip install` — allows
  smoke images to skip heavy upstream deps at build time (`false` in compose)
- Inert default `CMD` in each Dockerfile confirmed

TRL and experiments Dockerfiles were already correct:
- TRL: stub backend is stdlib-only; no pip install in Dockerfile; command path
  `services/learning/trl/smoke_test.py` matches WORKDIR layout
- Experiments: memory and offline W&B backends are stdlib-only; no pip install

---

## Gate Safety

- Activation gates remain closed for all six scaffolds
- FinRL/RLlib/Ray Tune: smoke verified to emit `artifact_state=draft`,
  `deployment_stage=none`, `gate_state=closed`
- Qlib/TRL: smoke uses stub/offline path; no preflight bypass
- Experiments: memory-backend smoke; no SDK-backed W&B activation
- `PANTHEON_DEFERRED_PREP_GATE=1` is documentary/contextual only; actual gates
  are the CLI `--enable-deferred-prep` flag and stub backend selection

---

## Notes

- `restart: "no"` on all dormant smoke services is correct
- No `depends_on:` to any running service — these are standalone build-and-run jobs
- Header comment in docker-compose.yml with explicit run commands is clear and complete

---

## Decision

Approved. All four acceptance criteria pass. Sidecar-identified build-context
risk resolved. Verification is reproducible. Return to Codex2 for closeout.
