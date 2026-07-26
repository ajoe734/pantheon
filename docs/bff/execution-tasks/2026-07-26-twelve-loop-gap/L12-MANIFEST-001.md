# L12-MANIFEST-001 — Activate the complete runtime manifest

Wave 3, lane `runtime-manifest`, owner `Antigravity`, reviewer `Claude`; depends on every loop implementation task listed in `tasks.json`.

Outcome: one owner integrates all required scheduled/async workers into the default Compose/deploy path with restart, health, durable volumes, auth, and safe egress/capital defaults.

Scope: `docker-compose.yml`, nonprod deploy script, and this task's evidence directory.

Acceptance: complete worker inventory, graceful supervised restart, deny-by-default source egress, isolated paper mode, live writes disabled, and no duplicate legacy worker.

Proof: Compose config, local-stack smoke, kill-one restart, service inventory, and safety readback. The full dependency and machine contract is canonical in `tasks.json`.
