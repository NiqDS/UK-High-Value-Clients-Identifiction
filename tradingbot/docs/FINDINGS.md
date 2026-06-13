# Research findings — what we tested, and what is (and isn't) an edge

This document records the honest, out-of-sample (OOS) results of every signal
and overlay we evaluated. It exists so the conclusion isn't re-litigated and so
a future contributor knows exactly what ground has been covered.

**Method.** Every experiment is scored on a held-out OOS slice (default 40%) it
never tuned on, net of fees (0.6%/side) and slippage, with the risk-adjusted
metric `net return ÷ max drawdown`. A mechanic only "counts" if it HELPS OOS
**and** trades enough for the result to be trustworthy. Run it yourself:

```
python3 -m tradingbot compare --source csv --csv data/btc_1h_real.csv --oos 0.4 \
    --funding-csv data/funding.csv [--funding-gate] --report reports/compare.md
```

## The headline conclusion

**No entry edge was found in anything we tested.** On real BTC hourly data, no
signal predicts direction well enough to overcome fees. Every mechanic that
"helps" does so by **trading less or smaller** (capital preservation), not by
trading *better* (alpha). This is the expected, honest outcome — most simple
price/positioning signals are arbitraged away — and the platform is built to
*reject* false edges rather than flatter them.

## What we tested

| Signal / mechanic | Class | OOS verdict |
|---|---|---|
| SMA crossover (H1 baseline) | price / directional | **No edge.** −0.36% net, 32% win, loses to fees. |
| VWAP mean-reversion (H2 thesis) | price / volatility | **No edge.** −0.83% net, 19% win; worse than baseline. |
| Trend filter | price overlay | Neutral OOS. |
| VWAP valuation gate | price overlay | Neutral OOS. |
| Fee-drag controls | selectivity governor | **Helps** (+0.725) — but only by cutting to ~3 trades. It's "trade less," not edge. |
| VWAP scaled sizing | size governor | Marginal (+0.029). |
| Cycle regime (200w SMA + halving) | slow risk budget | Exposure governor by construction; not a trade trigger. |
| **Funding rate** (z-score) | positioning | **No timing edge.** See below. |

## Funding rate — the most-scrutinized signal

Funding is the cleanest non-price crypto signal (hard to fake; pulled free from
Binance's public dumps, around the exchange geo-block). We tested it two ways:

- **Size-only overlay:** scales long size by the funding z-score. Effect: same
  trades, same win rate (32%), but **net loss and drawdown both fell ~37%**
  (baseline −0.36%→−0.22%, maxdd 0.36→0.22; reversion −0.83%→−0.52%). Because it
  shrank numerator and denominator together, the *risk-adjusted* score was
  unchanged. It made the bot **trade smaller into crowded leverage**, not better.
- **Hard gate** (skip crowded-long entries): removed only 4 of 69 trades and
  **win rate did not move** (32%→32%). Funding rarely hit a crowded extreme at an
  actual entry, and skipping those didn't improve the hit rate.

**Verdict:** funding is a legitimate **exposure governor** (a real −37%
drawdown reduction, which matters for the capital-preservation mandate) but has
**zero entry/timing edge**. Keep it as a defensive overlay; do not treat it as a
signal.

## The "politician tracker" (Autopilot / Unusual Whales) — out of scope, here's why

Mirroring congressional trades is a real, talked-about idea, but it does not fit
this bot: (1) it's an **equities/options** signal — this bot trades crypto on a
single exchange; (2) **disclosure lag** under the STOCK Act is 30–45 days, so any
copy is weeks stale; (3) the headline outperformance is **concentrated in a few
names** (e.g. Pelosi +54% in 2024) while Congress *on average ≈ the index*
(2025: ~32% of portfolios beat the S&P). It's a separate equities project, not a
crypto signal.

## Where a *real* edge would have to come from

The platform is a fully-tested, risk-first execution shell with three working
exposure governors (fee-drag selectivity, cycle regime, funding). What it lacks
is an entry signal — and that is a **data-acquisition problem, not a code one**.
Candidate inputs we have *not* tapped, in rough order of promise:

1. **On-chain accumulation/distribution** — MVRV Z-score, SOPR, exchange
   net-position/reserves. Historically front-run major turns. Needs Glassnode /
   CryptoQuant (mostly paid). Would plug in exactly like the funding overlay.
2. **Cross-exchange / order-flow** — basis, CVD, liquidation maps. Higher
   frequency, more infrastructure.
3. **Event-conditioned volatility** — the bot's stated thesis. Volatility is
   more predictable than direction *around known events*; the event-risk +
   calendar machinery is already built to exploit this defensively.

Any new signal should be added as a strategy/overlay and run through this same
`compare` harness before it is trusted. If it doesn't beat the baseline OOS with
enough trades to be credible, it doesn't ship.
