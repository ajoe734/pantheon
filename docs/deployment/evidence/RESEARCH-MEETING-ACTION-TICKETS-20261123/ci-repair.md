# Source-owned CI repair, 2026-11-23

PR #7760 was returned to Codex after Branch CI run 45110756737,
job 137488110249 (`Test sweep (shard 3)`) failed. Both failures in
`services/control-plane/bff/test_rw01_research_ticket_contract.py` returned
503 because the existing injected `JsonFileOwnerStore` has the original
`get`/`put`/`list_all` contract and no `compare_and_set` method. The prior
owner validation omitted that existing BFF contract suite.

Ordinary RW-01 ticket creation and KW-02 note creation again use `put`.
The same unintended contract expansion affected notes, so the existing KW-02
BFF contract is included in the repair validation. Both paths still reject
the meeting namespace and attempts to replace a meeting-bound record.
Meeting assignment/evidence creation retains its separate atomic path;
meeting revisions, CAS transitions and evidence rechecks are unchanged.
No endpoint, historical record or supported caller is removed. No new writer,
collection, timer or runtime growth is introduced by this repair; the
previously recorded storage budget and residual findings still apply.

Current dev `0c806ffed` was merged into the published task branch without
rewriting its history. The repair anchor is `7954a3441`. The new published
head requires independent Antigravity review; the prior head's approval is
not reused. Local component verification is not hosted acceptance.

## Terminal validation

- Existing RW-01/KW-02 BFF contracts and owner tests: 26 passed, exit 0,
  using the checkout-scoped provisioned Python.
- Isolated PostgreSQL 16 owner/HTTP/Persona suite: 134 passed, exit 0.
  The test container was stopped and removed after completion.
- Full local shard 3 rerun: all 79 test files succeeded, zero failed,
  wall time 226 seconds, exit 0. Existing environment-gated skips are
  retained and visible in [ci-repair-shard3.txt](ci-repair-shard3.txt).
- Storage manifest: 10 unit tests passed; inventory validator passed with
  no failures. Whitespace and evidence JSON parsing checks passed.

Exact commands, bounded batch outputs and exit statuses are recorded in
[ci-repair-validation.txt](ci-repair-validation.txt). Hosted acceptance and
the previously named RC2/RC3/RC6 residual owners remain pending as documented
in the original handoff; this repair adds no new residual defect.
