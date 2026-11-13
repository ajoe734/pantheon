# Paper pool admission and weights

Task: `POOL-ADMISSION-AND-WEIGHTS-20261112`; owner Codex2; reviewer Claude.
Composes with merged `POOL-MEMBER-BASELINE-20261112` (PR #7228).

## Delivered contract

The persona app exposes authenticated, tenant-scoped
`POST /api/personas/{persona_id}/strategy-pool/health` and
`POST /api/personas/{persona_id}/strategy-pool/decisions`.
The latter accepts `expected_version`, `execution_context: paper`,
`action: admit|reweight`, observed `books`, risk `observations`, and a governed
OHLCV `dataset` for admission. A candidate names only its registry reference,
checksum and optional demand reference. No candidate records an explicit
idle-budget refusal without inventing a trial.

Each policy verdict, including refusal, appends through the existing pool
store's CAS, digest chain, and registry checks. Existing pool read/version
routes expose the decision, exact policy snapshot/digest, input digest,
book source references, backtest references, reasons and persistent persona
trial count. Conflicts are 409. Authentication/tenant failures do not write.
Registry availability failures are 503 and are not converted to market verdicts.

The old record append route still supports initial record bootstrap, risk
reductions and removals. Later additions, allocation increases and trial
promotion cannot bypass policy through that route. The safety ceiling also
applies to bootstrap. It remains a record API; the existing owner-record seed
is the path for discovering the first running member.

## Calculations and boundaries

- The JSON policy holds all starting thresholds: 20-day window, 60% low
  utilisation, effective bets below 2, correlation strictly below 0.5, trial
  cap 10% until 20 distinct observed days, halve at 5% and remove at 10% own
  high-water drawdown, and maximum 20 live members.
- Health reports rolling gross/limit and drawdown/limit means, idle day share,
  and correlation spectral participation ratio `n² / sum(C_ij²)` as effective
  bets. The method is named in the response; it is a correlation-spectrum
  diversity measure rather than an entropy estimate. Correlations use the
  same observed dates across member virtual books. Missing/constant histories
  and missing denominators are unknown with reasons, never zero-filled.
- Weights use inverse-volatility risk parity and bounded Sharpe tilts. No tilt
  occurs before the policy history minimum. High-water drawdown requires
  since-inception books, not a rolling slice. A persisted drawdown cap halves
  the prior allocation once per drawdown episode; repeat requests do not keep
  halving it. Haircuts and trial caps leave risk budget unused.
- Admission resolves each artifact's own existing owner-verified gate proof;
  an approval state or summary number alone is insufficient. Caller-supplied
  gate verdicts, Sharpe metrics, trial counts and policy thresholds are not
  accepted. Persona attempts accumulate across successful and refused
  admission versions. The candidate Sharpe haircut is
  `trial_penalty_scale * sqrt(2 * log(persona_trials))`, in addition to strict
  improvement of the actual proposed pool over the current pool.
- A read-only historical replay uses observed governed daily bars, the real
  registry interpreter, and the existing immutable envelope combination rule.
  All members and both envelopes use the same trailing 30% OOS window (with
  earlier bars supplying warmup), previous-bar exposures, commission and
  Taiwan sell costs. Opening and closing costs are included. No fitting or
  parameter search occurs in this path. The return basis is explicitly the
  net underlying exposure-fraction virtual-book basis, not the gate's separate
  share-sized portfolio basis.
- Full allocation, full observed gross/drawdown utilisation, or a full latest
  risk observation requires replacement of a weaker member. Missing risk-budget
  observations refuse admission. Candidate correlation is checked against
  every currently active member, including the proposed replacement victim.
  No utilisation measure relaxes any gate or statistical threshold.
- Authenticated persona mutation principals supply source-referenced observed
  books and governed OHLCV inputs. Books require matching checksums, a known
  return basis, ordered unique dates and complete high-water history. There
  are no synthetic production observations, members or baselines.
- Admission conservatively stays within the current pool's approved symbol
  union; this task does not infer authority to widen the persona's universe.
- Per R6, a decision only changes the versioned pool record. Execution still
  requires a separately approved immutable envelope, DeploymentPlan and
  RuntimeBinding. The response says `requires_approved_envelope`; this task
  does not deploy or change capital, orders, or persona lifecycle.

## Evidence and test scope

`services/research/tests/fixtures/pool_admission_tw_recorded.json` contains
original TWSE STOCK_DAY responses for 2330 and 2317, September–December 2024,
with source URLs. October/November responses reuse the portfolio-combination
task's captures; September/December were retrieved from TWSE on 2026-11-12.
The default split supplies 24 observed OOS return intervals,
2024-11-27 through 2024-12-31. The existing momentum candidates fail on this
data; the refusal is retained rather than weakening policy to manufacture a
market success.

The HTTP integration test covers admit, reweight, replace and refuse, then
reads all versions through the existing routes. It uses FakeRegistry at the
registry owner boundary and a clearly test-only constant-long approval-shaped
entry, with schematic virtual books, to exercise state transitions. These
fixtures are not genuine approval evidence for that variant and are not
hosted proof. The production OOS evaluator itself is not patched: it consumes
the recorded TWSE bars and real artifact interpreter. Separate tests use the
baseline task's actual recorded gate proof fixtures and reject missing proof,
wrong checksums, foreign tenants and failed gates.

Validation commands, terminal results and artifact hashes are in
`evidence.json`. This is source/API verification; daily persona-cycle wiring,
envelope delivery and natural hosted acceptance belong to their downstream
tasks. Independent review and merge are pending until canonical approval and
supervisor integration; this record does not assert `done`.
