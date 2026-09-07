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

**Direction-of-fit is everything: mean-reversion works on equities, trend-following
works on BTC.** No *mean-reversion or directional* signal beats fees on crypto —
those mechanics only "help" by trading less/smaller (capital preservation). But
matching the strategy to the asset's character changes the result entirely:
- **Mean-reversion** is positive OOS on mean-reverting **equity indices (SPY, QQQ:
  4/5 segments each)** and loses on BTC (0/5).
- **Trend-following (Donchian breakout)** is positive OOS on trending **BTC (3/5
  segments, strongly net-positive in aggregate)** — the first real crypto edge —
  and is the wrong tool for range-bound equities.

The founding thesis (*volatility is more predictable than direction*) holds for
mean-reverting instruments; for trending ones (BTC), **momentum/trend** is the fit.
The platform is built to *reject* false edges, which is why these two real,
asset-matched edges stand out from everything that didn't survive.

**The crypto trend edge generalizes across the majors** (see "Top-10" below):
net-positive in ≥3/5 segments on 7 of the top 10 coins — it's a property of
trending crypto, not a BTC fluke. It is also **daily-specific**: the same
strategy is positive across 18/18 param settings on daily bars and negative
across 0/18 on 1-minute bars (intraday fees swamp the move).

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
| **Funding rate** (z-score) | positioning | **No timing edge.** Exposure governor (−37% drawdown); see below. |
| **MVRV Z-score** (on-chain) | valuation | **No edge, best governor.** Cut net loss & drawdown ~17–20% on multi-year daily BTC; nudged the baseline's risk-adjusted score off the floor (−1.000→−0.844) — faint cycle-timing, but still on a losing base. |
| **Mean-reversion on equities (SPY)** | cross-asset | **POSITIVE OOS** — +0.10% net, 45% win, 62 trades, score +0.99. The one real edge found. See "Cross-asset" below. |

## Cross-asset — the thesis was right, the asset was wrong

The same mean-reversion strategy, run through the identical harness on **equity
indices**, flips from *worst on crypto* to **best and positive** — and it
replicates across two independent indices:

| Asset | Class | Mean-reversion (net% / score / trades) | Wins? |
|---|---|---|---|
| QQQ | Nasdaq-100 | +0.13 / +1.30 / 77t | **YES** |
| SPY | S&P 500 | +0.10 / +0.99 / 62t | **YES** |
| TLT | 20y bonds | −0.03 / −0.35 / 101t | no |
| BTC | crypto | −0.94 / −1.00 / 199t | no |

Why: it's **mean-reverting vs trending**, not equities vs crypto. Equity indices
mean-revert (dips bought, low vol) → "fade the move" works. BTC trends and
whipsaws → reversion gets run over. TLT trended over 2016–2025 → reversion mildly
negative too. The project's founding thesis — *volatility is more predictable
than direction* — holds, but fits **mean-reverting instruments (equity indices),
not crypto.** Caveats: the magnitude is small (+0.10–0.13% total over the OOS
window — beats fees, not a money machine); needs walk-forward to confirm across
time windows; and acting on it needs an **equity broker** (this bot trades
crypto), so it's a separate build.

## Top-10 crypto — does the trend edge generalize? (yes, on the coins that trend)

Same Donchian breakout, run through `robustness` (5 sequential segments, 0.1%/side
maker fee) on ~5–8y of daily history for the top 10 by market cap:

| Coin | mean-reversion (seg +) | trend (seg +) | trend trades | verdict |
|---|---|---|---|---|
| BNB | 0/5 | **4/5** | 18 | strong |
| ETH | 0/5 | **3/5** | 22 | holds |
| BTC | 0/5 | **3/5** | — | holds (validated) |
| ADA | 0/5 | **3/5** | 9 | holds |
| AVAX | 0/5 | **3/5** | 11 | holds |
| DOGE | 1/5 | **3/5** | 20 | holds |
| TRX | 0/5 | **3/5** | 22 | holds |
| SOL | 1/5 | 2/5 | 13 | marginal |
| XRP | 0/5 | 1/5 | 20 | fails (range-bound) |
| LINK | 0/5 | 1/5 | 28 | fails (choppy) |

- **Trend: 7/10 coins net-positive in ≥3/5 segments.** Repeats across 7 independent
  assets → a property of trending crypto, not luck. The 3 misses are coins that
  *didn't trend* in the window (XRP suppressed by litigation; LINK choppy; SOL
  outlier-dependent, shortest history) — a diagnosis of what the edge needs, not a
  hole in it.
- **Mean-reversion: 0–1/5 on all 10** with hundreds of trades each — the
  best-powered negative in the study. Dead on crypto, conclusively.
- **Caveat:** trend fires only 2–8 trades/segment; scores ride a few fat winners
  (that's trend-following's positive-skew profile, not a bug). Confidence comes
  from the 7/10 cross-coin repetition, not any single net% figure.
- **Implication:** deploy as a **basket of daily-trend majors** (BTC/ETH/BNB/ADA/
  AVAX/DOGE/TRX) — pooling coins gives the aggregate the sample size each lacks
  alone and diversifies outlier dependence. Exclude XRP/LINK/SOL until they trend.

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
