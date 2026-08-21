# Idle V2 provider-health refresh evidence

This task repairs the V2 idle-fleet gap: until this change, the supervisor
only asked for a delivery-health probe when a pending task or reassignment had
already demanded one. A fully idle fleet could therefore retain expired
endpoint authentication or shared-account quota evidence indefinitely.

The committed manifest, [evidence.json](evidence.json), is the review artifact.
It covers only the source change and deterministic regression proof. The
post-merge deployment authority must promote the exact merged command runtime,
run its bounded supervisor cycle, and record the live `delivery_health`
readback through the normal runtime/evidence channels. That operation must not
edit canonical task state or provider credentials.

The live readback is accepted only when a configured Codex1 endpoint refreshed
the `codex1` account, `claude2` refreshed its separate account, and no
provider-only shared-account orphan was projected into runtime health.
