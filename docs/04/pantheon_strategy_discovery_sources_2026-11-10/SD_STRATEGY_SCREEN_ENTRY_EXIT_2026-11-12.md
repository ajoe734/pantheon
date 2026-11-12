# SD-STRATEGY-SCREEN-ENTRY-EXIT-20261112 — a strategy produces its own candidate pool, then times entries inside it

| Field | Value |
| --- | --- |
| Extends | `SD_PERSONA_STRATEGY_POOL_AUTONOMY_2026-11-12.md` §4.3 (replaces "permitted universe P as a fixed persona list") and `SD_VIEW_UNIVERSE_CROSS_SECTIONAL.md` |
| Repo base | `origin/dev` 8d19981d7 |
| Hosted reads | read-only listing of `/data/source-ingest/market_data_store/normalized` on the dev VM, 2026-11-12 |
| Scope | design only |

## 0. 中文摘要（操作者用）

- **候選池是策略自己產生的。** 之前設計寫「persona 有一份固定的允許清單（例如台灣 50），策略在裡面選」，操作者 11/12 指出這是錯的。
- **每個策略有三段規則：**
  1. 選股規則：從整個市場篩出候選池，可以分多步（例如先選產業、再選個股），通常用財報、月營收、資金流向這類變動較慢的資料。
  2. 進場規則：每天從候選池裡挑出現在該買的，通常用價格和成交量。
  3. 出場規則：什麼時候賣，包含股票被移出候選池時怎麼處理。
- **邊界是全部有治理資料的台股**，只排除流動性太低、停牌、歷史資料不足的。
- **選股和進場可以各自獨立演化**；候選池的更新頻率跟著資料公布頻率走。
- **驗證要檢查選股規則有沒有貢獻**：拿同樣的進場規則用在全市場來比。
- **目前的資料缺口（§3）：** hosted 上只有 61 個交易日的日線和期貨契約。財報、月營收、三大法人、估值這些在程式裡有定義，但 hosted 上沒有資料；產業分類完全沒有資料集。這是實作前要先補的。

## 1. Operator statements (2026-11-12, verbatim)

> 策略選標的 ，你的策略應該同時產生出適合的策略挑選標的規則。
> 然後這些策略挑選才是選出pool裡面的標的
> 然後才從pool裡面看哪個要買進
> 應該是這樣吧?
> 例如你可能的策略是買轉機股，那可能做法是從股票財報裡面找出基本面正在轉好，然後才從這邊找價格技術面出現突破這種股票??
> 或是例如你可能判斷金融類股是資金淨流入，然後從這種類型股票裡面找到哪個體質更好? 或是有價差

Confirmed after the coordinator restated it as screen / entry / exit, independent evolution of screen and entry, and
refresh cadence following data release cadence: 「對，應該是這樣沒錯」.

## 2. Strategy structure

```
 market boundary M(D): every Taiwan symbol with governed data, minus illiquid / suspended / too-short history
        │  screen (ordered steps; slow data; refreshed on its data's release cadence)
        │     e.g. step 1 sector filter (net inflow into financials) → step 2 quality rank inside the sector
        ▼
 candidate pool C(D)  (snapshot stored with members, exclusions and reasons; point-in-time)
        │  entry rule (daily; price / volume / spread) → per-symbol view {direction, strength}
        │  exit rule (time, signal, stop, and "left the candidate pool")
        ▼
 member views → persona combination layer → instrument expression (underlying or derivative) → risk gate → ledger
```

* `factor_rule_v1` gains a `screen` section: an ordered list of steps, each a filter or a rank-and-cut over the
  current set, using governed datasets with their availability lag; group steps (by industry) are allowed. The
  existing `entry` / `exit` / `strength` sections apply only to members of C(D).
* The screen's refresh cadence is declared per step and may not be faster than its slowest input's release
  cadence.
* Every C(D) snapshot is stored and hashed into the decision key (as designed for universe snapshots).
* Short entries are expressed through stock futures only (2026-11-07 instrument scope).
* Hypothesis cards state the screen idea and the entry idea separately; either may be missing, in which case
  the compiler pairs the card with a declared default (whole boundary as screen, or a plain hold-while-in-pool
  entry) and records that it did.

Operator examples in this structure:

| | Turnaround stocks | Financials with net inflow |
| --- | --- | --- |
| Screen | fundamentals improving in financial statements | step 1: the financial sector shows net capital inflow; step 2: better quality inside the sector |
| Entry | price breakout among pool members | a price gap / relative mispricing among pool members |

## 3. Data needed and what exists (hosted, 2026-11-12)

| Data | In the feature catalogue (`services/registry/factor_rule.py:109-143`) | On hosted |
| --- | --- | --- |
| Daily bars | yes | `tw_price_daily`, 61 daily partitions 2026-08-12 … 2026-11-11 |
| Two-year bar history | — | fetched on the collector host (backfill finished 2026-11-11T12:37Z); not yet in the Pantheon store |
| Monthly revenue, quarterly statements, margin, valuation, institutional flows, macro, community | yes | no normalized dataset present |
| Stock futures contracts | — | `taifex_ssf_contracts`, 5 partitions |
| Industry classification (point-in-time) | no dataset; `industry_neutralize` is parsed but rejected | none |
| Sector-level capital flow | none (derivable from institutional flows plus industry classification) | none |

Why the catalogued datasets are absent on hosted was not investigated here.

## 4. Validation additions

* **Screen contribution test**: the same entry and exit rules are run on the whole boundary M; the candidate is
  admitted only if the screened version is better out of sample after costs. A screen that adds nothing is
  dropped, and the candidate is re-registered as entry-only.
* **Point-in-time**: C(D) uses only data available by D−1 including each dataset's release lag; history must
  include symbols later delisted or suspended, otherwise the backtest is marked survivorship-limited and is
  not admitted.
* **One evaluator**: backtest and paper compute C(D), entry and exit through the same screen/entry/exit evaluator
  code path; only the data feed differs. A backtest-only screen implementation is not admissible.
* **Minimum pool size**: below a declared minimum the strategy holds, it does not trade a pool of one or two
  names by accident.
* Trial counting: screen variants and entry variants both count as trials of the family.

## 5. Evolution

Screen and entry are separate mutation surfaces. The persona may keep a screen and replace the entry, or the
reverse; children carry which part changed, so attribution can show which part earns.

## 6. Tasks to open (after this document is reviewed)

| Task | Scope |
| --- | --- |
| `DISCOVERY-DATA-HOSTED-GAP-AUDIT` | why catalogued datasets and the two-year history are absent on hosted; what enabling each needs |
| `TW-INDUSTRY-CLASSIFICATION-DATASET` | governed point-in-time industry classification from the exchange's public data; sector flow derived from it |
| `FACTOR-RULE-SCREEN-SECTION` | §2 rule format, evaluator, candidate-pool snapshots, backtest/paper parity; after `PERSONA-PORTFOLIO-COMBINATION-20261112` (shared files) |
| `HYPOTHESIS-CARD-SCREEN-ENTRY` | card and compiler split |
| `GATE-SCREEN-CONTRIBUTION` | §4 |
