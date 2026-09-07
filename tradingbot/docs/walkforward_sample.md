# Walk-forward report — SYNTHETIC (seed=11, 4000 bars) — NOT market data
- windows: 4 | metric: net_return_over_maxdd (net-of-fees return / max drawdown)
- fees: 0.6%/side, slippage: 0.05%/fill

`learned(OOS)` = params learned on the PREVIOUS window, scored on this unseen window. A learner that only wins in-sample but not OOS is overfitting.

```
### Learner: param_optimizer
win |  bars | baseline | learned(OOS) | in-sample | verdict
  0 |  1000 |   -0.268 |     n/a     |    +0.157 | 
  1 |  1000 |   -0.212 |    -0.451 |    +0.000 | worse/equal
  2 |  1000 |   -0.187 |    +0.000 |    +0.258 | better
  3 |  1000 |   -0.260 |    +0.226 |    +0.226 | better
OOS windows where learning beat baseline: 2/3 (mean OOS score -0.075)
```

```
### Learner: qlearning
win |  bars | baseline | learned(OOS) | in-sample | verdict
  0 |  1000 |   -0.268 |     n/a     |    +0.000 | 
  1 |  1000 |   -0.212 |    +0.000 |    +0.000 | better
  2 |  1000 |   -0.187 |    +0.000 |    +0.000 | better
  3 |  1000 |   -0.260 |    +0.000 |    +0.000 | better
OOS windows where learning beat baseline: 3/3 (mean OOS score +0.000)
```
