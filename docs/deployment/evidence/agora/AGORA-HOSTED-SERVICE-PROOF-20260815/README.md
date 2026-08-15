# Agora hosted service proof

Task: `AGORA-HOSTED-SERVICE-PROOF-20260815`

This directory is the review manifest for an actual Pantheon-owned dev proof.
It is not itself a claim that the hosted proof has run. The only qualifying
runtime artifacts are `service-journey-evidence.json` and
`restart-evidence.json` from a successful exact-head run of
`.github/workflows/agora-hosted-acceptance.yml`.

The workflow fails closed unless all of these are true:

- the dispatch is the exact current `dev` BFF SHA and a matching HTTPS
  frontend deployment manifest;
- strict dev-login returns distinct operator identities, and every recorded
  stage traverses the public BFF over HTTPS;
- the proof creates only bounded, analysis/observe-only dev resources and
  verifies cross-tenant, cross-user, self-attestation, fixture, CAS, and
  authority negative controls;
- it acquires the shared dev environment lease, seeds PostgreSQL-backed
  proof resources, and uses `docker compose up --force-recreate` rather than
  an in-place container restart;
- the before/after container IDs differ, BFF readiness returns for the exact
  deployment SHA, and the durable workshop is read through authenticated HTTP
  after recreation.

The uploaded JSON deliberately contains request/response digests and hashed
subject, container, and resource identifiers. It never contains credentials,
raw request bodies, raw response bodies, or raw user identities.

To dispatch after a compatible `dev` deployment is live, select `dev` as the
workflow ref and supply the exact public manifest pair. A missing, stale, or
mismatched artifact is unqualified evidence, not a passing substitute.
