# Persona research demand write authorization

All three current mutations (`POST /api/research/persona-demands`, `/reconcile`,
and `/{demand_id}/actions`) now require the authenticated actor
`persona-pool-cycle`. They reuse the existing Research token verification,
read-role validation and explicit tenant boundary before checking that actor.
`GET /api/research/persona-demands` retains the existing read-role dependency.

The action receipt route is included because it is present in the merged source
used by this task. The caller already sends `PERSONA_RESEARCH_SERVICE_TOKEN`,
and the existing dev issuer assigns that credential the `persona-pool-cycle`
subject and `viewer` role. This change adds no role, credential, grant or route.
Demand storage, query-plan policy, persona decisions and paper/live authority
remain with their existing owners.

The route tests use real FastAPI dependencies, signed local fixture JWTs,
the existing principal issuer with fixture verifier material, and temporary
Research demand/query-plan stores. They verify persisted positive results and
unchanged storage on rejected writes. They cover other viewers, operators,
admins, cross-tenant requests, identity spoofing, invalid/expired credentials,
missing tenant headers and the existing permissive structured-token boundary.
The query-plan integration tests now authenticate demand writes as the cycle
principal rather than their former generic operator fixture.

See `evidence.json` for commands and terminal results. This is local source
evidence; it makes no deployment, hosted activation or exploitation claim.
Independent review and merge are recorded by the canonical task workflow.
