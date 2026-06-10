# EP5 Canary Closeout Evidence

Status: renderer scaffold only; no canary proof is claimed by this directory
until a real observation report is rendered here.

This directory is the reviewer-facing landing point for the EP5 closeout
packet produced by `services/governance/ep5_proof/closeout_renderer.py`.

## Expected Inputs

- `observation-report.json`: output from
  `services.governance.ep5_proof.observation_report`
- embedded `proof_packet`: A2.2 EP5 proof packet
- embedded `promotion_readiness_packet`: structured readiness packet consumed
  by the EP5-002 validator

## Expected Outputs

- `closeout-packet.json`: deterministic machine-readable closeout packet
- `README.md`: reviewer-friendly Markdown rendering of the same packet

## Render Command

```bash
PYTHONPATH=. python3 -m services.governance.ep5_proof.closeout_renderer \
  --input docs/deployment/evidence/ep5-canary/observation-report.json \
  --json-output docs/deployment/evidence/ep5-canary/closeout-packet.json \
  --markdown-output docs/deployment/evidence/ep5-canary/README.md
```

## Review Boundary

The renderer preserves observation blockers, proof-packet blockers, derived
promotion-readiness blockers, and fail-closed live/capital side-effect flags.
A passing closeout packet means the archived evidence is internally consistent;
it does not authorize broker-production live routing or live capital binding.
