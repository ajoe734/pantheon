# OPS-ACTIVITY-V2-SUPERSEDED-RECOVERY-001 evidence

Incident date: 2026-07-18 UTC. This record contains hashes and counts only; it
does not include activity payloads or process environments.

## Root cause

The schema-v2 transaction
`activity-rotation-67ce2f6008cf241c92651c22681102f7e7fc472703698e1411cc87217de2ef99`
was fully staged, but an auto worker invoked `scripts/ai_status.py` from the
stale shared checkout instead of the installed `PANTHEON_COMMAND_ROOT`. That
legacy writer created
`archive/logs/ai-activity-log.jsonl-2026-07-18T0414Z.gz`, retained its 1,000
line suffix, and left the schema-v2 intent pending. Normal recovery then saw an
active digest matching neither the original source nor the intended target and
failed closed on every supervisor loop.

Quota was not the initiating failure. The supervisor failed before dispatch,
so worker occupancy fell to zero while the pending intent remained.

## Read-only proof

- Inventory SHA-256:
  `530779b6962a9934fd8e6bd58d7fb2e762968d9fbe921d5a79dd70019af06c65`
- Schema-v2 source: 5,279,769 bytes, 2,325 lines, SHA-256
  `71aac5a7579b4fce1bb5c7bdc8f7b6d1fdf33a4e77f066c9727371e3180239d1`
- Staged archive payload: 2,973,026 bytes, 1,324 lines, SHA-256
  `e4a824ab6a9990c12fe1192deca36ce1525eab056eef66885707e1b12e91e28b`
- Staged tail: 2,306,039 bytes, 1,000 lines, SHA-256
  `c5d86dd43690bc28555b409d0746ccd6a1ab04a60322ae0af100acf60fe92f30`
- Superseding legacy payload: 5,505,752 bytes, 2,400 lines, SHA-256
  `fcefdeb4b430ba445e790735419471136e967d8c5e54549acd74192de97015cb`
- The superseding payload contained 75 post-intent rows; the live active file
  contained the exact retained 1,000-line overlap plus 27 post-rotation rows.
- Affected logical set: 2,427 rows, 2,427 distinct event IDs, zero missing and
  zero duplicate IDs.

## Guarded recovery

The execute used the exclusive runtime-admission lock with the supervisor
stopped. A stable inventory pin was required before mutation. Fifty-nine rows
arrived after the first pin; the retry preserved them as an immutable evidence
artifact and included all 251,587 bytes in the reconstructed active target.

- Status: `resolved`
- Resolution sequence: `2`
- Resolution ID:
  `activity-intent-resolution-d3ad0f00c7e4b7199c4b20169f79aa8fd067f91e896703f8c5b752d630d2389d`
- Result lineage SHA-256:
  `c5133a91084c9bd5b8647c646b1748f2ccad9f69426d8a0f52d49f2169cdf5b7`
- Result active SHA-256:
  `126ea4c83c54098d0c76d404876de83a30e5bda82f17a8a871dc9591b754b738`
- Full logical scan: 429 sources, 1,412,359 events, zero invariant failure.
- Preserved evidence directory:
  `.orchestrator/logs/activity-rotation/resolved/activity-rotation-67ce2f6008cf241c92651c22681102f7e7fc472703698e1411cc87217de2ef99/`

The pending intent and stage files were removed only after the resolution,
active log, intended lineage, and full logical scan were durable.

## Recurrence prevention

The permission broker now denies auto-worker `ai_status.py` and
`ai-status.sh` commands unless the script resolves through the installed
`PANTHEON_COMMAND_ROOT`. Regression coverage includes the original
`cd /home/lupin/code/pantheon && ... python3 scripts/ai_status.py` command,
the pinned command-root form, and non-worker compatibility.

