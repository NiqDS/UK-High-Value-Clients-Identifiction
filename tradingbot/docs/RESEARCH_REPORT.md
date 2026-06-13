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

| Asset | Class | Mean-reversion OOS | Verdict |
|---|---|---|---|
| **SPY** (S&P 500 ETF) | equity index | **+0.10% net, 45% win, 62 trades, 0.05% maxdd, score +0.99** | **Positive edge** |
| BTC (daily) | crypto | −1.02% net, 16% win | No edge (loses) |
| TLT (20y Treasuries) | bonds | *pending — download CSV* | — |
| QQQ (Nasdaq-100) | equity index | *pending — download CSV* | — |

**Why it flips:** equity indices mean-revert (dips bought) with upward drift and
much lower volatility — "fade the move" works. BTC trends and whipsaws — the same
logic gets run over. The thesis was sound; the asset was wrong.

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

1. **The crypto algo has no entry edge** — confirmed across timeframes, signals,
   and real data. This is the *expected* result and the harness proved it cleanly.
2. **The platform's value is risk management + honest evaluation**, not (yet)
   alpha: hard risk gates, human approval, and three drawdown-reducing governors.
3. **The thesis is validated on the right asset.** Mean-reversion is positive OOS
   on SPY. The strategy was looking for its edge in the wrong market.
4. **Highest-leverage direction for *crypto* returns** is therefore NOT more
   mean-reversion overlays, but testing strategies that fit BTC's *trending,
   high-volatility* character (Section 9).

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
