# LLM Cowork Playbook (Claude + Gemini + Codex)

## 1. Goal and Definition of Done
Build a production-ready v1 delivery path for local development + Docker runtime + GCP deployment + GitHub CI/CD, with clear ownership and auditable progress history.

Done means:
- Local stack boots with `docker compose up`.
- LEAN container image builds deterministically.
- GitHub Action builds and pushes image to GCP Artifact Registry.
- Deployment workflow to GCP has a verified staging path.
- Team progress and decisions are traceable in git history.

## 2. Working Agreement (All Agents)
- Work in short PR-sized changes.
- Never force-push shared branch.
- Every status update must append one line in `## 8. Progress Log` with UTC timestamp.
- Every non-trivial decision must append one entry in `## 9. Decision Log`.
- If blocked > 30 minutes, log blocker and handoff request in `## 8. Progress Log`.

## 3. Branch and PR Strategy
- Main protected branch: `main`
- Shared integration branch: `integration/cowork-v1`
- Agent branches:
  - `feat/claude-runtime-stack`
  - `feat/gemini-gcp-cicd`
  - `feat/codex-orchestration-qa`

PR flow:
1. Agent branch -> `integration/cowork-v1`
2. Resolve conflicts on integration branch
3. Final stabilization PR: `integration/cowork-v1` -> `main`

## 4. Equal Workload Split (approx. 33/33/34)
### Claude (Runtime + Container Reliability) ~33%
Owner files:
- `Dockerfile`
- `docker-compose.yml`
- `Launcher/config.json` (only runtime-related deltas)
- Optional: `scripts/local-smoke.sh`

Responsibilities:
1. Make LEAN Docker build/run deterministic.
2. Fix compose service wiring (healthcheck, depends_on, ports, env, volumes).
3. Add local smoke-run command and troubleshooting notes.

Handoffs:
- Output image/tag conventions for Gemini CI pipeline.
- Output run contract (required env vars, mounted paths) for Codex validation.

### Gemini (GCP + CI/CD) ~33%
Owner files:
- `.github/workflows/gcp-deploy.yml`
- Optional: `.github/workflows/ci.yml`
- `infra/` (Terraform or deployment manifests if present/new)

Responsibilities:
1. Harden GitHub -> GCP auth flow (prefer OIDC, fallback key-based only if needed).
2. Build and push image to Artifact Registry.
3. Add staging deployment step (Cloud Run or GKE path, choose one and document).
4. Add deploy-time checks and rollback note.

Handoffs:
- Publish exact secret/variable matrix for Codex docs + QA.
- Publish deploy command contract for Claude local parity checks.

### Codex (Integration + QA + Governance) ~34%
Owner files:
- `LLM_COWORK_PLAYBOOK.md` (this file)
- `README` deployment/runbook sections
- Optional: `docs/release-checklist.md`

Responsibilities:
1. Define cross-agent contracts and acceptance criteria.
2. Validate end-to-end flow (local -> build -> push -> staging deploy).
3. Maintain progress log, decision log, and release checklist.
4. Run final integration pass and prepare merge summary.

Handoffs:
- Open blocker tickets when contracts mismatch.
- Confirm final DoD evidence links before merge.

## 5. Sequencing and Dependency Plan
Phase A (parallel start):
1. Claude: fix runtime/container contract.
2. Gemini: fix CI auth/build/push skeleton.
3. Codex: prepare acceptance checklist + test matrix.

Phase B (integration):
1. Claude publishes image/run contract.
2. Gemini consumes contract and completes deploy job.
3. Codex validates with checklist and reports gaps.

Phase C (stabilization):
1. Claude + Gemini patch gaps.
2. Codex runs final verification and writes release note.

## 6. Communication Protocol
Use this exact update format in `## 8. Progress Log`:

`[UTC timestamp] [Agent] [Status] [Scope] [Next] [Blocker(optional)] [PR/Commit(optional)]`

Status values:
- `STARTED`
- `IN_PROGRESS`
- `BLOCKED`
- `READY_FOR_REVIEW`
- `DONE`

Daily sync cadence:
1. Start-of-day: one `STARTED` line per agent.
2. Mid-day: at least one `IN_PROGRESS` line.
3. End-of-day: `DONE` or `BLOCKED` with explicit next action.

## 7. Acceptance Checklist (must all pass)
- [ ] `docker compose config` passes and services are coherent.
- [ ] Docker image builds without manual patching.
- [ ] Workflow authenticates to GCP and pushes image.
- [ ] Staging deployment command executes successfully.
- [ ] Required secrets/vars are documented and minimal.
- [ ] Runbook includes local run, deploy, rollback.
- [ ] Progress log and decision log are up to date.

## 8. Progress Log
Append only. Do not rewrite previous lines.

- [2026-04-01T00:00:00Z] [Codex] [STARTED] [Created cowork playbook and collaboration protocol] [Next: wait for agent updates and track integration] [PR/Commit: pending]

## 9. Decision Log
Append only. One decision per item.

- [2026-04-01] Decision: Use `integration/cowork-v1` as shared branch before `main` merge.
  Rationale: Reduce merge churn and allow staged stabilization.
  Impact: All agents target integration branch first.

- [2026-04-01] Decision: Keep communication and history in this file under sections 8 and 9.
  Rationale: Single source of truth readable by all LLMs and humans.
  Impact: No separate tracker tool is required to start collaboration.

## 10. Immediate Next Actions
1. Claude starts runtime fixes in `feat/claude-runtime-stack`.
2. Gemini starts CI/CD hardening in `feat/gemini-gcp-cicd`.
3. Codex prepares acceptance verification script/checklist and updates this log every integration event.
