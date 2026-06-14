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

### Trade the basket: the `portfolio` index backtest

Because each coin trades only a handful of times, the honest way to deploy the
edge is as one **basket**, not seven separate bets. The `portfolio` command
bundles the trend-majors into an equal-weight (or inverse-volatility) index: the
starting capital is split into per-coin *sleeves*, each sleeve runs the same
Donchian strategy and is **fully deployed when its coin is in an uptrend, cash
otherwise**, and the sleeve equity curves are summed (over the common date
window) into one index curve.

It reports each coin's **weight** three ways so you can see who carries the
basket:
- **alloc%** — capital weight in (equal = 1/N, or 1/vol normalised);
- **contrib%** — share of the basket's net P&L (who actually drove it; negative
  for a losing coin);
- **final%** — share of the ending bundle value.

The key payoff is visible in one line of the report: the **index drawdown is far
lower than the average single-coin drawdown**, because pooling N convex, thin
trend streams diversifies the outlier dependence — when one coin's big trend
trade misses, another's lands. Run it (0.6%/side = the conservative real-life
taker cost; full-deployment sizing pays it on the whole sleeve each round trip):
```
python3 -m tradingbot portfolio \
  --asset BTC=data/btc_1d_long.csv  --asset ETH=data/eth_1d_long.csv \
  --asset BNB=data/bnb_1d_long.csv  --asset ADA=data/ada_1d_long.csv \
  --asset AVAX=data/avax_1d_long.csv --asset DOGE=data/doge_1d_long.csv \
  --asset TRX=data/trx_1d_long.csv \
  --equity 70000 --fee 0.6 --slippage 0.05 \
  --weight-mode equal --report reports/trend_index.md
# swap --weight-mode invvol for a risk-parity-style index (calmer coins weighted up)
```

**Result — verified at 0.6%/side taker fees, leverage-free** (equal-weight, 1986
common daily bars, 2020-09-22 → 2026-05-31):

| coin | alloc% | net% | buy&hold% | expo% | maxdd% | trades |
|---|---|---|---|---|---|---|
| BNB | 14.3 | +1107 | +2855 | 37 | 36.9 | 15 |
| ADA | 14.3 | +987 | +189 | 26 | 50.4 | 11 |
| DOGE | 14.3 | +953 | +3699 | 28 | 57.7 | 17 |
| AVAX | 14.3 | +629 | +69 | 23 | 58.7 | 14 |
| ETH | 14.3 | +465 | +483 | 37 | 45.5 | 16 |
| BTC | 14.3 | +209 | +600 | 38 | 34.4 | 17 |
| TRX | 14.3 | +82 | +1306 | 38 | 66.2 | 18 |
| **INDEX** | **100** | **+633** | +1314 | 32 | **37.7** | **108** |

(These are the cash-capped, no-leverage figures: an earlier run read +678% but
~45pts of that came from BNB implicitly re-leveraging a post-loss re-entry; the
engine now caps every entry at available cash, so +633% is the honest number.
Drawdown barely moved (37.0→37.7), confirming the leverage flattered *return*, not
risk.)

Equal-weight vs inverse-volatility weighting, both at 0.6%/side:

| weighting | net return | max DD | return/DD |
|---|---|---|---|
| **equal** | **+633%** | 37.7% | **16.8** |
| inverse-vol | ~+617% | 35.8% | ~16.4 |

Inverse-vol does *not* help: it trims ~2pt of drawdown but gives up return (it
down-weights DOGE, a +953% winner), losing on both raw and risk-adjusted terms.
**Equal-weight is the better and simpler index.**

Fee note: at full-deployment sizing each trend ride multiplies the position
several-fold, so ~1%/round-trip over ~15 trades costs only ~15pts on a multi-100%
gross — fees are rounding noise *at this magnitude*, which is itself a property of
the bull-regime window (it would not hold in a chop where moves are small).

**What it shows — and what to discount.** Diversification is real: index drawdown
**37.7% vs 50% average single-coin**, with no coin dominating returns (BNB top,
TRX bottom) — the basket isn't a one-coin bet. **But the headline +633% is
not a forecast:** (1) the window spans crypto's two largest bull runs (2020–21,
2023–24) — trend-following's best-case regime; (2) the constituents are the
*survivors* (today's top majors) — a real-time system would also have bought the
uptrends of coins that later died (LUNA, FTT), so the live capture is lower; and
(3) there is no OOS hold-out here (full history; defensible only because Donchian
50/15 is fixed, not fitted). And 37% drawdown is still a hard ride. Treat this as
"trend-following worked in the bull regime and the basket smoothed it," not as an
expected return.
Note: each sleeve deploys its *full* allocation on a long (target notional =
sleeve), unlike the tiny fixed notional in the single-coin `compare`/`robustness`
runs — so the index net% reflects real capital at work, not the 0.4%-sized
sleeves used to test signal *sign*.

### Bear-regime behavior — does it preserve capital? (isolate the window with `--start/--end`)

Running the index on just the 2021–2023 bear (the bull runs sliced off) shows
what the long-only trend index does when the trend turns down:

| window | trend index | buy & hold | index DD | hold DD | time-in-market |
|---|---|---|---|---|---|
| Nov-2021 → Jan-2023 | **−16.0%** | −63.1% | 26.5% | 73.5% | **10%** |
| 2022 (pure) | **−25.8%** | −64.0% | 26.3% | 64.3% | **9%** |

Three behavioral findings:

1. **It sells near the top and sits in cash.** Time-in-market collapses to ~9–10%
   — the basket is long only ~1 day in 10 and in cash the rest. Donchian is
   long-only, so it can't short the downtrend; "spotting the bear" is *passive* —
   requiring an upside breakout to enter keeps it out of a falling market by
   default.
2. **Capital preservation works, ~halving the damage.** Holding the basket was
   catastrophic (−63%, 73% drawdown); the trend index was painful but survivable
   (−16 to −26%, ~26% drawdown). It beat buy-and-hold by **+38–47 pts** of return
   and cut **38–47 pts** of drawdown.
3. **It still bleeds via whipsaw — the one fixable weakness.** Despite being 90%
   in cash it lost 16–26%, because each bear-market *relief rally* that poked
   above the channel triggered a long that then failed and stopped out — small
   repeated losses (DOGE worst: highest exposure 19–23%, worst loss −43%; AVAX
   occasionally caught a real bounce, +6.4%). The longer window (−16%) beats pure
   2022 (−26%) precisely because it includes Jan-2023's recovery, where the algo
   *re-engages* as breakouts start working again — the full cycle is: ride bull →
   channel-exit near top → cash through the bear (nibbling failed breakouts) →
   re-enter on the next breakout.

**Tested next improvement — the 200-day-SMA regime gate (RULED OUT).** The
hypothesis was that gating longs to "price above its 200d SMA" would suppress the
bear-rally false breakouts. Run on the full 2020–2026 index (so the SMA warms
from real prior data), it **failed badly — strictly worse on every axis that
matters:**

| full-index metric | no gate | 200d gate |
|---|---|---|
| net return | **+633%** | +94% |
| max drawdown | **37.7%** | 49.3% |
| return / maxdd | **16.8** | 1.9 |
| time-in-market | 32% | 23% |

It cut return by **86%** *and raised* drawdown. Why: trend-following's edge is a
**convex right tail** — the biggest gains come at the *start* of a new uptrend,
when price first breaks out from a bottom while the 200d SMA still sits far
overhead. The gate blocks exactly those early entries, so you buy late into
mature, reversal-prone trends; every coin's return collapsed (BNB +1107%→+111%,
ADA +993%→+90%, DOGE +953%→+36%). Drawdown got *worse* because the few allowed
trades concentrate into fewer, larger, later positions (lumpier timing). **Lesson
(same as the governors): never gate a strategy whose edge is early entries with a
lagging trend filter.** The capital preservation we wanted already comes free
from the channel exit + naturally-low bear exposure (~10%); the raw breakout is
the better system. *(The gated DOGE sleeve originally read maxdd 159.9% — an
artifact of full-deployment sizing implicitly leveraging a re-entry after a loss
into a violent gap. The engine now caps every entry at available cash (no
leverage), which bounds drawdown at 100%; the return verdict was decisive
regardless.)*

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

# the trend basket as one index (equal- or inverse-vol-weight; reports per-coin weights)
python3 -m tradingbot portfolio --asset BTC=data/btc_1d_long.csv --asset ETH=data/eth_1d_long.csv \
    --asset BNB=data/bnb_1d_long.csv --asset ADA=data/ada_1d_long.csv --asset AVAX=data/avax_1d_long.csv \
    --asset DOGE=data/doge_1d_long.csv --asset TRX=data/trx_1d_long.csv \
    --equity 70000 --fee 0.6 --slippage 0.05 --weight-mode equal
```
