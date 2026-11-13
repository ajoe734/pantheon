# Persona pool detail read evidence

Owner: Codex2. Assigned independent reviewer: Claude. Source delivery only;
no hosted/deployment or independent approval claim is made here.

Both `/bff/personas/{id}` and `/api/v1/operator/persona-management/{id}` now
return `data.strategyPool` with owner pool/history, member stages/weights and
baseline references, observed virtual books and health, latest cycles, and
open research demands. Caller authorization and tenant are forwarded to the
HTTP owners. A foreign persona is refused before pool reads.

Health/books are the latest daily cycle observation, pinned to the current
pool version and digest, with packet ID and observation timestamp. They are
not recomputed by BFF. Unknown owner measures remain unknown. Missing or
mismatched observations do not fall back to an older cycle. Incomplete marks,
missing provenance, absent metrics or wrong checksums suppress the member's
book with an explicit reason. Failed independent sections remain null with
an unavailable surface, while valid empty owner lists remain empty lists.

The production default OODA port has no provider. The new adapter therefore
uses the Persona owner's existing record decoder and configured shared path
when that default is missing. It supports the actual JSON envelope and JSONL
formats and never creates or writes the owner's file. Injected ports still
take precedence. No new scheduler, owner state, policy, data seeds, capital
action or execution path was added.

The new route tests generate pool/history/health with `PersonaStrategyPoolStore`,
books with `MemberVirtualBooks`, demands with `ResearchDemandStore`, and packets
with `OodaLoopPacket` / `append_ooda_packet`. The production router and new port
run unchanged; external HTTP and identity verification are the test boundaries.
A transport test also executes the real HTTP helper and checks GET-only calls,
authorization forwarding, tenant header and bounded timeout.

Validation commands, terminal results, acceptance mapping and limits are in
`evidence.json`. The full architecture scan exceeded its 90-second foreground
budget, and the broad router/facade run exceeded 60 seconds with partial
failures; neither is counted as passed. Focused inventory, live importer scan,
persona detail and owner-read checks completed successfully.

The unrelated route-count assertion expects 49 routes while the unchanged
dev router registers 53; this reproduces on a clean archive of base
`5a419c47f0827caa5ab9acee83a6743c3280cbaf`. The CPU reduction timing assertion
failed twice (32.6% and 41.7% of old CPU versus a 30% threshold), then passed
in isolation; it also passed on that clean base. This task changes neither
the list projection nor that timing test.
