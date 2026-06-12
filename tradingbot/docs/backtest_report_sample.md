# Backtest report — sma_crossover

- data: SYNTHETIC random walk (seed=7) — NOT market data
- bars: 1500 (in-sample 1050, out-of-sample 450)
- fees: 0.6%/side, slippage: 0.05%/fill
- strategy: SMA 10/30, TP 1.5% / SL 1.0%

```
--- Backtest IN-SAMPLE ---
trades:            22
win rate:          36.4%
gross return:      -0.02%
NET return (fees): -0.12%
fees paid:         10.56
max drawdown:      0.12%
final equity:      9987.77 (from 10000.00)

--- Backtest OUT-OF-SAMPLE ---
trades:            5
win rate:          60.0%
gross return:      +0.01%
NET return (fees): -0.02%
fees paid:         2.41
max drawdown:      0.02%
final equity:      9998.39 (from 10000.00)
```

> A strategy that only works in-sample, or is profitable gross but not net of fees, should be treated as failed.
