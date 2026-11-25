# Daily algorithm review: v2, stage 1

Date: 26 November 2026.

Status: paper build. This is not a live release.

The operator confirmed that the four-hour AWS service is disabled. This build does not start an AWS service.

## 1. Purpose and terms

The system tests a long-only daily trend signal. It uses spot assets and quote cash. It does not use borrowing or derivatives.

A signal is a request derived from completed price bars. A fill is a record of an executed order.

Notional is units multiplied by the estimated execution price. Equity is cash plus the marked value of holdings.

An entry claim is a saved record that prevents another submission for the same daily signal. It is not a complete order record.

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

The candidate symbols remain:

BTC/USDT, ETH/USDT, BNB/USDT, ADA/USDT, AVAX/USDT, DOGE/USDT, and TRX/USDT.

This is a research list. Current Bybit EU eligibility is not certified. The exchange client in this paper profile remains the existing `bybit` client.

Do not treat this client name as confirmation of EU account access. The private EU authentication problem remains unresolved.

The historical 15-asset universe tests selection bias. It is not automatically copied into the active profile.

The active profile has these settings:

- Daily completed bars.
- Entry channel: 50 bars.
- Exit channel: 15 bars.
- Target entry notional: 27 USDT.
- Maximum positions: seven.
- Absolute entry cap: 30 USDT.
- Percentage entry cap: 15% of marked equity.
- Cash floor: 10 USDT.
- Additional cash buffer: 5 USDT.
- Poll interval: 60 seconds.
- No fixed take-profit.
- No raised channel stop.
- No trend, funding, MVRV, or regime filter.
- Event filter disabled. No verified active macro-event calendar is supplied.
- Learning disabled until net accounting and fill recovery are complete.

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

Full order recovery must distinguish a proved rejection from an unknown outcome. That work is pending.

The claim store is separate from fill accounting. It does not prove that an order filled, and it does not replace venue reconciliation.

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

Paper cash and positions are saved in the same JSON snapshot. Paper holdings without a verified cash balance require migration.

The two daily configuration filenames refer to the same new paper database. They are alternatives, not two services to run together.

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

Exchange quantity precision and minimums still require venue checks. Market orders can fill beyond the observed quote.

The current price checks are not a venue-enforced maximum fill price. A fresh check after delayed approval remains pending.

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

The poll interval is now one minute. This improves observation frequency, but it does not provide independent protection.

The current stop path still depends on earlier candle and order-state calls. A failed call can prevent the stop check.

An unknown order query deliberately blocks new submissions. That conservative policy can also block a local exit.

A health suspension itself permits a known risk-reducing exit. This positive control passed the fault test.

Independent protection remains pending. Actual Bybit EU spot conditional-order support and recovery must be verified before any live release.

## 9. Risk limits and their meaning

The configured limits include seven positions, eight open orders, fourteen daily trades, and 206 USDT of daily traded notional.

The daily loss setting is 28 USDT. The cash floor is 10 USDT, with a 5-USDT sizing buffer.

These settings limit entry authorization. They do not guarantee a maximum realized loss or maximum portfolio drawdown.

The 15% per-trade risk ceiling remains deliberately wide. It is not a recommendation to lose 15% on each trade.

The current daily-profit calculation is not a complete start-of-day marked-equity calculation. It needs the remaining accounting work.

## 10. What is implemented now

| Control | State |
|---|---|
| Daily completed-bar 50/15 signal | Retained |
| Shared marked equity | Implemented |
| Current-price entry sizing | Implemented |
| Market-price notional checks | Implemented |
| Finite account, mark, quantity, and stop checks on edited paths | Implemented |
| Strict required daily window | Implemented |
| Stable entry identity and persistent accepted-entry claim | Implemented |
| Evolving paper cash, saved with positions | Implemented |
| New paper database namespace | Implemented |
| Four-hour active config | Disabled; installer rejects its named service |
| Daily service default | Daily paper configuration |
| Live CLI startup | Blocked before broker construction, including saved-override attempts |
| Single-pass daily paper demo | Blocked; use the persistent runner |

The installer guard is not a general service inventory. The operator has already disabled the actual four-hour AWS service.

## 11. Work reserved for 04:00

The following faults remain release blockers:

1. Durable order and fill recovery is incomplete. Open acknowledgements, partial fills, late fills, and uncertain submissions need explicit states.
2. A closed response with zero filled units must not become a full fill.
3. Purchase fees are still missing from reported realized net profit. Paper cash includes the fee, but the report does not include full cost basis.
4. Base-asset and third-asset fees need one consistent accounting policy.
5. Order, fill, cash, position, cost, and protection records need atomic transactions and fill-message deduplication.
6. Position-file save errors can still be logged without stopping the loop. Database errors can interrupt snapshot updates.
7. Saved position validation remains incomplete for all numeric fields. Strict JSON parsing alone is insufficient.
8. Startup recovery can adopt holdings without their original cost and stop. It can also discard small genuine holdings.
9. Independent protection and its venue behavior remain unverified.
10. A process lock must prevent two daily instances from sharing the same state concurrently.
11. Historical archive downloads outside the daily runner can still skip missing files. Their validation must be integrated.
12. Exchange eligibility, fee tiers, quantity precision, and private authentication remain unresolved.

Do not remove the live startup block merely because paper tests pass.

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

That ledger is not yet integrated into this runner.

## 13. Software test evidence

The build runs `scripts/offline_test.py`. It uses temporary state and blocks Python network calls and `.env` access.

The tests include the existing regression suite and new daily-policy cases.

New cases check marked equity, invalid marks, current-price caps, paper cash recovery, stale or missing bars, and persistent signal claims.

They also check the daily runner wiring, the retired four-hour configuration, and the live startup block.

The final exact count and source changes are recorded with the build artifacts. This stage has no new AWS test result.

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
- `src/tradingbot/app/portfolio.py`: paper cash and position snapshot.
- `src/tradingbot/__main__.py`: build wiring and live startup block.
- `tests/test_daily_policy.py`: new expected-safe regression cases.

The full research records are in the audit workspace under `outputs/strategy-validation-phase2-20261125`.

The combined archive contains the public data, historical source, tests, calculation records, and earlier results.
