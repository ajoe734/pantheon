# Idle V2 provider-health refresh evidence

This task repairs the V2 idle-fleet gap: until this change, the supervisor
only asked for a delivery-health probe when a pending task or reassignment had
already demanded one, or when a cached retry/refresh deadline had already
elapsed. A fully idle fleet could therefore retain expired endpoint
authentication or shared-account quota evidence indefinitely, and a lane
whose cached `retry_at` was still in the future stayed closed even after a
live probe would already succeed.

Human/Ops flagged exactly that gap on the prior head (19836b33d): live
`codex1` remained blocked behind a `quota_terminal` `retry_at` in the future
despite a successful direct probe. This round adds an authorized, bounded
bypass (`authorized_delivery_health_refresh_targets`) that may probe past a
cached future `retry_at`, but only when the exact delivery topology changed
(covers supervisor startup, since a fresh state has no prior fingerprint) or
an explicit Human/Ops request was recorded via
`supervisor.py --request-delivery-health-refresh`. The bypass is consumed
once per cycle so it never becomes an every-cycle probe storm, and it still
skips any lane that is already healthy.

The committed manifest, [evidence.json](evidence.json), is the review artifact.
It covers only the source change and deterministic regression proof. The
post-merge deployment authority must promote the exact merged command runtime,
run its bounded supervisor cycle (using the explicit Human/Ops trigger if the
live `codex1` retry_at is still in the future), and record the live
`delivery_health` readback through the normal runtime/evidence channels. That
operation must not edit canonical task state or provider credentials.

The live readback is accepted only when a configured Codex1 endpoint refreshed
the `codex1` account, `claude2` refreshed its separate account, and no
provider-only shared-account orphan was projected into runtime health.
