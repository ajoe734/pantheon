# OPS-SECURITY-DEPENDENCY-001 — Reviewer Approval Record

- Task: OPS-SECURITY-DEPENDENCY-001 — Reconcile and remediate current
  dependency alerts
- Owner: Codex2 (reassigned from Codex)
- Reviewer: Claude
- Review date: 2026-07-22
- Branch: `task/OPS-SECURITY-DEPENDENCY-001`
- Reviewed HEAD: `6e96c402a` (evidence branch, post `origin/dev` merge)
- Delivered evidence merge on `dev`:
  `17637741a579ea9873f13066f4636301048df64a` (PR #3975)
- Decision: **APPROVED** — return to owner Codex2 for closeout

## Delivery facts independently verified

All four claimed commits confirmed as ancestors of current `origin/dev`
(`35d7e5724`):

- `983c2a84b2f4947f848ffbbd0f7f230d6c8d5875` — remediation PR #3968
- `1f51fc82f918412bd5654a2872bb48df716a4f82` — least-privilege alert-read
  workflow fix PR #3969
- `17637741a579ea9873f13066f4636301048df64a` — evidence/revalidation PR #3975
- `834318190c90962e024ee9b82243cf31a742f441` — removal of
  `execute-plans/package-lock.json` (path confirmed absent from `origin/dev`)

Manifest pins confirmed in the checkout: `mlflow==3.11.1`,
`ray[rllib]==2.54.0`, `ray[tune]==2.54.0`, `gymnasium==1.2.2` (RLlib) /
`1.2.3` (FinRL), `torch==2.13.0+cpu`.
`.github/workflows/dependency-alert-reachability.yml` present on `origin/dev`;
delivered-`dev` run `29946794630` (`Dependency Alert Reachability`) reported
`completed/success`.

## Independent reviewer verification (2026-07-22 UTC)

Commands re-run by the reviewer in this task worktree, not copied from owner
evidence:

- `python3 -m unittest scripts.security.test_dependabot_reachability` — 4/4 OK
- `services/research/mlflow: python3 -m unittest test_security_boundary` — 7/7 OK
- `services/research/rllib: python3 -m unittest test_security_boundary` — 6/6 OK
- `services/research/rllib: python3 -m unittest test_adapter
  test_ray_tune_adapter` — 35/35 OK
- `services/registry/experiments: python3 -m unittest test_adapter` — 16/16 OK
- Total: 68/68 focused tests, matching the owner's claimed suite composition.
- `docker compose config --quiet` — pass; `git diff --check` — pass.
- Live GitHub API query (`state=open`): 14 alerts — 6 critical, 2 high,
  5 medium, 1 low — exactly matching the Codex2 revalidation record.
- Live reconciliation:
  `python3 scripts/security/dependabot_reachability.py --alerts-json
  <live alerts> --fail-on critical --fail-on high` — exit 0, eight
  critical/high alerts `candidate_fixed`, six `below_threshold_fixed`,
  zero violations: "No reachable critical/high alert remains vulnerable in
  this checkout."

## Acceptance criteria verdict

1. **No open critical/high alert in accepted reachable graph** — verified by
   live reconciliation above; the six dismissed npm alerts bind only to the
   removed historical frontend mirror, with removal commit and path evidence.
2. **MLflow / Ray not exposed unauthenticated on non-loopback** — MLflow
   boundary suite covers Host-header/CORS 403, job-submission refusal, and
   non-loopback-without-basic-auth refusal (exit 78); Ray boundary suite covers
   token-mode requirement (≥32 chars), remote-address refusal, and non-loopback
   dashboard refusal. Both suites re-run green by the reviewer.
3. **Dormant profiles fail-closed** — dormant Compose services have
   `network_mode: none`, no ports, read-only root, capabilities dropped; owner
   and Codex2 both recorded live dormant probes (200 health / 403 disallowed /
   write refused / `deployment_stage=none`, `gate_state=closed`).
4. **Historical alerts reconciled by path/commit evidence** — dismissals
   limited to #22–#26, #28; no frontend source or lockfile re-added to
   Pantheon.
5. **Installs/builds/tests/compose/secret-scan pass; PR merged to `dev`** —
   CI run green on delivered `dev`; image build, pip-check, PPO smoke, and
   secret-scan evidence recorded by owner and independently revalidated by
   Codex2 after reassignment; reviewer re-ran the non-container checks above.

## Residuals accepted by reviewer

Accepted as bounded, documented risks with clear future ownership:

1. `finrl==0.3.7` imports the undeclared broker SDK `alpaca_trade_api` at
   top level. No broker credentials or activation exist in this delivery; the
   dormant stub stays closed. Any future FinRL activation task must resolve
   this optional integration first.
2. RLlib 2.54 runs the synchronous action adapter via Ray's legacy API-stack
   compatibility mode. Functional and tested now; a future migration task
   should adopt the RLModule/EnvRunner API before upstream removes the
   compatibility path.
3. GitHub repository-level secret scanning is disabled (API 404). The branch
   compensates with a local changed-content secret scan and records the
   platform limitation instead of claiming server-side coverage.
4. GitHub's default-branch alert view lags `dev` promotion; the reachability
   CI evaluates advisory ranges against the checked-out candidate manifests,
   which is the correct source of truth for the accepted graph.

## Decision

APPROVED. The delivered graph satisfies every acceptance criterion; the
exclusions (no production activation, no blanket dismissal, no frontend
source in Pantheon) are respected. Task returns to owner Codex2 for closeout
finalization per `.orchestrator/skills/task-closeout-finalization.md`.
