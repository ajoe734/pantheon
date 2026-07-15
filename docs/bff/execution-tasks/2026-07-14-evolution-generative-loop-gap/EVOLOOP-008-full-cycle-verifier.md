# EVOLOOP-008 — Full-Cycle Live Verifier

Status: implemented & verified

Owner: Antigravity  
Reviewer: Claude  
Target branch: `dev`  

## Outcome

`EVOLOOP-008` implements the full-cycle E2E business-flow verifier for the generative OODA loop. It verifies that a breach triggers an incident, which creates a proposal, gets approved, executes a retrain producing a v2 artifact, redeploys the v2 artifact under governance, and logs the complete cycle in the evolution journal with linked IDs at every stage.

The E2E verifier is added to `verify_e2e_evolution_loop.py` and is fully integrated into the mainline test suite script `scripts/run_e2e_verifiers.sh`.

---

## E2E Full-Cycle Verification Flow

The E2E full-cycle live verification logic runs in 11 sequential steps in `scripts/verify_e2e_evolution_loop.py`:

1. **Active Runtime Inspection**: Fetches active runtime bindings from the BFF (`/bff/runtimes`), selecting one binding to initiate the breach.
2. **Breach Telemetry Ingestion**: Ingests a threshold breach telemetry event (`drawdown_snapshot`) targeting the selected binding/runtime.
3. **Incident Creation**: Injects the threshold breach payload via the incidents service's `consume-threshold` API, producing an incident.
4. **Incident Resolution**: Marks the incident as resolved, triggering the outbox worker to automatically create a postmortem draft.
5. **Postmortem Publication**: Publishes the postmortem draft, which triggers the outbox worker to automatically create an evolution proposal of type `retrain`.
6. **Proposal Review**: Submits a review transition for the evolution proposal.
7. **Proposal Approval**: Approves the evolution proposal, triggering the background dispatch worker to execute the parameter mutation retrain.
8. **Artifact Mutation & Approval**: Verifies that the research plane successfully MUTATES the strategy's parameters (e.g., `lookback_bars`), registers the mutated artifact (v2) in the registry, and advances its state to `approved`.
9. **Redeploy Follow-Through**: Calls `/redeploy-followthrough` to generate the redeploy command and registers/approves the decision in the governance service.
10. **Redeploy Plan & Dispatch**: Retires the old active binding, creates a new deployment plan for the mutated v2 artifact, and dispatches it.
11. **Verification**: Verifies that the new binding is active in BFF runtimes and that the evolution journal (`/bff/management/evolution-journal`) logs the complete cycle (breach -> incident -> proposal -> execution).

---

## Local Verification Commands

To run all BFF-based E2E verifiers including the full-cycle verifier:

```bash
BFF_BASE=https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io BFF_TOKEN=op-dev:admin:mfa scripts/run_e2e_verifiers.sh
```

To run only the evolution loop verifier script:

```bash
BFF_BASE=https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io python3 scripts/verify_e2e_evolution_loop.py
```

---

## Task Closeout Commit

- **LLM-Agent**: `Antigravity`
- **Task-ID**: `EVOLOOP-008`
- **Reviewer**: `Claude`
- **Verified**: Full-cycle E2E verifier run successfully against local services stack
- **Artifacts**:
  - [scripts/run_e2e_verifiers.sh](file:///tmp/pantheon-worker-worktrees/pantheon/evoloop-008/scripts/run_e2e_verifiers.sh)
  - [scripts/verify_e2e_evolution_loop.py](file:///tmp/pantheon-worker-worktrees/pantheon/evoloop-008/scripts/verify_e2e_evolution_loop.py)
  - [docs/bff/execution-tasks/2026-07-14-evolution-generative-loop-gap/EVOLOOP-008-full-cycle-verifier.md](file:///tmp/pantheon-worker-worktrees/pantheon/evoloop-008/docs/bff/execution-tasks/2026-07-14-evolution-generative-loop-gap/EVOLOOP-008-full-cycle-verifier.md)
