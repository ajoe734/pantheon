# V2 — Dependency vulnerability audit & remediation (direction D-security)

- Date: 2026-06-14
- Branch: task/verify-v2-dep-vulns
- Non-duplication check: recent PRs/task-briefs show other agents on MGMT-LIVE-*,
  BFF OIDC/JWKS auth-facade hardening, datastrat_* (research/data), fe_int_gate
  (frontend). A grep of task-briefs for dependabot/CVE/vulnerability found NOTHING,
  so dependency-vulnerability remediation is an unclaimed gap. This round is distinct.

## Plan
Triage the 21 open Dependabot alerts (8 critical / 4 high / 8 medium / 1 low,
flagged by the chair review and never triaged) and remediate the safely-fixable ones.

## Findings (all pip / Python; all in research/ML services)
| pkg | alerts | vulnerable range | patched | action |
|-----|--------|------------------|---------|--------|
| mlflow | 1 crit + 4 high + 4 med | <=3.10.1 / <3.11.0 | 3.11.0 / 3.11.1 | **bump 3.10.1 -> 3.11.1** |
| ray | 4 crit + 2 med | <2.52.0 / <2.54.0 (also <=2.52.0 no-patch) | 2.52.0 / 2.54.0 | TRACK: pinned `ray[rllib/tune]==2.9.3`; 2.9->2.54 is a breaking major upgrade for the rllib service -> needs a real upgrade+test task, not a blind bump |
| torch | 1 low | <=2.12.0 | none | ACCEPT: no upstream patch; pinned 2.12.0+cpu (finrl) / >=2.1.0,<3 (trl) |

Key risk context: **every vulnerable package lives only in research/ML services
(mlflow, rllib/ray-tune, finrl, trl) that run as DORMANT smoke containers (all Exited
in `docker ps`), NOT in the live execution / control-plane / BFF / broker path.** So
the live attack surface is limited; these are build-time/dormant-service risks.

## Fix (this round)
- `services/research/mlflow/requirements.txt`: `mlflow==3.10.1 -> 3.11.1`. Resolves the
  mlflow high (<3.11.0) and medium (<=3.10.1) alerts and moves out of the critical
  `<=3.10.1` range. Minor bump; siblings (pydantic>=2.6.3, redis>=5) are unconstrained
  against it. The mlflow smoke image should be rebuilt to pick it up (dormant; low risk).

## Tracked follow-ups (not blind-fixed)
- ray `ray[rllib]==2.9.3 / ray[tune]==2.9.3` -> 2.54.0: a major upgrade with breaking API
  changes; requires upgrading + re-validating the rllib/ray-tune research path. Open as a
  dedicated task. (Two ray criticals are `<=2.52.0 -> none`: no patch exists yet.)
- torch `<=2.12.0 -> none` (low): no upstream patch; monitor.
- Several alerts are `first_patched: none` (mlflow critical <=3.10.1, ray <=2.52.0,
  torch) -> cannot be fixed by bumping; mitigated by the dormant-service context above.
