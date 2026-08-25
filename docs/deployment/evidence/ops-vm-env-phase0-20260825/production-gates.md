# Production Decision Gates & Governance Boundaries (Phase 0 Baseline)

- **Measurement Date:** 2026-08-25
- **Status:** Record of explicitly approved vs explicitly pending operational decisions
- **Reference Plan:** [`vm-dev-staging-prod-management-plan.md`](../../vm-dev-staging-prod-management-plan.md) §10, §11, §12, §16

---

## 1. Decision Truth & Governance Principle

In accordance with the Pantheon collaboration rules and VM management plan:
- **No production values, operational thresholds, or owner identities may be inferred by LLM agents.**
- Only decisions explicitly approved by Human/Ops are marked as approved.
- All unconfirmed operational parameters remain explicitly recorded as **PENDING OPERATOR APPROVAL**.
- No production or staging infrastructure may be provisioned during Phase 0 baseline measurement.

---

## 2. Production Gates Status Table

| Gate / Operational Parameter | Current Status | Baseline Target Recommendation | Required Approving Authority | Action / Blocking Gate |
|---|---|---|---|---|
| **Recovery Point Objective (RPO)** | ⏳ **PENDING OPERATOR APPROVAL** | $< 1\text{ hour}$ via automated pre-deploy and periodic disk snapshots. | Human/Ops (Platform Operator) | Production environment provisioning blocked until RPO threshold is formally approved. |
| **Recovery Time Objective (RTO)** | ⏳ **PENDING OPERATOR APPROVAL** | $< 30\text{ minutes}$ via automated GCE instance template rebuild and persistent data disk attach. | Human/Ops (Platform Operator) | Production runbooks and alert escalation thresholds must bind the approved RTO. |
| **Real-Capital Trading Scope** | ⏳ **PENDING OPERATOR APPROVAL / DEFERRED TO PHASE 4** | Disabled by default across all non-prod environments (`PANTHEON_LIVE_BROKER_ENABLED=false`). Phase 4 requires independent Prod Execution VM before real broker credentials can be attached. | Human/Ops & Trading Risk Owner | Live broker credentials strictly forbidden on Dev, Ephemeral Staging, and Prod Control VMs. |
| **On-Call Operator / Primary Incident Owner** | ⏳ **PENDING OPERATOR APPOINTMENT** | Designated platform engineer / Human/Ops operator with 24/7 paging routing. | Human/Ops | Production deployment lane cannot be opened without a designated human on-call rotation. |
| **Production GCP Project** | ⏳ **PENDING OPERATOR PROVISIONING** | Dedicated GCP project (e.g. `pantheon-prod-202608XX`); strictly separate from `pantheon-lupin-dev-20260719` and suspended `pantheon-benjamin-20260528`. | Human/Ops (GCP Organization Admin) | No production resources may be created inside the dev project. |
| **Production GitHub Environment** | ⏳ **PENDING REPO CONFIGURATION** | Protected GitHub Environment `production` with required reviewers enabled, self-review prevented, and deployment branch restrictions (`master` or signed promotion tag only). | Human/Ops (GitHub Admin) | Prevents unapproved automatic deployments from reaching production. |
| **Broker Secret Storage Boundary** | ✅ **APPROVED DESIGN CONTRACT** | Broker/TWS credentials reside exclusively in GCP Secret Manager scoped to the Prod Execution VM service account; never placed in GitHub secrets, repo files, or Control VM environment. | Pre-approved in L1 Security Policy | Fail-closed CI check rejects broker secrets in non-execution manifests. |
| **Blue/Green Release Observation Window** | ✅ **APPROVED DESIGN CONTRACT** | 30-minute automated post-cutover observation window monitoring 5xx error rates, P95 latency, health endpoints, and singleton worker ownership. | Pre-approved in VM Management Plan §10.4 | Automatic rollback triggered if critical error rate exceeds threshold during observation window. |

---

## 3. Read-Only Compliance Certification

This Phase 0 baseline assessment certifies that:
1. **Zero Infrastructure Provisioned:** No GCE instances, disks, snapshots, VPC networks, or IAM roles were created for Staging or Production.
2. **Zero Broker / Capital Mutation:** No broker connections were established; live broker flags remained `false`; no financial or trading state was altered.
3. **Zero Secret Read / Mutation:** No secrets or credentials were read from Secret Manager, GitHub secrets, or protected configuration.
4. **Non-Destructive Inspection:** All metrics and configurations were gathered via read-only system inspection (`/proc/`, `docker stats`, `df`, `uptime`, `systemctl status`, compose YAML parsing).
