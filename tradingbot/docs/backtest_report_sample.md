# Backtest report — sma_crossover

- data: SYNTHETIC random walk (seed=7) — NOT market data
- bars: 1500 (in-sample 1050, out-of-sample 450)
- fees: 0.6%/side, slippage: 0.05%/fill
- strategy: SMA 10/30, TP 1.5% / SL 1.0%
- valuation filter: VWAP(50) on, buy floor +0% / force-exit ceiling 3% vs VWAP

```
--- Backtest IN-SAMPLE ---
trades:            9
win rate:          44.4%
gross return:      +0.00%
NET return (fees): -0.04%
fees paid:         4.32
max drawdown:      0.04%
final equity:      9995.72 (from 10000.00)

--- Backtest OUT-OF-SAMPLE ---
trades:            1
win rate:          100.0%
gross return:      +0.01%
NET return (fees): +0.00%
fees paid:         0.48
max drawdown:      0.00%
final equity:      10000.08 (from 10000.00)
```

> A strategy that only works in-sample, or is profitable gross but not net of fees, should be treated as failed.
