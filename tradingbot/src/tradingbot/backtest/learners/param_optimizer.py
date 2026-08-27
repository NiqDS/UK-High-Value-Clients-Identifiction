"""Parameter optimizer: random search over the interpretable refinement params.

Transparent and hard to overfit (a small discrete space, few params). Returns a
strategy factory built from the best-scoring config on the training window; the
walk-forward then scores that factory on the *next, unseen* window.
"""

from __future__ import annotations

import random
from typing import Callable

from pydantic import ValidationError

from ...config import StrategyConfig
from ...strategy.base import Strategy
from ...strategy.sma import SmaCrossoverStrategy
from ..engine import Backtester, BacktestConfig
from ..metrics import Metric

# discrete search space over the refinement knobs
DEFAULT_SPACE: dict[str, list] = {
    "trade_cooldown_bars": [0, 15, 30, 60],
    "min_crossover_strength_pct": [0.0, 0.1, 0.25, 0.5],
    "buy_valuation_floor_pct": [0.0, -0.5, -1.0],
    "sizing_mode": ["fixed", "vwap_scaled"],
    "exit_mode": ["pct", "atr"],
    "take_profit_pct": [1.0, 1.5, 2.5],
    "stop_loss_pct": [0.8, 1.0, 1.5],
    "atr_tp_mult": [1.5, 2.0, 3.0],
    "atr_sl_mult": [1.0, 1.5, 2.0],
    "trailing_enabled": [False, True],
}


class ParamOptimizer:
    name = "param_optimizer"

    def __init__(
        self, bt_config: BacktestConfig, metric: Metric,
        n_samples: int = 40, seed: int = 0, space: dict[str, list] | None = None,
    ) -> None:
        self.bt_config = bt_config
        self.metric = metric
        self.n_samples = n_samples
        self.seed = seed
        self.space = space or DEFAULT_SPACE
        self.best_config: StrategyConfig | None = None
        self.best_score: float = float("-inf")

    def learn(self, candles, base_config: StrategyConfig) -> Callable[[], Strategy]:
        rng = random.Random(self.seed)
        base = base_config.model_dump()
        best_cfg, best_score = base_config, self._score(candles, base_config)
        for _ in range(self.n_samples):
            overrides = {k: rng.choice(v) for k, v in self.space.items()}
            try:
                cfg = StrategyConfig(**{**base, **overrides})
            except ValidationError:
                continue
            score = self._score(candles, cfg)
            if score > best_score:
                best_score, best_cfg = score, cfg
        self.best_config, self.best_score = best_cfg, best_score
        return lambda: SmaCrossoverStrategy(best_cfg)

    def _score(self, candles, cfg: StrategyConfig) -> float:
        res = Backtester(self.bt_config).run(candles, SmaCrossoverStrategy(cfg))
        return self.metric(res)
