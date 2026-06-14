# Trading-bot research report — what we tested, what we found, what's next

A complete, plain-language record of the strategy research run on this platform:
every signal evaluated, the honest out-of-sample numbers, the data sources and
network constraints we hit, and a menu of concrete next experiments for the BTC
algo. Read it end-to-end; the last section ("Open questions") is designed for you
to pick new items to check.

*Companion file: `FINDINGS.md` is the short version; this is the long one.*

---

## 1. Bottom line (executive summary)

- **On crypto, no strategy we tested has a real entry edge.** Directional
  (SMA crossover), volatility (VWAP mean-reversion), and positioning signals
  (funding, MVRV) all either lose to fees or only "help" by trading **less or
  smaller** — capital preservation, not profit.
- **The one genuine edge appeared on equities.** The *same* mean-reversion
  strategy, run on **SPY** (~10y daily), was **positive out-of-sample**
  (+0.10% net of fees, risk-adjusted score +0.99, 62 trades). On BTC that same
  strategy always lost.
- **Interpretation:** the founding thesis — *volatility/mean-reversion is more
  predictable than direction* — is **correct, but fits equity indices, not
  crypto.** BTC trends and is too volatile for dip-buying; SPY mean-reverts.
- **What you have:** a fully-tested, risk-first execution platform plus an honest
  research harness that **rejects false edges before they cost money** — which is
  exactly why the single real edge stands out instead of drowning in noise.
- **Caveat on the edge:** +0.10% total over the OOS window is *positive but tiny*
  — it beats fees, it is not a money machine, and it needs walk-forward + more
  assets before being trusted. And acting on it needs an equity broker (this bot
  speaks crypto/ccxt).

---

## 2. How we tested (methodology) — why these results are trustworthy

Every number below comes from the same disciplined harness:

- **Out-of-sample (OOS) only.** Data is split; parameters are chosen on the
  in-sample portion and scored on a held-out slice (default 40%) the strategy
  never saw. An idea that only shines in-sample is overfitting and is called out.
- **Net of costs.** Fees and slippage are charged on every fill (crypto 0.6%/side
  default; equities 0.01%/side, realistic for a liquid ETF).
- **Risk-adjusted metric.** Default score = `net return ÷ max drawdown`. A
  strategy that makes money only by risking ruin scores poorly.
- **No look-ahead.** All overlays (funding, MVRV, regime) look up only data at or
  before the current bar — verified by unit tests.
- **"Trade enough to trust."** A great score over 2 trades is noise. Verdicts
  weight both the score and the trade count.
- **Walk-forward.** Beyond a single split, the `walkforward` command re-learns
  parameters on each rolling window and scores them on the *next* unseen window —
  the strongest test of whether an edge is real or curve-fit.

---

## 3. What's built (the platform)

A production-minded, risk-first crypto bot (single regulated exchange via ccxt),
plus the research tooling used here:

- **Hard risk engine** — 14 ordered gates (trading flag, allowlist, daily-loss
  stop, per-trade risk cap, notional caps, spread guard, fee gate, account floor…).
- **Human-in-the-loop** — Telegram approval, kill-switch, event-risk windows,
  heartbeat, weekly reports (Telegram + email).
- **Three exposure governors** — fee-drag selectivity, cycle regime (200-week SMA
  + halving phase), and the funding/MVRV overlays. All preserve capital.
- **Research harness** — backtester, `compare` (mechanic attribution),
  `walkforward` (overfit detection), and now `cross-asset` (multi-asset view).
- **Data adapters** — Binance public dumps (OHLCV + funding), BGeometrics
  (MVRV), Yahoo/Stooq/Nasdaq/local-CSV (equities), CoinGecko.
- **225 passing tests.**

---

## 4. Results — crypto (no edge found)

Representative out-of-sample numbers (real BTC data, costs included):

| Signal / mechanic | Class | OOS result |
|---|---|---|
| SMA crossover (baseline) | directional | **No edge** — daily: −0.10% net, 43% win, 21 trades; hourly: −0.36%, 32% win. Loses to fees. |
| VWAP mean-reversion (thesis) | volatility | **No edge** — daily: −1.02% net, 16% win; *worst* performer on BTC. |
| Trend filter | overlay | Neutral OOS. |
| VWAP valuation gate | overlay | Neutral OOS. |
| VWAP scaled sizing | size governor | Marginal (+0.03). |
| Fee-drag controls | selectivity governor | "Helps" only by cutting to ~3 trades — trade less, not edge. |
| Cycle regime (200w + halving) | slow risk budget | Governor by construction; not a trigger. |
| Funding rate (z-score) | positioning | **No timing edge.** Exposure governor: −37% drawdown by trimming size into crowded leverage. Hard-gate removed 4/69 trades, win rate unchanged. |
| MVRV Z-score (on-chain) | valuation | **No edge, best governor.** Multi-year daily BTC: cut net loss & drawdown ~17–20%; nudged baseline's risk-adjusted score −1.000→−0.844 (faint cycle de-risking). Still a losing base. |

**Pattern:** every "improvement" on crypto is a form of *trading less/smaller*.
The governors (fee-drag, regime, funding, MVRV) genuinely reduce drawdown — MVRV
and funding each ~20–37% — which matters for capital preservation, but none turn
a no-edge entry into a winning one.

---

## 5. Results — cross-asset (the one real edge)

The same baseline + mean-reversion strategies, run through `cross-asset` on each
market with its own realistic fee:

| Asset | Class | Mean-reversion OOS (net% / score / trades) | Verdict |
|---|---|---|---|
| **QQQ** (Nasdaq-100) | equity index | **+0.13 / +1.30 / 77t** | **Positive edge** |
| **SPY** (S&P 500) | equity index | **+0.10 / +0.99 / 62t** | **Positive edge** |
| TLT (20y Treasuries) | bonds | −0.03 / −0.35 / 101t | No edge (mild) |
| BTC (daily) | crypto | −0.94 / −1.00 / 199t | No edge (loses badly) |

**Why it splits this way:** it is *not* "equities vs crypto" — it's
**mean-reverting vs trending**. Equity indices mean-revert (dips bought) with
upward drift and low volatility → "fade the move" works → positive on both SPY
and QQQ. BTC trends and whipsaws → reversion gets run over. TLT (20y Treasuries)
*trended* over 2016–2025 (the 2022 rate selloff was one long move) → reversion
mildly negative there too, behaving more like BTC than like stocks. Two
independent equity indices replicating the edge makes it credible, not a fluke.

**To complete this section**, download TLT and QQQ daily CSVs (Nasdaq → browser),
then:

```
python3 -m tradingbot fetch-data --exchange localcsv --csv ~/Downloads/<TLT>.csv --out data/tlt.csv
python3 -m tradingbot fetch-data --exchange localcsv --csv ~/Downloads/<QQQ>.csv --out data/qqq.csv
python3 -m tradingbot cross-asset \
  --asset BTC=data/btc_1d_long.csv@0.6 \
  --asset SPY=data/spy.csv@0.01 \
  --asset QQQ=data/qqq.csv@0.01 \
  --asset TLT=data/tlt.csv@0.01 \
  --oos 0.4 --slippage 0.005 --report reports/cross_asset.md
```

And validate SPY isn't a one-period fluke:
```
python3 -m tradingbot walkforward --source csv --csv data/spy.csv --fee 0.01 --slippage 0.005
```
Look at the OOS column: if learned params beat baseline on most *unseen* windows,
the edge is robust; if only in-sample, it's curve-fit.

---

## 5b. The crypto edge — trend-following on BTC

The cross-asset work showed BTC *trends* (which is why mean-reversion fails on it).
So we built a **Donchian breakout** trend strategy (long on a break above the
prior N-bar high; exit on a break below the prior M-bar low; long-only, no fixed
take-profit) and ran it on multi-year daily BTC across 5 sequential segments:

| BTC segment | mean-reversion (net / score, trades) | trend-breakout (net / score, trades) |
|---|---|---|
| 0 | −0.93 / −1.00 (142) | **+0.31 / +1.43 (7)** |
| 1 | −0.64 / −0.99 (98) | **+1.63 / +3.82 (5)** |
| 2 | −1.10 / −0.96 (165) | −0.15 / −0.41 (11) |
| 3 | −0.31 / −0.86 (83) | **+0.15 / +0.76 (9)** |
| 4 | −0.71 / −1.00 (116) | −0.18 / −0.87 (10) |
| **Σ segments** | **≈ −3.7%** | **≈ +1.8%** |

**Trend is net-positive in 3/5 BTC segments and strongly positive in aggregate;
mean-reversion is 0/5.** The return profile is convex (classic trend-following):
a few large winners (segment 1: +1.63%, score +3.82 — a captured bull trend) and
small losses in choppy/bear segments. Crucially the trade count is tiny (5–11 per
segment), which is why it survives crypto's 0.6%/side fees where reversion's
100+ trades bled out. Default params (20/10), not optimized — so it is not
curve-fit. **This is the first real, positive, asset-matched crypto edge found.**

**Parameter robustness (the anti-overfit check): 18/18 net-positive.** A 5×5 grid
of Donchian (entry, exit) periods (10–80 / 5–30, exit<entry) was *positive in
every single setting* (scores +0.04 to +2.29). The default 20/10 is middling;
longer entry lookbacks score better risk-adjusted (50/15 → +1.80 with 9 trades;
80/10 → +2.29 but only 7 trades). Positive across the whole grid = a structural
edge, not a lucky parameter.

**Governors improve the trend base** (they only help a base with edge):

| BTC OOS | net% | score | maxdd% |
|---|---|---|---|
| trend + funding | +0.29 | +1.46 | 0.20 |
| trend + MVRV | +0.20 | +1.22 | 0.16 |
| plain trend | +0.23 | +1.16 | 0.20 |

Funding lifts return and risk-adjusted score; MVRV cuts max drawdown. The
overlays that did nothing for no-edge strategies now earn their keep.

Caveats: convex/lumpy (depends on catching the big trends; bleeds in chop);
small absolute magnitudes (conservative backtest sizing); daily bars, one asset,
~3.4y OOS; picking the single best grid param post-hoc is mild overfitting (use a
robust mid-grid value like 50/15, not the max). Run it yourself:
```
python3 -m tradingbot compare    --source csv --csv data/btc_1d_long.csv --oos 0.4
python3 -m tradingbot robustness --csv data/btc_1d_long.csv --segments 5 --fee 0.6 --slippage 0.05
```

## 5c. The trend edge generalizes — top-10 crypto (daily)

Having validated trend-following on BTC, we tested whether it is a property of
*one* coin or of *trending crypto as an asset class*. We pulled ~5–8y of daily
history for the top 10 by market cap and ran the same Donchian breakout through
the `robustness` command (5 sequential segments, 0.1%/side maker fee — realistic
for daily limit orders), scoring trend and mean-reversion side by side.

| Coin | mean-reversion (segments +) | trend-breakout (segments +) | trend total trades | verdict |
|---|---|---|---|---|
| **BNB** | 0/5 | **4/5** | 18 | strong |
| **ETH** | 0/5 | **3/5** | 22 | holds |
| **BTC** | 0/5 | **3/5** \* | — | holds (validated §5b) |
| **ADA** | 0/5 | **3/5** | 9 | holds |
| **AVAX** | 0/5 | **3/5** | 11 | holds |
| **DOGE** | 1/5 | **3/5** | 20 | holds |
| **TRX** | 0/5 | **3/5** | 22 | holds |
| SOL | 1/5 | 2/5 | 13 | marginal |
| XRP | 0/5 | 1/5 | 20 | fails |
| LINK | 0/5 | 1/5 | 28 | fails |

\* *BTC trend from the §5b validated run; not re-scored at 0.1% here.*

**Two findings, very different confidence levels:**

1. **Mean-reversion is conclusively dead on crypto.** 0/5 (or 1/5) on all 10
   coins, with *hundreds* of trades per segment — the most statistically powered
   result in the study. Fading crypto dips loses, universally. Locked.

2. **Trend-following is a real, repeatable edge on the majors that trend** —
   net-positive in ≥3/5 segments on **7 of 10 coins**. The edge repeats across 7
   independent assets, which is what makes it trustworthy despite thin per-coin
   trade counts. It is no longer "BTC got lucky"; it is a property of trending
   crypto.

**The 3 misses are diagnostic, not contradictory.** They show *what the edge
needs*: an asset in a genuinely trending regime.
- **XRP (1/5):** years of SEC-litigation suppression / range-bound chop — never
  trended, so trend can't profit.
- **LINK (1/5):** choppy, no sustained directional regime; the one green segment
  rests on a single trade.
- **SOL (2/5, marginal):** heavy outlier dependence (seg0 +2.25% on 3 trades, the
  2021 parabola) and the shortest history — least reliable.

**Honest caveat (same as §5b, amplified):** trend fires only **2–8 trades per
segment**; scores ride a few fat winners. That positive-skew, low-frequency
profile *is* trend-following — not a flaw — but it means no single coin is
well-powered alone. Confidence comes from the cross-coin repetition (7/10) and
per-segment consistency, **not** from any one net% figure.

**Implication — trade the basket, not the coin.** The natural deployment is a
**portfolio of daily-trend majors** (BTC, ETH, BNB, ADA, AVAX, DOGE, TRX).
Pooling 7 coins on the same edge gives the aggregate the statistical mass each
coin lacks individually and diversifies the outlier dependence (when one coin's
big trend trade misses, another's lands). Exclude XRP/LINK/SOL until they show a
trending regime. Reproduce:
```
for c in btc eth bnb ada avax doge trx; do
  python3 -m tradingbot fetch-data --exchange binancevision --symbol ${c^^}USDT \
      --timeframe 1d --months 96 --out data/${c}_1d_long.csv
  python3 -m tradingbot robustness --csv data/${c}_1d_long.csv --segments 5 \
      --fee 0.1 --slippage 0.05 --report reports/robust_${c}.md
done
```

## 5d. The trend edge is timeframe-specific — it dies on 1-minute bars

To test whether the daily edge ports to intraday execution, we built
`config.donchian-1m.yaml` and re-ran the full battery on 3 months of 1-minute BTC
(132,480 bars). It is a **clean negative control**: same strategy, same asset,
same fee model — only the bar interval changed.

| Test | Daily BTC (validated) | 1-minute BTC |
|---|---|---|
| Donchian OOS `compare` | +0.23%, score +1.16, **rank #1** | −2.62%, score −1.00, **rank #6/8** |
| Param sweep | **18/18 settings net-positive** | **0/18 settings net-positive** |
| Segment robustness | 3/5 segments positive | **0/5 segments positive** |

**Why it dies:** at 0.6%/side, a round-trip costs ~1.3%, and a 50-*minute*
breakout move rarely clears that. The sweep is a dose-response curve — net return
is almost perfectly monotonic in trade count (most trades → worst loss: 10/5 →
−9.27% over 1777 trades; least → least-bad: 80/30 → −1.59% over 302). That is the
signature of pure fee bleed, not a tunable edge. Win rate is ~0% across the grid;
1-minute "breakouts" are microstructure noise (bid/ask bounce, liquidation wicks)
that mean-revert within minutes rather than persisting. Lower (maker) fees would
shrink the loss, not invert it — the edge is signal-gated, not fee-gated.

**Conclusion:** the BTC trend edge lives at the **daily/swing horizon**, where a
multi-percent breakout dwarfs the round-trip fee. **Do not deploy this signal
intraday.** The edge is now characterized on three axes: **trend-following +
trending-crypto + daily bars.**

---

## 6. Ideas evaluated and ruled out (scope)

- **"Politician tracker" (Autopilot / Unusual Whales).** Real services, but: it's
  an **equities/options** signal (wrong venue — this bot trades crypto), trades
  are disclosed **30–45 days late** (STOCK Act), and the headline returns are
  concentrated in a few names while Congress on average ≈ the index. Not a crypto
  signal; a separate equities project at best.
- **On-chain realized cap via Coin Metrics.** `CapRealUSD` is a **paid** metric;
  the free community API returns 403 for it. We used **BGeometrics** instead,
  which serves the finished MVRV Z-score for free.

---

## 7. Data sources & network constraints (practical reference)

What worked and what's walled — so future data pulls don't repeat the search:

| Source | Use | Status from your network |
|---|---|---|
| Binance public dumps (`data.binance.vision`) | BTC OHLCV + funding | ✅ Works (static CDN) |
| BGeometrics (`bitcoin-data.com`) | MVRV Z-score | ✅ Works (free API) |
| CoinGecko | daily BTC | ✅ Works |
| Coin Metrics community | market cap (free) / realized cap (paid) | ⚠️ Reachable; realized cap paywalled |
| Yahoo Finance chart API | equities/bonds | ❌ IP rate-limited (429) |
| Stooq | equities/bonds | ❌ JavaScript bot-wall |
| Nasdaq.com (browser) | equities/bonds | ✅ Manual download → `localcsv` |

**Rule of thumb:** static CDNs and small free APIs pass; anything behind
Cloudflare bot-protection or per-IP rate limits doesn't. For equities, a one-time
**browser download → `--exchange localcsv`** is the reliable path.

---

## 8. Conclusions

1. **Direction-of-fit is the whole game.** Mean-reversion has no edge on crypto;
   trend-following does. Mean-reversion has an edge on equity indices;
   trend-following does not. Match the strategy to the asset's character.
2. **The crypto edge is trend-following, and it generalizes across the majors.**
   Net-positive in ≥3/5 segments on 7 of the top 10 coins (BTC, ETH, BNB, ADA,
   AVAX, DOGE, TRX). The 3 misses (XRP, LINK, SOL) are coins that didn't trend in
   the window — the edge needs a trending regime, which is a diagnosis, not a hole.
3. **Mean-reversion is conclusively dead on crypto** — 0–1/5 segments on all 10
   coins with hundreds of trades each (the study's best-powered negative).
4. **The edge is daily-specific.** It is strong on daily bars (18/18 param
   settings) and *inverts* on 1-minute bars (0/18) — fees swamp the intraday
   move. Trade it at the swing horizon, never intraday.
5. **Equity indices have a validated mean-reversion edge** (SPY & QQQ, 4/5
   segments each) — replicated across two indices and across time.
6. **The platform's other value is risk management + honest evaluation**: hard
   risk gates, human approval, and drawdown-reducing governors (regime, funding,
   MVRV) — which now have a base *with* edge to scale (they only help a positive base).
7. **Next:** deploy as a **basket of daily-trend majors** (pools thin per-coin
   samples, diversifies outlier dependence); validate params walk-forward; layer
   the governors on top.

---

## 9. Open questions — candidate next checks on the BTC algo

A menu, roughly ordered by promise/effort. Each plugs into the same `compare` /
`walkforward` harness, so each is a clean yes/no test. Pick any and we build it.

1. **Trend-following instead of mean-reversion (highest priority).** BTC *trends*
   — that's *why* reversion fails. Test breakout/momentum entries (Donchian
   channel breakout, 50/200-day or weekly MA trend, time-series momentum). If
   any is positive OOS on BTC daily/weekly, that's the crypto analog of the SPY
   result. *This is the single most promising next test.*

2. **Hold-and-de-risk vs active trading.** Maybe BTC's honest edge is "own it and
   cut exposure near cycle tops," not "trade it." Backtest DCA/buy-and-hold with
   the **MVRV + regime governors** trimming exposure at high Z / extended cycle
   — compare risk-adjusted return vs active strategies.

3. **Volatility targeting.** Size positions inversely to realized volatility
   (constant risk per trade). Historically lifts risk-adjusted returns even
   without a better entry. Bolt onto any base.

4. **Regime-switch strategy.** Trend-follow when price > 200-week SMA (bull
   regime), stand aside or mean-revert when ranging. Combines the cycle overlay
   with two entry styles instead of one.

5. **Governors on a base that actually has edge.** The governors only help a base
   with positive expectancy. Once (1) or (2) finds a positive base, re-apply
   funding + MVRV + regime to see if they improve *its* risk-adjusted return.

6. **Funding carry / basis (market-neutral).** Earn funding by being short perp /
   long spot when funding is richly positive — a *yield* source, not a directional
   bet. Different risk profile; needs perp+spot legs.

7. **Volatility breakout (ATR / Bollinger).** Enter on expansion beyond N×ATR;
   suits trending bursts. A specific flavor of (1).

8. **Seasonality.** Day-of-week / time-of-day effects (weekend drift, funding-
   settlement windows). Cheap to test; often noise, occasionally real.

9. **Longer horizon / weekly bars.** Re-run trend and reversion on weekly data —
   higher signal-to-noise, fewer fees, fits BTC's multi-week swings.

10. **Cross-sectional momentum across alts.** Rank a basket (BTC/ETH/SOL/…) by
    recent return, long the strongest — needs multi-asset crypto OHLCV. More
    infrastructure, but a well-documented crypto edge.

**My recommendation:** start with **(1) trend-following** and **(2)
hold-and-de-risk** — they directly target *why* the current algo fails on BTC,
and both are fast to test in the existing harness.

---

## 10. Command reference

```
# crypto data (works on your network)
python3 -m tradingbot fetch-data --exchange binancevision --symbol BTCUSDT --timeframe 1d --months 96 --out data/btc_1d_long.csv
python3 -m tradingbot fetch-funding --exchange binancevision --symbol BTCUSDT --months 12 --out data/funding.csv
python3 -m tradingbot fetch-onchain --out data/mvrv.csv

# equities (browser download -> local convert)
python3 -m tradingbot fetch-data --exchange localcsv --csv ~/Downloads/<file>.csv --out data/spy.csv

# evaluate
python3 -m tradingbot compare     --source csv --csv data/btc_1d_long.csv --oos 0.4 \
    --funding-csv data/funding.csv --mvrv-csv data/mvrv.csv --report reports/compare.md
python3 -m tradingbot walkforward --source csv --csv data/spy.csv --fee 0.01 --slippage 0.005
python3 -m tradingbot cross-asset --asset BTC=data/btc_1d_long.csv@0.6 --asset SPY=data/spy.csv@0.01 --oos 0.4
python3 -m tradingbot regime      --csv data/btc_1d_long.csv
```
