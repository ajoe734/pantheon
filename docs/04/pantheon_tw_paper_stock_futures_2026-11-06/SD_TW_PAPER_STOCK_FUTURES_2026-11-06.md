# SD-TW-PAPER-STOCK-FUTURES-20261106 — Taiwan paper shorting through listed stock futures

Status: design for review (Reviewer: Antigravity). Owner: Claude. Date: 2026-11-06.
Scope of this change: this document and `docs/deployment/evidence/SD-TW-PAPER-STOCK-FUTURES-20261106/evidence.json` only. No source, test, workflow or configuration change. No implementation task is created by this work.

## 1. Decision, scope and non-scope

**Operator decisions, 2026-11-06** (recorded from the task brief; not re-opened here):

1. Taiwan paper shorting stays.
2. It follows real market rules.
3. It is done through derivatives: listed single-stock futures (股票期貨 / ETF 期貨) at 臺灣期貨交易所 (TAIFEX).

**Not decided:** whether the *long* side also moves to futures. §9 compares both with real prices and recommends; the choice stays open and §13 shows which tasks change.

**Out of scope, stated explicitly:** no live order, no broker sandbox call, no futures smoke (`services/broker/sinopac/README.md` line 140 forbids it), no real capital. Everything below is paper simulation inside the existing paper runtime. The securities-margin-short route (融券 at 90%) is not reopened: for 1 lot it needs 2,016,000 (2330), 211,500 (2317), 87,435 (0050) against a 100,000 ledger (brief, operator already saw it).

**Why the current ledger is wrong.** `services/control-plane/bff/persona_provisioning_coordinator.py` ~L2124 writes `positive_action=BUY`, `non_positive_action=SELL`, `lookback_bars=2`, `order_quantity=1`, `quantity_type=SHARES`. `services/execution/lean_runtime/paper_signal_producer.py` L418 maps SELL to SHORT without reading holdings. `services/execution/lean_runtime/paper_runtime.py` `SubmitTaiwanBrokerOrder` (~L414–580) computes `next_quantity = current + signed_fill` and `next_cash = cash − signed_fill × price`, hardcodes `sec_type=equity`, has no holdings or margin check, and `initial_cash` defaults to 100000 (L289) with no caller overriding it. A SELL on zero shares therefore yields −1 share and credits the proceeds to cash, which no Taiwan broker allows. The risk event near L3443 is written after the fill and is a label, not a pre-trade check.

## 2. Contract mapping

### 2.1 Official sources (all read 2026-11-06)

| Source | Gives | How obtained |
| --- | --- | --- |
| `https://www.taifex.com.tw/cht/2/stockLists` | underlying ↔ 2-letter contract family ↔ contract unit (股), sessions | public HTTPS GET, HTML table parse (also linked `file/taifex/CHINESE/2/2_stockinfo.ods`) |
| `https://openapi.taifex.com.tw/v1/SingleStockFuturesMargining` (stocks) and `/SingleStockFuturesETFMargining` (ETF) | full contract code (family+`F`), underlying code, margin rates/amounts, `Date` | public GET; the ETF endpoint returns **CSV with a BOM even when JSON is requested**; the stock endpoint returned JSON on the same day |
| `https://openapi.taifex.com.tw/v1/DailyMarketReportFut` | per contract month, per session: Last, `SettlementPrice`, Volume, OpenInterest | public GET, JSON |
| `https://openapi.taifex.com.tw/v1/FinalSettlementPriceSSF` | final settlement price by contract month | public GET, JSON |
| `https://www.taifex.com.tw/cht/2/sTF` (股票期貨契約規格) | unit, last trading day, settlement, hours, daily settlement price definition | public GET |
| `https://www.taifex.com.tw/cht/5/formulaStock?menuid1=12` | final settlement price formula | public GET |
| `https://www.taifex.com.tw/cht/4/feeSchedules` | exchange fees, futures transaction tax | public GET |

### 2.2 Selection rule (deterministic, fail closed)

Input: a Taiwan symbol such as `2330.TWSE`, `2330.TW`, `0050.TWSE`.

1. **Normalize the underlying.** Take the numeric/alphanumeric code before the first `.`. `2330.TW` and `2330.TWSE` both normalize to `2330` and are **one underlying** with one position book (§7). Any other suffix (`.TWO` etc.) is looked up by the same code; a code with no listing fails closed (step 3).
2. **Candidate contracts** = rows of `SingleStockFuturesMargining ∪ SingleStockFuturesETFMargining` with `UnderlyingSecurityCode == code`, joined to `stockLists` on `family = contract[:-1]` (contract `QFF` → family `QF`, unit 100). Only codes of the form `^[A-Z]{2}F$` that appear in a margin table **and** in `stockLists` qualify.
3. **Choose** the candidate with the **smallest contract unit**; tie-break by contract code ascending. The rule is "smallest listed unit" because the ledger is 100,000 and the large contract never fits for 2330 (§5.5). Whether one contract fits available margin is a run-time pre-trade decision (§5.4), not a mapping decision.
4. **Fail closed** with event `paper_order_rejected`, `reason=no_listed_stock_futures_contract`, if no candidate exists, if the margin row for the contract is missing, or if the unit is missing. Never fall back to a negative share ledger.
5. **Adjusted codes.** `DailyMarketReportFut` carries adjusted-type codes such as `CM1`, `DC1`, `DE1` (base code + digit), and spread rows with a `/` in `ContractMonth(Week)` (e.g. `202611/202612`). Neither is a candidate: step 2 requires `^[A-Z]{2}F$`, and contract-month rows must match `^\d{6}$`. An open position whose contract later gets an adjusted-code counterpart is **not** auto-translated: the position is held, marked at its last standard-code settlement, a `paper_position_contract_adjusted` event is emitted, and new orders on that underlying are rejected with `reason=contract_adjusted_unsupported` until an operator-governed task handles it. (Handling of adjusted contracts' changed units is unverified; the official 契約調整 pages were not read.)

### 2.3 Exact results (data dated 20261105)

| Underlying | Candidates (code, unit) | Selected | Multiplier | Margin row (clearing / maintenance / initial) |
| --- | --- | --- | --- | --- |
| 2330 | CDF 2,000; QFF 100 | **QFF** (小型台積電期貨) | 100 shares | 10.00% / 10.35% / 13.50% (級距1) |
| 2317 | DHF 2,000 (no small contract) | **DHF** (鴻海期貨) | 2,000 shares | 10.00% / 10.35% / 13.50% (級距1) |
| 0050 | NYF 10,000 units; SRF 1,000 units | **SRF** (小型元大台灣50ETF期貨) | 1,000 units | 6,400 / 6,700 / 8,700 TWD per contract (fixed amounts) |

Contract units come from `stockLists`: CD 2,000, QF 100, DH 2,000, NY 10,000, SR 1,000. DHF trades regular session only (the `DailyMarketReportFut` rows for DHF have no 盤後 session; `stockLists` shows `-` for after-hours).

## 3. Contract lifecycle

Official rules (`/cht/2/sTF`, read 2026-11-06): contract months are the next two consecutive months plus three successive quarter months (Mar/Jun/Sep/Dec); the last trading day (最後交易日) is the **third Wednesday** of the delivery month; last settlement day = last trading day; if the last trading day is a domestic holiday or force majeure prevents trading, the **nearest following business day** becomes the last trading day, "but the exchange may adjust" (詳見股票期貨契約交易規則; the trading-rules text itself was not read, so the "may adjust" clause is the only part verified second-hand from the spec page footnote); the expiring month trades 08:45–13:30 on its last day with no after-hours session; a new month starts trading on the first regular session after the last trading day; settlement is cash, net of the difference between the final settlement price and the contract price. Final settlement price = arithmetic average of the underlying over the 60 minutes before the close on the last settlement day (formula page, which uses the TWSE/TPEx index-publication prices between 12:30 and 13:25 plus the last closing index tick). Daily settlement price = volume-weighted average of trades in the final minute of the regular session (`/cht/2/sTF`).

Consistency check with data: `FinalSettlementPriceSSF` returns `TheFinalSettlementDay=20261021` for contract month 202610. October 2026: Oct 1 is a Thursday, so Wednesdays are 7, 14, 21 and the third is **2026-10-21** ✓.

**Rules for the ledger**

* **Order month** for a decision on trading day D: the nearest `^\d{6}$` month in the report whose last trading day is **strictly after D**. On the last trading day itself no new position is opened in the expiring month (the report has no after-hours row for it either).
* **Last trading day** = third Wednesday of the month; if it is not a Taiwan trading day, the next trading day, using the repository's calendar (`MARKET_CALENDAR_AND_SESSION_POLICY.md`; the exact calendar reader to bind is an implementation decision, so holiday-shift correctness against the official TAIFEX calendar is **unverified** until the implementation task tests it with a holiday month).
* **No automatic roll.** A position held through expiry is cash-settled at `FinalSettlementPriceSSF` for that month on the last settlement day, recorded as `paper_futures_settled`, and the position becomes flat. The next signal opens a position in the then-nearest month. An automatic roll would be an invented strategy action that real futures do not perform.
* **Settlement price missing.** If `FinalSettlementPriceSSF` has no row for the month on the settlement day, the position stays open as `settlement_pending`, is marked at its last daily settlement price, new orders on the underlying are rejected with `reason=settlement_pending`, and the check repeats each tick. Nothing is substituted.

**Dated example across the November 2026 expiry.** 2026-11-18 is the third Wednesday (Wednesdays in Nov 2026: 4, 11, 18; no holiday on Nov 18 is known to this author, calendar unverified). Illustrative scenario for 2330 with QFF, capital 100,000; prices marked *[known]* are from `DailyMarketReportFut` dated 20261105, the others are **illustrative inputs, not market data**:

| Date | Event | Price | Ledger effect |
| --- | --- | --- | --- |
| Thu 2026-11-12 | SELL signal, flat. Order month 202611 (LTD 11-18 > 11-12). Fill at prior settlement | 2,247 *[known 11-05 settlement as stand-in input]* | open −1 QFF @2247; cash unchanged (100,000); margin reserved 100×2247×13.5% = 30,334.5; fee/tax per §6 |
| Mon 2026-11-16 | still short; mark | 2,230 *(illustrative)* | unrealized = (2247−2230)×100×1 = +1,700 |
| Tue 2026-11-17 | SELL signal while short | — | no-op: already at target direction (position stays −1; no add, to keep order_quantity=1 semantics; §5.2) |
| Wed 2026-11-18 | last trading day; no new 202611 orders. 13:25 final settlement | 2,215.40 *(illustrative final settlement)* | `paper_futures_settled`: realized += (2247−2215.40)×100 = +3,160; cash += 3,160 − costs; position flat; margin released |
| Thu 2026-11-19 | next SELL signal; order month 202612 (known 11-05 settlement 2255) | per §4 | opens −1 QFF 202612 |

## 4. Price timing

Definitions (for a decision on trading day D, taken at the first runtime tick on or after 08:45 D):

* **Signal input**: stock daily bars from the existing market-data path, closed up to D−1. Unchanged. These are **stock** prices.
* **Fill price**: `SettlementPrice` (regular session, `TradingSession=一般`) of the **selected contract month** for the most recent trading day D−1 (the previous Taiwan trading day). It is public from D−1's evening. Basis label on every fill: `fill_price_basis=futures_daily_settlement_prev_session`. This is the only price used; the after-hours rows have `SettlementPrice=NULL` and are ignored.
* **Mark price**: the latest published regular-session `SettlementPrice` for the position's own month. Label `mark_basis=futures_daily_settlement`.
* **No future information**: a decision on D uses prices dated ≤ D−1 only; the signal input is already closed bars through D−1 and the fill price is the D−1 settlement. No order uses a price not public at decision time.

**Publication times.** The exchange does not document an exact openapi publication time in the sources read. Measured 2026-11-06 about 11:27Z (19:27 Taiwan): `DailyMarketReportFut` still held only `Date=20261105` while Taiwan stock marks already held 2026-11-06, so the openapi trails by at least one session at that hour. Its precise daily release time is therefore **unverified**; the design depends only on "D−1 is published by the first tick of D", and fails closed otherwise. The daily file `file/taifex/Dailydownload/DailydownloadCSV/Daily_2026_11_06.zip` was available at 11:28Z but contains trade ticks (~2 million rows) with no settlement column; it is **not** used.

**Lag behaviour (exact).** If the newest `Date` for the contract month is older than D−1 the order is rejected, no fill, event `paper_order_rejected`, `reason=futures_price_stale`, with `observed_price_date` and `required_price_date`. Marks keep the last known settlement and are labelled `mark_stale=true`. A stock close is **never** used as a futures price. Where this document shows stock prices in place of futures prices (§9) they are labelled `stand_in=stock_close`.

## 5. Ledger

Notation: `m` multiplier, `P` fill price, `q` signed contracts, `A` average entry price (price of the position's own contract month), `I`/`M` initial/maintenance margin per contract.

### 5.1 Formulas

State per underlying per contract month: `q`, `A`, plus account `cash`, `realized`, `costs`.

* **Open** (q=0 → q'=±n): `A' = P`; `cash` unchanged (**no sale proceeds are credited; opening a short adds no cash**); `margin_reserved = Σ|q|·I`.
* **Add** (same sign): `A' = (|q|·A + n·P)/(|q|+n)`; cash unchanged.
* **Reduce** (opposite sign, n < |q|): `realized += n·m·(P − A)·sign(q)`; `cash += that realized amount`; `q' = q + (opposite n)`; `A` unchanged.
* **Close** (n = |q|): as reduce, `q'=0`, `A'=none`, margin released.
* **Flip** (n > |q|): close |q| as above, then open `n − |q|` at `P`, margin checked for the new leg only after the close's release.
* **Cash** thus moves only by realized PnL and costs. **Equity** = `cash + Σ q·m·(Mark − A)`.
* **Total PnL** = `realized + unrealized − costs` and reconciles to `equity − initial_cash`.

### 5.2 Order quantity

Units are literal and never reinterpreted: existing immutable equity artifacts keep `quantity_type=SHARES` meaning shares and are unchanged; new futures artifact revisions explicitly carry `instrument_type=stock_future`, `quantity_type=CONTRACTS`, `order_quantity=1` (§8), and the runtime rejects `SHARES` on a `stock_future` artifact (no SHARES-to-contract compatibility mapping). **Proposed per-order policy:** SELL while already short and BUY while already long are a no-op (event `paper_order_noop`, `reason=already_at_direction`) so one signal never pyramids; this keeps exposure per signal fixed at one contract. §9 replays a *labelled alternate* cumulative-sizing scenario only to compare capacity; it is not this policy.

### 5.3 Margin definitions

`margin_reserved = Σ|q|·I_c`. `I_c = P_ref·m·InitialMarginRate` for rate-based rows (QFF, DHF) and the fixed `InitialMarginAmount` for ETF rows (SRF 8,700). Maintenance `M_c` likewise from the `MaintenanceMarginRate`/amount. `P_ref` is the latest settlement price of the contract month (§4). `available_margin = equity − margin_reserved`.

### 5.4 Pre-trade check and rejection event

Before any open/add/flip leg: require `equity ≥ margin_reserved_after`, where `equity` is marked at the current `P_ref` (equity before the new order's costs) and `margin_reserved_after` re-marks **all** contracts, existing and new, at that same current `P_ref` (§5.3), not entry-time margins. Fees only reduce equity further and can only cause earlier rejection. Otherwise, **before any fill**: event `paper_order_rejected`, `reason=insufficient_initial_margin`, with `required`, `available`, `contract`, `price`, `price_date`. No cash/position change. This replaces the post-fill label at paper_runtime ~L3443.

### 5.5 Maintenance breach

At each mark: if `equity < Σ|q|·M_c` the account is in breach: event `paper_margin_call` (`equity`, `required_maintenance`), new opening orders rejected with `reason=margin_call_active`, only reducing orders are accepted. If `equity < Σ|q|·M_c` persists for the next full session mark (approximating the real next-day追繳 window; the exact broker call/liquidate procedure is broker-specific and **unverified**), the runtime force-closes the position at that mark with event `paper_forced_liquidation`. Official minimum: brokers may not charge below the exchange-announced initial and maintenance levels (`/cht/2/sTF`, 保證金).

### 5.6 Worked numbers (settlement prices of 2026-11-05; capital 100,000)

| Contract | Settlement | Notional = P·m | Initial margin | Max contracts = ⌊100,000 / I⌋ |
| --- | --- | --- | --- | --- |
| QFF (202611) | 2,247 | 2,247×100 = 224,700 | 224,700×13.5% = **30,334.5** | 3 (3×30,334.5 = 91,003.5; 4 would need 121,338) |
| DHF (202611) | 237 | 237×2,000 = 474,000 | 474,000×13.5% = **63,990** | 1 |
| SRF (202611) | 97.35 | 97.35×1,000 = 97,350 | fixed **8,700** | 11 (11×8,700 = 95,700) |

These were recomputed by `replay.py`-style arithmetic in this task's scratch session and match the brief. For comparison one CDF (2,000 shares) needs 2,247×2,000×13.5% = 606,690, which is why QFF is selected.

## 6. Costs

Official sources: `https://www.taifex.com.tw/cht/4/feeSchedules`, read 2026-11-06.

* **Exchange fees** (per contract, per side, TWD; 交易經手費 / 結算手續費 / 交割手續費): stock futures with a *stock* underlying 3 / 2 / 2; ETF futures with a 10,000-unit underlying 7.5 / 5 / 5; ETF futures with a 1,000-unit underlying 4.8 / 3.2 / 3.2. The page does not distinguish the small stock contract (QFF) from the 2,000-share one; the page row is applied literally (per contract, regardless of unit).
* **Per-side cost used by the ledger** = 交易經手費 + 結算手續費: QFF/DHF 5.0; SRF 8.0. 交割手續費 is charged at delivery; cash-settled at expiry it is applied once at `paper_futures_settled`: QFF/DHF 2.0, SRF 3.2 (applicability to cash settlement is read as "settlement" by this author; flagged **unverified** against the trading rules).
* **Futures transaction tax** (期貨交易稅): contract amount × rate, per side, where the page footnote says "股價類即十萬分之2" = **0.00002**; at cash settlement, tax uses the final settlement price ("按結算之市場價格計算").
* **Fee-row mismatch resolved.** `openapi /FuturesAndOptionsFeeSchedule` (CSV with BOM) shows `STF,3,2,2,0.001` and `STO,3,2,2,0.00002`, which is swapped relative to the web page, whose table says stock **options** (標的為股票) 3/2/2/**0.001** and stock **futures** (標的為股票) 3/2/2/**0.00002**. The page and its footnote ("股價類即十萬分之2") agree and 0.001 is the options *premium* tax rate; **the page is the source of record, the openapi row is treated as mislabelled**, and the implementation must not read the openapi row blindly. The fee values are copied into a reviewed constants file (§10), not parsed at run time, with a drift test against the page.
* **Broker commission**: negotiated between the broker and the customer (page note: 期貨商實際向交易人收取之費用係由雙方自行議定). No official number exists; the paper model assumes **0 broker commission** and carries a parameter `broker_fee_per_contract_per_side` defaulting to 0, listed **unverified/assumption**.
* **Example**: open 1 QFF @2,247: fee 5.0 + tax 0.00002×224,700 = 4.494 → 9.494; closing at 2,230 adds 5.0 + 0.00002×223,000 = 4.46.

**Reconciliation**: `total_pnl = realized + unrealized − costs` where `costs = Σ fees + Σ tax`, every term carried per fill event (`fee`, `tax`, `multiplier`). `equity − initial_cash == total_pnl` is asserted by the ledger test.

## 7. Taiwan stock ledger after the change

For `.TW`/`.TWSE` (and other Taiwan market) symbols:

* **BUY** behaves per the long-side decision (§9); under today's behaviour (share ledger) it buys shares.
* **SELL with the share ledger holding no shares** (`position_shares ≤ 0`) never creates a negative share position and never credits proceeds. Outcome: if the artifact is short-capable (`instrument_type=stock_future`) the SELL is routed to the contract path (§5); otherwise the order is rejected with `paper_order_rejected`, `reason=sell_without_holdings` (no-op with no fill, no cash change).
* **SELL with shares held** reduces/closes the shares (long exit), clamped to holdings: SELL quantity greater than holdings fills only the held amount and emits `paper_order_partial_clamped`; the remainder is **not** opened short in the share ledger.
* `2330.TW` and `2330.TWSE` share one position book keyed by normalized underlying.
* **Non-Taiwan behaviour is unchanged**: US/other markets keep their current ledger path; the change is gated on a Taiwan-market check in the paper runtime and a Taiwan-only artifact field.

## 8. Strategy contract

* `services/registry/strategy_artifact.py` ~L908 allows quantity types `SHARES`, `CASH_VALUE`, `PERCENT_PORTFOLIO`. Add an execution-recipe field `instrument_type` ∈ {`equity` (default, absent = equity), `stock_future`} and, for `stock_future`, require `quantity_type=CONTRACTS` with `order_quantity` a positive integer. `CASH_VALUE`/`PERCENT_PORTFOLIO` are rejected for `stock_future` (contract rounding is undefined; unverified need).
* The recipe stores **only the underlying** (e.g. `2330.TWSE`), never a contract month (months roll); the contract code and unit are resolved at run time by §2 and recorded on each fill (`contract_code`, `contract_month`, `multiplier`).
* The persona provisioning template (`persona_provisioning_coordinator.py` ~L2124) emits `instrument_type=stock_future`, `quantity_type=CONTRACTS`, `order_quantity=1` for new Taiwan personas; BUY/SELL actions unchanged in name.
* **Existing artifacts and checksums stay valid**: no existing artifact is edited. The new fields are optional with default `equity`; the checksum is over the artifact content, which for existing artifacts does not change. New behaviour reaches personas only through *new* artifact revisions (§12), via the existing governed path `transition_legacy_persona_market`, which "preserves every byte, checksum, and version of the parent artifact".

## 9. Long side: same contract versus odd-lot shares

Real price sequences from the task (hosted paper fills, week ending 2026-11-06, in fill order): 2330: 2370, 2330, 2295, 2260, 2240, 2135; 0050: 104.95, 104.1, 100.6, 99.85, 97.15, 92.5; 2317: 245.5, 251, 245.5, 240, 235, 224.5. **These are stock prices used as a labelled stand-in** (`stand_in=stock_close`) because daily futures history is not in the repository.

**Two scenarios, kept distinct.** (A) *Proposed per-order policy (§5.2):* same-direction BUY while long is a no-op, so six consecutive BUYs open exactly one contract (QFF, DHF or SRF), five no-ops, and exposure is that of the first signal. (B) *Alternate cumulative-sizing scenario (labelled, not proposed):* every BUY adds one unit, to measure capacity and loss under a rejection stream. The §5.2 policy and the operator's still-open long-side choice are not decided by (B).

Scenario B replay rules: capital 100,000, no exits; before each BUY apply §5.4 literally: `equity = 100,000 + Σ m·(P − A_i)` over existing fills at the current price `P` (less costs already paid), `required = (n+1)·I(P)` with `I(P) = 13.5%·P·m` re-marked at the current `P` for all contracts (SRF fixed 8,700 each); reject if `equity < required`. Costs per §6 on fills (QFF/DHF 5.0 + 0.00002·P·m; SRF 8.0 + tax).

Trade-by-trade, 2330 QFF (m=100):

| BUY at | Equity before costs | Required for n+1 contracts at P | Result |
| --- | --- | --- | --- |
| 2,370 | 100,000 | 1×31,995 = 31,995 | fill (1) |
| 2,330 | 100,000 + 100×(2,330−2,370) = 96,000 | 2×31,455 = 62,910 | fill (2) |
| 2,295 | 100,000 + 100×((2,295−2,370)+(2,295−2,330)) = **89,000** | 3×30,982.5 = **92,947.5** | **reject** (insufficient_initial_margin) |
| 2,260 | 82,000 | 3×30,510 = 91,530 | reject |
| 2,240 | 78,000 | 3×30,240 = 90,720 | reject |
| 2,135 | 57,000 | 3×28,822.5 = 86,467.5 | reject |

Costs paid on the two fills total 19.40 (10.00 fees + 9.40 tax) and only lower equity. The earlier "3 fills / −59,000 / 94,432.5" figures were wrong: they used a static maximum of 3 at initial equity and summed entry-time margins.

2317 DHF (m=2,000): the 2nd BUY at 251 needs 2×251×2,000×13.5% = 135,540 versus equity ≈ 110,985 → reject; later BUYs stay rejected (equity falls to ≈ 57,985 at 224.5). 0050 SRF: equity (≥ 55,790 at the end) always exceeds n×8,700 (≤ 52,200), so all six fill.

| Underlying | Instrument | Exposure of 1st signal | A: per-order policy (fills / no-ops, gross PnL at last mark) | B: cumulative alternate (fills / rejected) | B margin at last mark (all contracts at last P) | B gross PnL at last mark | B entry fees + tax |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2330 | odd-lot shares (1 share) | 2,370 | 1 / 5, −235 (same as B only if shares pyramid; shares are not subject to this policy) | 6 / 0 (cash 13,630) | — | −820 | — (securities costs not modelled) |
| 2330 | QFF (100) | 237,000 | 1 / 5, −23,500 (100×(2,135−2,370)) | 2 / 4 | 2×2,135×100×13.5% = 57,645 | −43,000 | 10.00 + 9.40 |
| 2330 | CDF (2,000) | 4,740,000 | 0 / rejected on the first BUY, 0 | 0 / 6 (first needs 639,900) | — | 0 | — |
| 2317 | odd-lot shares | 245.5 | 1 / 5, −21 | 6 / 0 (cash 1,441.5) | — | −94.5 | — |
| 2317 | DHF (2,000) | 491,000 | 1 / 5, −42,000 | 1 / 5 | 60,615 | −42,000 | 5.00 + 9.82 |
| 0050 | odd-lot shares | 104.95 | 1 / 5, −12.45 | 6 / 0 (cash 599.15) | — | −44.15 | — |
| 0050 | SRF (1,000) | 104,950 | 1 / 5, −12,450 (1,000×(92.5−104.95)) | 6 / 0 | 52,200 | −44,150 | 48.00 + 11.98 |

For the share rows scenario A means one share only because the same no-op rule is applied for symmetry; the operator may instead keep per-signal share accumulation (the share ledger today fills every BUY).

Findings:

* One futures signal carries 100× (QFF), 2,000× (DHF) or 1,000× (SRF) the exposure of one odd-lot signal. Under the proposed policy the same 2330 sequence loses 23,500 on one QFF (23.5% of capital) against 235 on one share; under the cumulative alternate it loses 43,000 on two QFF against 820 for six shares.
* On 2330 and 2317 the account supports at most 3 and 1 contracts at initial equity, and fewer as price falls against the position (2 on 2330 in this path), so cumulative long sizing would be a *capacity-bound rejection stream* instead of the current always-fill.
* With **mixed** instruments the signal stream is asymmetric: BUY adds 1 share, SELL opens 100 shares of short exposure (QFF). A long position of N shares cannot be offset by a contract, so a flip becomes "close shares, then open contract" — two ledgers per underlying and a path where long→short changes both instrument and size by 100×.
* Both options trip the drawdown baselines (§11), because the short side is futures in either case.

**Recommendation (not a decision):** long and short through the **same contract**. Reasons: one ledger per underlying, one margin account, symmetric flips, and no 100× jump at a flip; the cost is that long-side PnL scales too and that capacity is 1–3 contracts on 2330/2317. If the operator prefers to keep shares for the long side, the share ledger remains valid for BUY/exit only (§7) and the tasks marked ◇ in §13 change. The coordinator's prior ("both directions same contract") is **supported by the comparison**, but the 23,500 (one QFF) / 42,000 (one DHF) loss figures show it needs the re-baselining in §11 first; nothing here assumes it is chosen.

## 10. Data path

One path only: the existing source ingestion (no second fetch path).

* **Connector**: extend `tw-taifex-futures-options-chip` (`services/source_ingestion/connector_definitions.py` L885; hosts `openapi.taifex.com.tw`, `www.taifex.com.tw`) with a new dataset `taifex_ssf_contracts` covering `SingleStockFuturesMargining`, `SingleStockFuturesETFMargining`, `DailyMarketReportFut` (filtered to contracts in the margin tables), `FinalSettlementPriceSSF`, plus the `stockLists` unit table. Dataset-level: the CSV-with-BOM responses must be decoded with `utf-8-sig` regardless of the requested format.
* **Storage**: in the `SourceUpdateRule` in `services/source_ingestion/active_universe.py` (~L276) add a second rule alongside `taifex_futures_chip`: `raw/taifex/ssf/date=YYYY-MM-DD/`, `normalized/taifex_ssf_contracts`, retention 2555 days like the existing rule, `symbol_scope` = underlying codes of active Taiwan paper personas (not market-wide), cadence `daily_after_taifex_publication`.
* **Dev egress gate**: `scripts/deploy_nonprod_vm.sh` `validate_source_refresh_profile` (~L1222; the connector-specific branch ~L1282 names only `tw-twse-tpex-official-market`) and `.github/workflows/nonprod-deploy.yml` (~L1397–1400, `PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS` currently `openapi.twse.com.tw,www.tpex.org.tw`) must gain a second reviewed connector entry for `tw-taifex-futures-options-chip` requiring exactly `openapi.taifex.com.tw,www.taifex.com.tw`; no wildcard, no widening of the TWSE entry, and `SOURCE_INGEST_BOUNDED_CONNECTOR_ID` remains a fixed value per run.
* **Schedule**: after TAIFEX publication; because the observed openapi lag is at least one session at 19:27 Taiwan time, schedule at a time proven by implementation measurement (first hosted run); until measured, the runtime fail-closed rule in §4 protects orders.
* **Reader**: `services/data-plane/models/contract_master.py` (`ContractMaster` with multiplier, expiry, margin_type) has no production reader today; it is the intended read model for `normalized/taifex_ssf_contracts`, to be confirmed by the implementation task (if its fields do not fit, extend it rather than adding a new model).

## 11. Consumer table

Method: `git grep` for `paper_fill_simulated`, `fill_quantity`, `position_quantity`, `ledger_cash`, `order_quantity`, `quantity_type` excluding tests and docs; file lists in `evidence.json`. "Mult." = needs the multiplier/contract identity.

| Consumer (path) | Reads | Changes? |
| --- | --- | --- |
| `services/execution/lean_runtime/paper_runtime.py` | writes fills, `ledger_cash`, `position_quantity`; L289 `initial_cash`; risk event ~L3443 | **Yes**: ledger §5, pre-trade check, contract identity |
| `services/execution/lean_runtime/paper_signal_producer.py` L418; `signal_producer.py`, `signal_consumer.py`, `executor.py` | SELL→SHORT, `order_quantity`, `quantity_type` | **Yes**: SELL routing §7; executor `position_quantity`/`fill_quantity` metadata |
| `services/broker/paper_simulation.py` `simulate_paper_order` | price/qty only | **Yes**: multiplier/position parameters (or the runtime computes) |
| `services/registry/strategy_artifact.py` ~L908; `paper_strategy_spec.py`; `strategy-artifacts/tw-session-momentum-v1.registration.json` | `quantity_type` | **Yes** (new type, §8); the registration file unchanged (default equity) |
| `services/telemetry/runtime_summary.py` (`_fold_symbol_cost` L244, L931–933, L1581), `trade_episode_projection.py`, `feedback_adapter.py`, `capture.py`, `telemetry_event.schema.json` | fills → average cost/realized PnL **per share** | **Yes — Mult.**: PnL and cost need `multiplier`; add contract fields to the schema |
| `services/control-plane/bff/pm12/service.py` `_management_portfolio_holding_entry` L934 (reads at L1052–1054), `bff/agora/performance/service.py` L939–941 | quantity/avg price → holdings, value | **Yes — Mult.** (market value = q·m·price; show contract month) |
| `services/trade_journey/lifecycle_projector.py`, `lifecycle_projector_capacity.py`, `telemetry_bridge.py`, `incremental_materializer.py`, `lifecycle_noop_order.py` | fill events, `fill_quantity` | Mostly **No** (quantity is count); `lifecycle_noop_order` unaffected; verify the `paper_order_rejected` / `paper_order_noop` events are classified and not counted as fills |
| `services/reconciliation-drift/main.py` L3833–3860 | `fill_quantity` vs `requested_quantity` duplicate/drift check | **No** for counts; identity block should include `contract_code`; rejected orders must not appear as unreconciled |
| `services/persona/agent_usability_validation.py`, `remaining_gap_validation.py`, `cognitive_loop_runtime.py` | fills, quantity types | **Yes**: validation expectations (signed quantities, margin) |
| `services/trade_journey/hosted_lifecycle_probe.py`, `scripts/probe_dev_runtime_paper_lifecycle.py` (L1295) | counts `paper_fill_simulated` | **Probe expectations change**: a rejected order produces no fill; probe must accept `paper_order_rejected` as a valid terminal state |
| Hosted all-12 harness (`scripts/backfill_hosted_chain_persona_scope.py`, `scripts/deploy/release_manifest.py`, `.github/workflows/nonprod-deploy.yml`, `scripts/verify_e2e_evolution_loop.py`) | personas and quantities | **Yes** where they assume `SHARES` or expect fills; full list to be settled in the hosted acceptance task |
| `services/capital/risk_policy.py` (`drawdown_pct`) and `services/evolution/config/threshold_sweep_baselines.json` | drawdown thresholds | **Yes** — see below |
| Frontend | no direct reader of these fields was found by `git grep` in `*.ts`/`*.tsx` (only BFF output); consumes BFF holdings | Follows the BFF change; **not verified in a browser** |

**Drawdown policies.** `PAPER-PERSONA-BASELINES-20261105` approved `expected_drawdown` 0.0027 (2330), 0.0002 (0050), 0.0011 (2317) with 1.25× breach triggers 0.003375 / 0.00025 / 0.001375 (`evidence.json`), and `H12-4923-DRAWDOWN-POLICY-CONFIG-20261101` proposes 0.0325 for another chain. They were calibrated on 1-share paper PnL. Per signal the PnL grows ×100 (QFF), ×2,000 (DHF), ×1,000 (SRF); the §9 replay gives (per-order policy, one contract) 23,500 / 42,000 / 12,450 loss on 100,000 (cumulative alternate: 43,000 / 42,000 / 44,150) against triggers of 0.34% / 0.14% / 0.025% of capital. **They will trip on the first adverse move and must be re-derived** before cutover; the policies are `approved_by`-governed evidence, so the re-baseline is a governed review task (§13), not a config edit here.

## 12. Cutover

* Existing open Taiwan share positions are all negative today (2330.TWSE −6, 0050.TWSE −6, 2317.TWSE −4, and two 2330.TW −1 rows at 2026-11-06 11:25Z). They have no real equivalent.
* **Path**: the governed persona path in `persona_provisioning_coordinator.py`: archive the old persona via `_retire_persona` (L858: requires authoritative archive readback) and provision a new persona/artifact revision with `instrument_type=stock_future`, or create a child revision via `transition_legacy_persona_market` (preserves parent bytes/checksums). **No hand-edited store, no deleted history.** The historical negative-share fills and positions stay in telemetry, labelled by the old artifact id; they are not rewritten or netted.
* The new persona starts flat with the ledger from §5; history of the old artifact is read-only.
* **Fleet baseline counts**: `services/evolution/config/threshold_sweep_baselines.json` currently holds 7 baselines. Each new artifact id needs its own approved `expected_drawdown`; archived artifacts' baselines remain (history) but stop counting as active. The shared fleet count therefore rises by the number of new artifacts until the old ones are retired, and the hosted all-12 expectations that assume a count must be updated in the hosted acceptance task. Exact numbers depend on how many personas are cut over and are **not asserted** here.
* Switch: a feature flag in the paper runtime keyed on the artifact's `instrument_type`, default `equity`, so rollback is "provision with `equity`" without touching state.

## 13. Implementation split (ordered; none is created by this work)

| # | Task | Artifacts | Acceptance | Depends on |
| --- | --- | --- | --- | --- |
| T1 | Contract master + data path | connector dataset, active-universe rule, `ContractMaster` reader, constants file for fees; unit tests with the real captured payloads (BOM CSV, spreads, adjusted codes) | mapping §2.3 reproduced for 2330/2317/0050; fail-closed cases | this doc merged + operator decision |
| T2 | Egress/deploy gate | `deploy_nonprod_vm.sh`, `nonprod-deploy.yml` second reviewed connector entry | exact-host validation test; no widening of TWSE entry | T1 |
| T3 | Strategy contract | `strategy_artifact.py`, schema, provisioning template, registry tests | existing artifacts' checksums unchanged | T1 |
| T4 | Ledger engine | `paper_runtime.py`, `paper_simulation.py`, signal routing, events | §5.6 numbers (30,334.5 / 63,990 / 8,700; 3 / 1 / 11), pre-trade rejection, settlement, reconciliation of PnL | T1, T3 |
| T5 | Telemetry/BFF/consumers | `runtime_summary.py`, projections, `pm12`, `agora/performance`, probes, reconciliation drift | multiplier-aware holdings and PnL; probe accepts rejection events | T4 |
| T6 | Drawdown re-baseline | governed review for `threshold_sweep_baselines.json` | new `expected_drawdown` approved per artifact | T4 replay on futures PnL |
| T7 | Cutover | provisioning of new personas via governed path | old history intact, flags on | T4–T6 |
| T8 | Hosted acceptance | one hosted run: short open → mark → reduce → settle across a real expiry, rejection on over-margin, stale-price fail-closed | hosted evidence | T1–T7 |

If the long side stays on shares, ◇: T3 keeps `SHARES` for BUY and adds the dual-ledger rules, T4 gains the share-to-contract flip logic, T6 re-baselines only short-side exposure, and T7 changes only the SELL persona set; if the long side moves to futures, T4 drops the share-to-contract flip and T5 drops the share/contract mixed holdings display.

## 14. Sources and unverified items

Read 2026-11-06: the seven sources in §2.1, `https://www.taifex.com.tw/cht/5/stockMargining` (fetched, 200), plus the repository files cited. Openapi datasets are dated 20261105.

**Unverified (no official source read or not testable now):**

1. Exact publication time of openapi `DailyMarketReportFut`/`FinalSettlementPriceSSF` (only the one-session lag at 19:27 Taiwan time was measured).
2. Holiday shift of the last trading day: only the spec-page footnote was read; the 股票期貨契約交易規則 text and the exchange calendar for a holiday month were not.
3. Whether 交割手續費 applies to cash settlement at expiry.
4. Broker commission (assumed 0, negotiable).
5. Broker margin-call and liquidation timing (paper uses the §5.5 approximation).
6. Handling of adjusted-contract (調整型) units and codes.
7. Whether an openapi dataset exists for contract units (the units were read from the `stockLists` HTML; swagger was not retrievable as JSON).
8. Frontend behaviour (no browser verification).
9. Hosted behaviour: nothing in this document or its evidence claims hosted behaviour.
