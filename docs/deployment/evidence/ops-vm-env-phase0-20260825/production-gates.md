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
| **Real-Capital Trading Scope** | ⏳ **PENDING OPERATOR APPROVAL / DEFERRED TO PHASE 4** | Distinguish environment live broker baselines: (1) **Active Dev VM:** disabled by default (`PANTHEON_LIVE_BROKER_ENABLED=false` in `docker-compose.yml` and `deploy_nonprod_vm.sh`, paper-only); (2) **Suspended Historical Staging-Live:** enabled by default in control stack (`PANTHEON_LIVE_BROKER_ENABLED=true` in `docker-compose.control.yml` and `deploy_nonprod_vm.sh`, credentials isolated to VM2 on suspended project `pantheon-benjamin-20260528`); (3) **Future Ephemeral Staging Target (Phase 2):** defaults to live-broker-off (`PANTHEON_LIVE_BROKER_ENABLED=false` / sandbox per VM Plan §5 & §9.3), with live broker enabled only on temporary dual-VM execution staging releases; (4) **Production (Phase 3/4):** real-capital live trading strictly gated to dedicated isolated Prod Execution VM (Phase 4) and blocked pending formal Human/Ops authorization. | Human/Ops & Trading Risk Owner | Live broker credentials strictly forbidden on Dev, Ephemeral Staging VM1, and Prod Control VMs. Real-capital execution blocked pending operator approval. |
| **On-Call Operator / Primary Incident Owner** | ⏳ **PENDING OPERATOR APPOINTMENT** | Designated platform engineer / Human/Ops operator with 24/7 paging routing. | Human/Ops | Production deployment lane cannot be opened without a designated human on-call rotation. |
| **Production GCP Project** | ⏳ **PENDING OPERATOR PROVISIONING** | Dedicated GCP project (e.g. `pantheon-prod-202608XX`); strictly separate from `pantheon-lupin-dev-20260719` and suspended `pantheon-benjamin-20260528`. | Human/Ops (GCP Organization Admin) | No production resources may be created inside the dev project. |
| **Production GitHub Environment** | ⏳ **PENDING REPO CONFIGURATION** | Protected GitHub Environment `production` with required reviewers enabled, self-review prevented, and deployment branch restrictions (`master` or signed promotion tag only). | Human/Ops (GitHub Admin) | Prevents unapproved automatic deployments from reaching production. |
| **Broker Secret Execution Boundary** | ✅ **APPROVED DESIGN CONTRACT** | Broker/TWS/exchange credentials reside strictly and exclusively within the execution boundary (isolated Phase 4 Prod Execution VM for production, and temporary Staging Execution VM [VM2] for staging sandbox testing per VM Plan §5, §9.2, §9.3, §12.1, and §16 Phase 4); strictly forbidden in Git repository, release manifests, GitHub secrets, Dev VM, Ephemeral Staging VM1 (control/non-execution), and Prod Control VM. Real-capital production credentials are strictly forbidden in all non-production environments including Ephemeral Staging. | Pre-approved in VM Management Plan §12.1 & §16 Phase 4 | Fail-closed check rejects broker secrets in non-execution manifests and non-execution environments/boundaries. |
| **Broker Secret Storage Mechanism** | ⏳ **PENDING OPERATOR APPROVAL** | Runtime secrets on Prod Execution VM to be retrieved via GCP Secret Manager or machine-local protected environment per VM Plan §12.1; exact storage mechanism selection pending operator decision. | Human/Ops & Trading Risk Owner | Execution VM runtime provisioning blocked until storage mechanism is formally approved. |
| **Blue/Green Release Observation Window** | ✅ **APPROVED DESIGN CONTRACT** | 30-minute automated post-cutover observation window monitoring 5xx error rates, P95 latency, health endpoints, and singleton worker ownership. | Pre-approved in VM Management Plan §10.4 | Automatic rollback triggered if critical error rate exceeds threshold during observation window. |

---

## 3. Read-Only Compliance Certification

This Phase 0 baseline assessment certifies that:
1. **Zero Infrastructure Provisioned:** No GCE instances, disks, snapshots, VPC networks, or IAM roles were created for Staging or Production.
2. **Zero Broker / Capital Mutation:** No broker connections were established or mutated; active Dev live broker flags remained `false`; historical staging remained unaccessed/suspended; no financial or trading state was altered.
3. **Zero Secret Read / Mutation:** No secrets or credentials were read or modified from GCP Secret Manager, machine-local protected env, GitHub secrets, or protected configuration.
4. **Non-Destructive Inspection:** All metrics and configurations were gathered via read-only system inspection (`systemd-analyze`, `/proc/`, `docker stats`, `docker inspect`, `df`, `uptime`, `systemctl status`, compose YAML parsing, and historical GitHub Actions workflow run logs). Zero container image builds, cache mutations, or state modifications were performed.
