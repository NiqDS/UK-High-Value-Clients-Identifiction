# Daily algorithm review: v3 safety build

Date: 26 November 2026.

Status: paper build. This is not a live release.

The operator confirmed that the four-hour AWS service is disabled. This build does not start an AWS service.

## 1. Purpose and terms

The system tests a long-only daily trend signal. It uses spot assets and quote cash. It does not use borrowing or derivatives.

A signal is a request derived from completed price bars. A fill is a record of an executed order.

Notional is units multiplied by the estimated execution price. Equity is cash plus the marked value of holdings.

An entry claim prevents another submission for the same daily signal. A separate order journal stores the order plan and execution state. An acknowledgement does not prove a fill.

The strategy expects a few large gains to pay for failed breakouts. Historical profit does not prove future profit.

## 2. Evidence behind the design

The verified research has two periods:

- Historical period: 22 September 2020 through 31 May 2026.
- New audit period: 1 June 2026 through 24 November 2026.

The new period was first examined during this audit. It is not proved unused by every earlier researcher.

Three missing months changed the old simulator result from +633.08% to +476.58%. The original settings were retained for that comparison.

The later cash models used 206 USDT initially. Fees were 0.10% per side. Additional price cost was 0.05% per side.

| Cash model | Historical return | New-period return |
|---|---:|---:|
| Selected seven assets, separate allocations | +469.59% | +8.39% |
| Historical 15 assets, separate allocations | +200.44% | +0.78% |
| Historical 15 assets, shared cash and seven positions maximum | +385.31% | +5.33% |

The shared-cash model remained positive at the tested higher costs. Its new-period return fell to approximately +3.57% under the largest combined cost stress.

Entry-priority changes produced historical returns of approximately +378% to +399%. All tested priorities gave the same new-period shared-cash return.

The broad separate-allocation model was weaker. Higher costs made its small new-period profit negative. Five nearby parameter settings also lost money there.

The selected seven-asset new-period result contained nine trades. Three trades produced approximately 92% of net profit.

The four-hour signal lost 6.16% at base costs in the new period. Higher costs increased the loss.

A raised channel stop reduced some outage losses. It reduced the selected seven-asset new-period return from 8.38% to 3.70%.

The build therefore retains the 50/15 daily signal and the original exit rule. It does not select a new parameter winner.

## 3. Active profile and asset universe

The default paper profile is `config.bybiteu-daily-paper.yaml`. It uses `ccxt.bybiteu`, quote cash in USDC, and these symbols:

BTC/USDC, ETH/USDC, BNB/USDC, ADA/USDC, AVAX/USDC, DOGE/USDC, and TRX/USDC.

The public EU spot instrument endpoint returned 129 instruments on 26 November 2026. All seven USDC pairs had Trading status. None of the seven USDT pairs appeared. The response had no next-page cursor.

Source: https://api.bybit.eu/v5/market/instruments-info?category=spot

Response SHA-256: `0c2c14b97eb1b9e2185e1f8ef9b581c2deebb5b76f4b126b31955c0b2ca68aa7`.

The USDT configurations remain available for research. They are not the default EU profile. The historical results in this document use USDT prices. They do not prove USDC performance. No account funds were converted.

A public listing does not prove account permission, fee tier, available balance, or native stop support. Private EU authentication remains unresolved.

The historical 15-asset universe tests selection bias. It is not automatically copied into the active profile.

The active profile has these settings:

- Daily completed bars.
- Entry channel: 50 bars.
- Exit channel: 15 bars.
- Target entry notional: 27 quote units.
- Maximum positions: seven.
- Absolute entry cap: 30 quote units.
- Percentage entry cap: 15% of marked equity.
- Cash floor: 10 quote units.
- Additional cash buffer: 5 quote units.
- Poll interval: 60 seconds.
- No fixed take-profit.
- No raised channel stop.
- No trend, funding, MVRV, or regime filter.
- Event filter disabled. No verified active macro-event calendar is supplied.
- Learning disabled. Strategy parameters do not change automatically.

The profile is not a constant-percentage portfolio. Entry amounts remain approximately fixed as account value changes.

Consequently, old accumulated profits can reduce later percentage exposure. A fresh account can have a different loss percentage in the same year.

## 4. Exact bar and signal rules

Let `C[t]` be the last completed daily close. Daily bar timestamps refer to their opening time in UTC.

The current forming bar is removed before signal evaluation.

The required window contains at least 51 completed bars. Entry and exit windows exclude the signal bar.

```text
Upper[t] = maximum high of bars t-50 through t-1
Lower[t] = minimum low of bars t-15 through t-1

Entry signal: no tracked position, and C[t] > Upper[t]
Normal exit: tracked position, and C[t] < Lower[t]
```

Equality does not trigger a signal.

The last completed bar must be the preceding UTC day. Required timestamps must be ordered, unique, and one day apart.

Prices must be finite and positive. Volume must be finite and nonnegative. Open and close must lie within low and high.

Missing, stale, duplicate, malformed, or unordered bars block this daily calculation. The code does not shorten the window to conceal missing dates.

The profile does not yet contain an approved daily market-suspension calendar. A missing daily bar therefore blocks the signal.

Historical hourly tests handled 13 common missing hours separately. Remaining hourly bars reproduced daily prices and volume for all seven assets.

Those tests invented no candles or fills during the missing hours.

## 5. Timing and once-per-signal behavior

The loop checks each configured symbol every 60 seconds. It does not assume execution at the exact daily opening price.

A new completed daily bar can produce an entry. Repeated polls of that bar use the same identity.

The identity contains the strategy version, symbol, timeframe, channel periods, bar-opening timestamp, and purchase side.

```text
Identity input = daily-v1 | symbol | 1d | entry period | exit period | bar open | buy
Client identity = tb- plus the first 24 hexadecimal SHA-256 characters
```

An SQLite record claims an accepted entry after risk checks and approval. The claim is committed before submission.

A second claim for the same identity is refused. Claims survive process restart.

A failed or uncertain attempt keeps its claim. The current stage does not automatically release that claim.

This prevents same-bar entry attempts after a stop. It can also miss an entry after a temporary execution rejection.

The order journal distinguishes prepared, unknown, open, partial, filled, canceled, and rejected states. An unknown outcome blocks another submission. Recovery queries the venue interface with the saved identity. It does not repeat an uncertain submission.

The claim store remains separate from fill accounting. It prevents another entry for the same signal, including after a terminal rejection. This can miss a valid retry. The journal stores the immutable order plan before submission. The paper venue is implemented. A real EU recovery adapter is not certified.

## 6. Account value and shared cash

Live account valuation code uses quote cash and every priced holding. It does not treat quote cash alone as total equity.

```text
Equity = total quote cash + sum(base units * current mark)
Free cash = free quote balance
```

Cash and quantities must be finite and nonnegative. Free quote cash cannot exceed total quote cash.

An asset outside the priced allowlist blocks valuation. The code does not assign that asset a zero price.

A missing or invalid holding price blocks entry authorization. No simulated balance replaces an unavailable live balance.

Valuation is cached for up to 30 seconds. A fill invalidates the cache. This cache is not an execution-price guarantee.

Paper mode starts with the configured cash. Purchases and sales change that cash.

```text
Paper purchase cash change = -(fill cost + fee)
Paper sale cash change = fill proceeds - fee
Paper equity = remaining cash + marked paper holdings
```

SQLite is the authority for daily paper cash and positions. Each fill updates cash, units, cost, stop state, trade history, and daily counters in one transaction. A failure before commit rolls back all updates. JSON is a legacy format. Existing history without a verified journal requires migration.

The EU paper profile uses a new v3 database. The USDT research profiles use a separate v3 database. Profiles that share a database are alternatives. A process lock prevents concurrent persistent runners for the same resolved database path. It does not lock an exchange account across different databases or hosts.

## 7. Entry size and current-price budget

The daily close decides whether a signal exists. The current ask determines purchase units.

Let `A` be the current ask, `E` marked equity, `F` free cash, and `f` the configured taker fee fraction.

```text
Available notional = max(0, F - floor - buffer) / (1 + f)

Budget = minimum of:
    configured target notional
    absolute entry cap
    percentage entry cap * E
    available notional

Cash units = Budget / A
Stop-risk units = (E * risk fraction) / (A - initial stop)
Order units = minimum(Cash units, Stop-risk units)
```

The ask must exceed the initial stop. Otherwise, the stale breakout cannot authorize a new purchase.

The normal risk engine checks the resized intent. Market-order risk estimates use the current bid or ask instead of an old signal close.

Example: a 27-USDT budget at an ask of 150 gives 0.18 units. The old quantity of 0.27 would cost 40.50 USDT.

At a 0.10% fee, the 27-USDT order also requires approximately 0.027 USDT for the fee.

The adapter rounds units down with the venue quantity precision. It checks active spot status, quote currency, quantity limits, and minimum cost. These checks require current market metadata. They do not prove private order acceptance. Market orders can fill beyond the observed quote.

After approval, the pipeline fetches a fresh quote and account snapshot. It repeats risk checks and verifies the daily signal timestamp. It rejects an expired signal or an excessive order. It does not silently increase quantity. The price check is not a venue-enforced maximum fill price.

## 8. Exit and protection rules

The normal exit closes the actual tracked quantity. It does not derive exit units from a fixed notional.

The initial stop remains:

```text
Initial stop = minimum(Lower[t], signal close * 0.99)
```

This is not a 1% loss cap. A low channel can place the stop far below entry.

A local stop sells when the latest observed price reaches or falls below the saved stop.

The stop is fixed at entry. The normal 15-bar channel exit is recalculated from completed bars.

No fixed profit target is used. No new raised channel stop was adopted.

The main signal poll interval is one minute. A separate local protection task checks saved holdings every five seconds. A known stop can execute before candle retrieval. A candle outage therefore does not itself suppress this check.

The local task still needs the process, server, ticker feed, and execution path. An unresolved order for the same symbol can block an exit. This task is not protection during server failure.

A separate protection interface checks a stable protective identity, acknowledged quantity, saved stop, and current position state. Its fake-venue tests pass. No live EU adapter is connected to that interface.

Generic V5 documentation describes spot conditional orders. It does not certify EU account support or cancellation behavior:

https://bybit-exchange.github.io/docs/v5/order/create-order

A live release needs a venue-held stop, recovery from history and executions, and tested stop-versus-sale cancellation races. Live startup remains blocked.

## 9. Risk limits and their meaning

The configured limits include seven positions, eight open orders, fourteen daily trades, and 206 quote units of daily traded notional.

The daily loss setting is 28 quote units. The cash floor is 10 quote units, with a 5-unit sizing buffer.

These settings limit entry authorization. They do not guarantee a maximum realized loss or maximum portfolio drawdown.

The 15% per-trade risk ceiling remains deliberately wide. It is not a recommendation to lose 15% on each trade.

The journal saves the first valid marked equity for each trading day. The loss check uses the change from that baseline, including unrealized value. A restart preserves the baseline. The first mark can occur after midnight. A gap before that first mark is not included. This is not a continuous midnight valuation service.

## 10. Order recovery and net accounting

The journal saves the order plan before submission. A stable client identity joins the plan, venue query, and executions. Every execution needs an immutable fill identity, quantity, price, fee, fee currency, and timezone-aware timestamp.

An open or closed acknowledgement alone does not create a fill. A closed acknowledgement with zero executions fails. A partial execution records only its actual quantity. An incomplete closed response remains partial until recovery supplies the missing records.

Duplicate delivery has no second economic effect. A duplicate identity with different content fails. Decimal values are normalized before comparison. Non-finite, negative, or boolean monetary values fail validation.

A cancellation response remains uncertain until the venue interface proves finality. Known late executions still apply. A proved terminal state cannot move backwards. The real adapter must query execution history as well as open orders. A missing open-order record does not prove no fill.

The fee rules are:

```text
Buy with quote fee:
    received units = gross fill units
    cost basis = gross units * price + quote fee
Buy with base fee:
    received units = gross fill units - base fee
    cost basis = gross units * price
Sell with quote fee:
    removed units = gross fill units
    net proceeds = gross units * price - quote fee
Sell with base fee:
    removed units = gross fill units + base fee
    net proceeds = gross units * price
Released cost = prior cost basis * removed units / prior units
Realized net = net proceeds - released cost
```

A partial sale retains the remaining cost in proportion to remaining units. A full sale releases all remaining cost. A fee in a third asset fails closed. No conversion rate is invented.

Net trade records drive the weekly report and database statistics. They do not subtract recorded fees twice. Entry records have zero realized net. Legacy rows retain their old reporting interpretation; they are not certified journal accounts.

A paper execution is deterministic from the saved order plan. This permits restart recovery after a commit failure. It does not model exchange queue priority, missing liquidity, or a fill during an outage.

## 11. Controls and remaining release gates

| Control | State |
|---|---|
| Daily 50/15 signal and original initial stop | Retained |
| Shared marked equity and current-ask sizing | Implemented |
| Required daily grid and archive completeness | Implemented |
| Decimal journal, fee-inclusive cost, net reports | Implemented and tested offline |
| Atomic fill application and duplicate protection | Implemented and tested offline |
| Unknown outcomes, partial fills, and restart recovery | Implemented with paper and test venue interfaces |
| Fresh risk check after approval | Implemented |
| Single process per database | Implemented |
| Local stop task independent of candle retrieval | Implemented; requires running server |
| Venue-held stop acknowledgement interface | Tested with a fake venue only |
| EU USDC public market eligibility | Verified on the stated date |
| Four-hour service | Disabled by operator; installer rejects its named service |
| Live CLI and legacy live broker | Blocked |

These release gates remain:

1. Resolve private EU authentication and account permissions.
2. Test the USDC profile with suitable USDC data, actual fees, spread, and lot sizes.
3. Implement and certify the real durable execution adapter. Query order history and executions across restarts.
4. Certify native stop creation, quantity updates, gap behavior, and cancellation races on EU spot.
5. Define third-asset fee conversion and verified legacy account migration.
6. Test server disk failure, backup recovery, monitoring, and clock behavior on AWS.
7. Test out-of-band fills, deposits, withdrawals, and manual holdings without invented cost history.
8. Collect a new forward paper period. Compare simple exposure rules at comparable risk.

Do not remove the live block because offline tests pass. The installer guard is not a complete inventory of services.

## 12. Calculation evidence

Research calculations use:

```text
Purchase cost = units * purchase fill price
Sale proceeds = units * sale fill price
Trade net = sale proceeds - sale fee - purchase cost - purchase fee
Return percent = (final equity / initial equity - 1) * 100
Drawdown percent = max(1 - equity / running prior peak) * 100
```

Eight independent calculation checks rebuilt 635 trades and 9,020 daily equity records. They matched the research outputs within floating-point rounding.

This confirms the research arithmetic. It does not establish actual executable fill prices.

The separate reference ledger passed 450 unique fills, 316 injected failures, 450 restarts, and 450 duplicate-message checks.

The production journal now also replays the eight base research cases. It reproduces all 635 closed trades and 1,270 executions. Each execution is delivered twice. Final cash and net reports match the reference within 0.00000001 quote units. This checks accounting, not signal execution.

## 13. Software test evidence

The build runs `scripts/offline_test.py`. It uses temporary state and blocks Python network calls and `.env` access.

The tests include the existing regression suite and new daily-policy cases.

New cases check marked equity, invalid marks, current-price caps, paper cash recovery, stale or missing bars, and persistent signal claims.

They also check the daily runner wiring, the retired four-hour configuration, and the live startup block.

The final suite passed 446 tests with Python networking and .env access blocked. It includes a child-process death before commit, restart recovery, late partial fills, base fees, malformed balances, exact protection coverage, and process locking. New module lint passed. This stage has no new AWS test result.

The actual daily runner also replayed stored prices from 1 June through 24 November 2026. It completed 29,736 symbol passes and nine closed trades. Starting cash was 206 USDT. Final cash was 223.20657846 USDT: +8.352708%. Maximum drawdown at hourly closing marks was 10.501467%. Fees were 0.50371029 USDT.

The replay uses hourly opening prices with 0.05% adverse price cost per side and 0.10% fees per side. It assumes healthy infrastructure. It does not apply actual lot sizes, an order book, Telegram approval delay, or native stops. It uses USDT research data. Its result is not a USDC or live-performance forecast.

## 14. Review questions for another model

- Does shared cash introduce symbol-order bias or cash starvation that the tests have not captured?
- Can a cached mark or delayed approval permit an order above the intended cash or risk budget?
- Can the same economic signal obtain a second identity after configuration changes or migration?
- Can an accepted claim prevent a legitimate retry after a proved terminal rejection?
- Can a late fill occur after cancellation, and how will the fill record reach the ledger?
- Can cash, units, cost, fee, and stop records diverge across any crash boundary?
- Can a price gap place a new position below its planned stop at submission?
- Can missing market data prevent protection without an effective independent stop?
- Does a token conversion preserve units, cost, and price history correctly?
- Are the research asset list and active venue eligibility being confused?
- Does fixed entry notional conceal risk for newly funded accounts after earlier profits accumulate?
- Does the strategy beat simpler exposure rules at comparable risk across genuinely new periods?
- Which assumptions still depend on exchange behavior that no offline test can establish?

## 15. Source map

- `src/tradingbot/strategy/trend.py`: channel signal and initial stop.
- `src/tradingbot/strategy/daily_policy.py`: window checks, stable identity, and entry claims.
- `src/tradingbot/app/runner.py`: account value, current-price sizing, paper cash, and local exits.
- `src/tradingbot/risk/engine.py`: numerical account checks and market-price budgets.
- `src/tradingbot/execution/pipeline.py`: claim before submission.
- `src/tradingbot/store/journal.py`: canonical cash, cost, fills, positions, and daily equity.
- `src/tradingbot/execution/journal_broker.py`: durable submission and recovery.
- `src/tradingbot/app/protection.py`: protection acknowledgement interface.
- `src/tradingbot/app/instance_lock.py`: single-runner lock.
- `src/tradingbot/app/portfolio.py`: position view and strict legacy snapshot parsing.
- `src/tradingbot/__main__.py`: build wiring and live startup block.
- `tests/test_daily_policy.py`: daily policy regression cases.
- `tests/test_journal_safety.py`: journal, crash, fee, recovery, and protection cases.
- `scripts/replay_accounting.py`: production journal research parity.
- `scripts/replay_daily_runner.py`: historical runner replay.

The full research records are in the audit workspace under `outputs/strategy-validation-phase2-20261125`.

The combined archive contains the public data, historical source, tests, calculation records, and earlier results.
