# BFF Screen Contracts

This directory holds screen-shaped Pantheon BFF notes and examples for cross-repo delivery.

Current canonical BFF policy still lives in:

- [BFF_API_CONTRACT.md](/home/ajoe734/code/pantheon/services/control-plane/bff/BFF_API_CONTRACT.md)
- [DEGRADED_OPERATOR_PATH.md](/home/ajoe734/code/pantheon/services/control-plane/bff/DEGRADED_OPERATOR_PATH.md)

## Staging/Production Read Cutoff

Staging and production BFF deployments must run with
`PANTHEON_BFF_ALLOW_LOCAL_SNAPSHOT_FALLBACK=false`. In that mode, `read_surfaces.json`
is a dev/test bootstrap artifact only; unavailable downstream services must surface as
degraded or unavailable, not as seeded local data.

The dedicated control-plane compose stack wires BFF reads through service URLs for
deployment, governance approval, capital, runtime-manager, incidents, postmortems,
evolution, lineage, and memory. It must not mount governance/runtime/incident data
volumes into `operator-bff` as a normal read path.
