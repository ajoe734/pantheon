# V8 — Container resilience / restart policy (direction D/E, deepening)

- Date: 2026-06-14
- Branch: task/verify-v8-container-hardening
- Non-duplication: no brief covers container hardening / restart-policy / healthcheck audit.

## Verification (live VM `docker inspect`)
Across the 51 running pantheon containers:
- **restart policy = "no" on ALL 51** -> nothing auto-recovers on crash or Docker/VM
  restart. Compose declared `restart:` on only 10 services — and those 10 are the
  one-shot smoke/init jobs (correctly `restart: "no"`), while the 36 LONG-RUNNING
  services declared none. This is the resilience gap behind the durability theme
  (cf V1 reconciler finding, the 2026-06-12 outage).
- no healthcheck: 17 services.
- running as root (empty `User`): 50/51.

## Fix (this round)
Added `restart: unless-stopped` to all 36 long-running compose services (broker,
operator-bff, runtime-manager, telemetry, governance, optimizer-svc, ...; skipped the
one-shot smoke/init services which correctly keep `restart: "no"`). YAML validated;
all 36 confirmed. Takes effect on the next `docker compose up` (recreate) — the durable
declaration; running containers keep `no` until redeployed (not force-recreated here to
avoid disrupting the live fleet).

## Tracked follow-ups
- Add missing healthchecks to the 17 services without one (observability + restart
  correctness).
- Run services as non-root (50/51 are root) — a hardening pass per Dockerfile (USER).
- On next deploy, verify the running containers pick up `unless-stopped`.
