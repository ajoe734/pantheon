# MGMT-QLIB-006 Management Artifact / Research Linkage Evidence

Task scope: expose the Qlib admission candidate as a Management-readable
research artifact linkage without writing registry truth or opening execution
paths.

## Artifacts

- `management_linkage_packet.json` - review packet for the Management artifact
  read model linkage.
- BFF contract coverage:
  `services/control-plane/bff/test_mgmt_qlib_006_artifact_research_linkage.py`.

## Boundary

This packet links existing Qlib admission evidence into Management read
surfaces:

- dataset manifest from `MGMT-QLIB-001`
- StrategySpec packet from `MGMT-QLIB-002`
- model/evaluation artifact refs reviewed in `MGMT-QLIB-004`
- pending registry admission evidence from `MGMT-QLIB-005`

It does not perform registry writes, deployment, broker session creation, order
routing, or live capital side effects.

## Verification

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/test_mgmt_qlib_006_artifact_research_linkage.py -q
PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile services/control-plane/bff/read_store.py services/control-plane/bff/test_mgmt_qlib_006_artifact_research_linkage.py
```
