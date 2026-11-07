# Persona Paper advisory admission recovery

Existing Management Paper Personas provisioned before this change have no
capability snapshot and no explicit `environment_ceiling`, so Agora Talk
rejects them with `capability_snapshot_unavailable, environment_ceiling_exceeded`.

## Operation

`POST /bff/personas/{persona_id}/provisioning/advisory-admission`
(operator role; tenant derived from the caller identity; no body).

It writes through the Persona owner only:
1. an immutable snapshot `cap-advisory-{persona_id}` with exactly `["persona_opinion"]`;
2. metadata `capability_snapshot_id` and `environment_ceiling="paper"`.

Idempotent: repeats return the same snapshot and perform no extra write.
No order, capital, broker or runtime authority is granted.

## Refusals
- 404: Persona absent or in another tenant.
- 409 `persona_not_active`: retired / suspended / archived / frozen / draft.
- 409 `persona_not_paper_running`: not a reconciled Paper-running Persona.
- 409 `environment_ceiling_exceeds_paper_advisory`: an existing higher ceiling is never rewritten.
- 409 `capability_owner_rejected: ...`: owner conflict/unavailable.

## Hosted use (existing publisher, after release)
Call once per Persona (青禾 R2 persona-be19a1fac471247f2b2a, 澄川
persona-d9363cffc0efbbbcbeb9, 衡岳 persona-9d4fb3f44cbc5e7cb84f) as
operator_a / tenant-dev, then read back via the Agora eligibility endpoint and
confirm `eligible: true` with `participant_snapshot`. Source delivery only;
hosted completion and provider-response proof are separate.
