# Pre-Launch Readiness — 7-Coin Daily Donchian Basket

**Status: PAPER (dry-run), running 24/7, collecting data. NOT yet live.**
Capital: ~£180 (~206 USDT funded) · Deployment: **Full (100%)** · Sleeves: 7 × ~23 USDT

_Generated as a decision reference for the September go-live. Descriptive, not a
forecast — the numbers below are what the strategy DID, not what markets WILL do._

---

## Strategy at a glance

- **What:** long-only **Donchian breakout** — enter on a new 50-day high, exit on
  the 15-day channel low. No take-profit; it rides the trend until the channel
  breaks. Sits in **cash** when there is no breakout.
- **Universe:** equal-weight basket of **7 coins** — BTC, ETH, BNB, ADA, AVAX,
  DOGE, TRX — each a 1/7 sleeve.
- **Venue:** Bybit **spot**, USDT-quoted, UK jurisdiction (no leverage, no
  derivatives).
- **Cadence:** decides on the **last closed daily bar** (parity with the backtest).

## What we validated

| Check | Result | Takeaway |
|---|---|---|
| Full-history backtest (2020-09 → 2026-05) | **+633%** net, **37.7%** modelled max drawdown, 32% time-in-market | Beats buy-and-hold on risk (hold = +1314% but **87%** drawdown). |
| Expectancy (daily trades) | 46% win rate, avg win +79% / avg loss −12%, **payoff 6.37**, **+29.8%/trade** | Healthy positive-skew trend profile — a few big winners. **Bull/survivor-inflated**; real forward is much lower. |
| Regime split | 2022 (bear): **−13.9%/trade** · 2023–26: **+11.4%/trade** | The edge lives in **sustained** rallies; bears produce false-breakout losses. |
| 2022 stress test | Strategy **−24%** vs buy-and-hold **−64%**; long only **9%** of the year | Built-in channel exit IS the bear protection — it dodges to cash. |
| 200-day SMA trend filter | **Rejected** — cuts return +633% → +76% AND worsens drawdown 37.7% → 42.4% | Loses on both axes. The filter forces late entries into tops. Do not use. |
| Per-coin review | All 7 coins **positive expectancy** over the full history | No coin deserves exclusion. Basket is well-constructed. |

## The deployment decision (your one real lever)

Deployment scales return AND drawdown together; lowering it caps the pounds at
risk but the drawdown floor is stubborn (~48% real even at 15%). `~real dd`
doubles the modelled figure for bull/survivor bias — **plan around that column.**

| Deploy % | Net % | Modelled DD | ~Real DD | Return/DD | Sleeve (of ~180) |
|---|---|---|---|---|---|
| **100 (chosen)** | **+633** | **37.7** | **~75** | **16.8** | **~23 USDT** |
| 75 | +509 | 35.9 | ~72 | 14.2 | ~17 USDT |
| 50 | +339 | 33.8 | ~68 | 10.0 | ~11 USDT |
| 25 | +170 | 28.8 | ~58 | 5.9 | ~6 USDT |
| 15 | +102 | 24.0 | ~48 | 4.2 | ~4 USDT |

**Chosen: Full (100%)** — best efficiency; rational for small "tuition" capital.
Revisit and dial down only once the account is large enough that a ~75% real
drawdown is genuine pain. More capital doesn't change the %s — keep the deploy %,
scale the absolute sleeve sizes.

## Go-live gates (in order)

1. **Bybit key: TRADE + READ, withdrawals DISABLED, spot.** — ✅ done _(verify
   withdrawals-off manually in the Bybit UI; the venue doesn't expose it via API)._
2. **Account funded + moved to the trading wallet.** — ✅ done (206 USDT visible).
3. **Relocate to a UK/EU host.** — ⏳ **pending · blocker.** The paper box is a
   Russian-cloud IP; a live UK Bybit account on that IP risks being frozen. Move
   before real money.
4. **Set `telegram.allowed_chat_ids`.** — ⏳ pending. The bot **refuses to start
   live** without it (so trades can't silently deadlock in pending-approval).
5. **Clean Step-1 paper run on the new host.** — ⏳ pending (markets load, data
   flows, no errors).
6. **Flip `app.dry_run` → `false`.** — the final switch. Real orders from here.

## Honest caveats

- **Backtest is bull/survivor-biased.** These 7 are today's survivors and the
  window includes 2020–21. Halve your return expectations; plan for ~2× the
  modelled drawdown.
- **Long-only.** It bleeds modestly in bears (dodges to cash) and can never short
  a downtrend.
- **Correlated basket.** 7 large-caps move together — real drawdown floor ~48%
  even at low deployment; diversification is limited.
- **Live data validates the pipeline, not the edge.** The 15m sandbox run proves
  the plumbing; edge evidence is the multi-year backtest.
- **Systematic — no prediction.** It never bets on calling tops or bottoms; the
  rules decide, in hindsight, trade by trade. That's the point.
- **Current market (Aug 2026):** just off a −52% correction, bouncing +25% —
  bottom or bear-trap is unknowable. The bot doesn't need to know.
