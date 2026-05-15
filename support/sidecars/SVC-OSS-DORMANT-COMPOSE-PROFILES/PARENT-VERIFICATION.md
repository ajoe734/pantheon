# SVC-OSS-DORMANT-COMPOSE-PROFILES Parent Verification

Task owner: Codex2
Reviewer: Claude
Date: 2026-04-29

This task adds the non-default `dormant-smoke` Docker Compose profile for
activation-gated OSS scaffold smoke packaging. The profile is explicit opt-in
only and is not part of default compose startup.

## Delivered Compose Targets

- `finrl-dormant-smoke`
- `rllib-dormant-smoke`
- `ray-tune-dormant-smoke`
- `qlib-dormant-smoke`
- `trl-dormant-smoke`
- `experiments-dormant-smoke`

All six services use `profiles: ["dormant-smoke"]`, `restart: "no"`, no
published ports, no secrets, and no live broker routes. FinRL, RLlib, and Ray
Tune commands require the reviewed `--enable-deferred-prep` CLI gate. Qlib and
TRL run their reviewed stub/offline smoke paths. Experiments uses the memory
backend as packaging-only smoke and does not activate SDK-backed W&B.

The dormant profile sets `PANTHEON_INSTALL_UPSTREAM_DEPS=false` for FinRL,
RLlib/Ray Tune, and Qlib images so explicit smoke packaging stays local/offline
and does not fetch heavy upstream dependencies during smoke image builds. The
Dockerfile default remains `true` for non-smoke builds.

## Verification Commands

```bash
docker compose config >/tmp/pantheon-compose-config.txt
docker compose --profile dormant-smoke config >/tmp/pantheon-compose-dormant-config.txt
docker compose config --services
docker compose --profile dormant-smoke config --services
docker compose config --services | rg 'dormant-smoke|finrl-dormant|rllib-dormant|ray-tune-dormant|qlib-dormant|trl-dormant|experiments-dormant' && exit 1 || true
docker compose --profile dormant-smoke build finrl-dormant-smoke rllib-dormant-smoke ray-tune-dormant-smoke qlib-dormant-smoke trl-dormant-smoke experiments-dormant-smoke
docker compose --profile dormant-smoke run --rm finrl-dormant-smoke
docker compose --profile dormant-smoke run --rm rllib-dormant-smoke
docker compose --profile dormant-smoke run --rm ray-tune-dormant-smoke
docker compose --profile dormant-smoke run --rm qlib-dormant-smoke
docker compose --profile dormant-smoke run --rm trl-dormant-smoke
docker compose --profile dormant-smoke run --rm experiments-dormant-smoke
```

## Observed Results

- Default `docker compose config --services` excludes all dormant smoke jobs.
- Explicit `docker compose --profile dormant-smoke config --services` includes
  all six dormant smoke jobs.
- The six dormant smoke images built successfully.
- FinRL smoke completed with `artifact_state=draft`,
  `deployment_stage=none`, and `gate_state=closed`.
- RLlib smoke completed with `artifact_state=draft`,
  `deployment_stage=none`, and `gate_state=closed`.
- Ray Tune smoke completed with `artifact_state=draft`,
  `deployment_stage=none`, and `gate_state=closed`.
- Qlib smoke completed with `artifact_state=draft` and
  `deployment_stage=none`.
- TRL smoke completed with `artifact_state=draft` and
  `deployment_stage=none`.
- Experiments memory smoke passed with registry metadata mapped into experiment
  metadata.

## Closeout Verification

Codex2 re-ran the focused closeout verification on 2026-04-29 before finalizing
the reviewed task:

```bash
docker compose config >/tmp/pantheon-compose-config.txt
docker compose --profile dormant-smoke config >/tmp/pantheon-compose-dormant-config.txt
docker compose config --services
docker compose --profile dormant-smoke config --services
docker compose --profile dormant-smoke build finrl-dormant-smoke rllib-dormant-smoke ray-tune-dormant-smoke qlib-dormant-smoke trl-dormant-smoke experiments-dormant-smoke
docker compose --profile dormant-smoke run --rm finrl-dormant-smoke
docker compose --profile dormant-smoke run --rm rllib-dormant-smoke
docker compose --profile dormant-smoke run --rm ray-tune-dormant-smoke
docker compose --profile dormant-smoke run --rm qlib-dormant-smoke
docker compose --profile dormant-smoke run --rm trl-dormant-smoke
docker compose --profile dormant-smoke run --rm experiments-dormant-smoke
```

Closeout results matched the approved evidence: default compose excludes the
dormant jobs, the explicit `dormant-smoke` profile includes all six jobs, all
six images build, and all six offline smoke runs pass without ports, secrets,
live broker routes, or registry/live activation.
