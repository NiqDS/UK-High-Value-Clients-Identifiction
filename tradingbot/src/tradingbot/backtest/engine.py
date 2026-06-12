"""Honest, zero-look-ahead backtester for the reference (long-only) strategy.

Guarantees no future leakage: at bar *i* the strategy sees only ``candles[:i+1]``
and any resulting order fills at bar *i+1*'s open. Take-profit / stop exits are
evaluated intra-bar from the bar's high/low, and when both are touched in the
same bar the **stop is assumed first** (worst case). Fees and slippage are
applied to every fill, so the report is net of costs — a strategy that is
profitable gross but not net of fees is reported as a loser.

Out-of-sample support splits the series chronologically and runs each segment
with a fresh strategy instance.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..domain import Side
from ..exchange.models import Candle, Ticker
from ..strategy.base import MarketData, Strategy


@dataclass
class BacktestConfig:
    initial_equity: float = 10_000.0
    fee_pct: float = 0.6  # per side (taker, conservative)
    slippage_pct: float = 0.05  # adverse, per fill
    oos_ratio: float = 0.30  # fraction reserved for out-of-sample


@dataclass
class Trade:
    entry_ts: int
    exit_ts: int
    entry_price: float
    exit_price: float
    units: float
    gross_pnl: float
    fees: float
    net_pnl: float
    reason: str


@dataclass
class BacktestResult:
    initial_equity: float
    final_equity: float
    gross_return_pct: float
    net_return_pct: float
    num_trades: int
    win_rate_pct: float
    max_drawdown_pct: float
    fees_paid: float
    trades: list[Trade] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)

    def summary(self, label: str = "") -> str:
        head = f"--- Backtest {label} ---".strip()
        return (
            f"{head}\n"
            f"trades:            {self.num_trades}\n"
            f"win rate:          {self.win_rate_pct:.1f}%\n"
            f"gross return:      {self.gross_return_pct:+.2f}%\n"
            f"NET return (fees): {self.net_return_pct:+.2f}%\n"
            f"fees paid:         {self.fees_paid:.2f}\n"
            f"max drawdown:      {self.max_drawdown_pct:.2f}%\n"
            f"final equity:      {self.final_equity:.2f} (from {self.initial_equity:.2f})"
        )


@dataclass
class _Position:
    entry_price: float
    units: float
    entry_ts: int
    take_profit: float | None
    stop: float | None
    entry_fee: float
    trail_distance: float | None = None


def _ticker_at(symbol: str, close: float) -> Ticker:
    return Ticker(symbol, bid=close, ask=close, last=close,
                  base_volume=0.0, quote_volume=0.0, timestamp=0)


class Backtester:
    def __init__(self, config: BacktestConfig | None = None) -> None:
        self.config = config or BacktestConfig()

    def run(self, candles: list[Candle], strategy: Strategy, symbol: str = "BTC/USD") -> BacktestResult:
        cfg = self.config
        slip = cfg.slippage_pct / 100.0
        fee_rate = cfg.fee_pct / 100.0

        cash = cfg.initial_equity
        pos: _Position | None = None
        pending: tuple[str, object] | None = None  # ('buy', intent) | ('sell', None)
        trades: list[Trade] = []
        equity_curve: list[float] = []
        gross_total = 0.0
        fees_total = 0.0

        def close(exit_price: float, ts: int, reason: str) -> None:
            nonlocal cash, pos, gross_total, fees_total
            assert pos is not None
            proceeds = pos.units * exit_price
            exit_fee = proceeds * fee_rate
            cash += proceeds - exit_fee
            entry_cost = pos.units * pos.entry_price
            gross = proceeds - entry_cost
            fees = pos.entry_fee + exit_fee
            trades.append(Trade(
                entry_ts=pos.entry_ts, exit_ts=ts, entry_price=pos.entry_price,
                exit_price=exit_price, units=pos.units, gross_pnl=gross,
                fees=fees, net_pnl=gross - fees, reason=reason,
            ))
            gross_total += gross
            fees_total += exit_fee
            pos = None

        for i, bar in enumerate(candles):
            # 1. execute the order decided on the previous bar, at this bar's open
            if pending is not None:
                action, intent = pending
                pending = None
                if action == "buy" and pos is None:
                    fill = bar.open * (1 + slip)  # adverse
                    units = intent.amount  # type: ignore[attr-defined]
                    entry_fee = units * fill * fee_rate
                    cash -= units * fill + entry_fee
                    fees_total += entry_fee
                    pos = _Position(
                        entry_price=fill, units=units, entry_ts=bar.timestamp,
                        take_profit=intent.take_profit_price,  # type: ignore[attr-defined]
                        stop=intent.stop_price,  # type: ignore[attr-defined]
                        entry_fee=entry_fee,
                        trail_distance=(intent.metadata or {}).get("trail_distance"),  # type: ignore[attr-defined]
                    )
                elif action == "sell" and pos is not None:
                    close(bar.open * (1 - slip), bar.timestamp, "signal exit")

            # 2. intra-bar stop/take-profit (stop first = worst case)
            if pos is not None:
                # ratchet a trailing stop up behind the bar high
                if pos.trail_distance is not None:
                    trailed = bar.high - pos.trail_distance
                    pos.stop = trailed if pos.stop is None else max(pos.stop, trailed)
                if pos.stop is not None and bar.low <= pos.stop:
                    close(pos.stop * (1 - slip), bar.timestamp, "stop")
                elif pos.take_profit is not None and bar.high >= pos.take_profit:
                    close(pos.take_profit * (1 - slip), bar.timestamp, "take-profit")

            # 3. mark equity at the close
            equity_curve.append(cash + (pos.units * bar.close if pos else 0.0))

            # 4. generate signals using ONLY data up to and including this bar
            md = MarketData(
                symbol=symbol, candles=candles[: i + 1],
                ticker=_ticker_at(symbol, bar.close), holding=pos is not None,
            )
            signals = strategy.generate_signals(md)
            if signals:
                s = signals[0]
                if s.side == Side.BUY and pos is None:
                    pending = ("buy", s)
                elif s.side == Side.SELL and pos is not None:
                    pending = ("sell", None)

        # liquidate any open position at the last close for a final mark
        if pos is not None and candles:
            close(candles[-1].close * (1 - slip), candles[-1].timestamp, "liquidation")
            equity_curve[-1] = cash

        final_equity = equity_curve[-1] if equity_curve else cfg.initial_equity
        net_total = sum(t.net_pnl for t in trades)
        wins = sum(1 for t in trades if t.net_pnl > 0)
        return BacktestResult(
            initial_equity=cfg.initial_equity,
            final_equity=final_equity,
            gross_return_pct=gross_total / cfg.initial_equity * 100.0,
            net_return_pct=net_total / cfg.initial_equity * 100.0,
            num_trades=len(trades),
            win_rate_pct=(wins / len(trades) * 100.0) if trades else 0.0,
            max_drawdown_pct=_max_drawdown_pct(equity_curve),
            fees_paid=fees_total,
            trades=trades,
            equity_curve=equity_curve,
        )

    def run_oos(
        self, candles: list[Candle], strategy_factory, symbol: str = "BTC/USD"
    ) -> tuple[BacktestResult, BacktestResult]:
        """Split chronologically into in-sample / out-of-sample and run both with
        fresh strategy instances. Returns ``(in_sample, out_of_sample)``."""
        split = int(len(candles) * (1 - self.config.oos_ratio))
        in_sample = candles[:split]
        out_sample = candles[split:]
        return (
            self.run(in_sample, strategy_factory(), symbol),
            self.run(out_sample, strategy_factory(), symbol),
        )


def _max_drawdown_pct(equity_curve: list[float]) -> float:
    peak = float("-inf")
    max_dd = 0.0
    for eq in equity_curve:
        peak = max(peak, eq)
        if peak > 0:
            dd = (peak - eq) / peak * 100.0
            max_dd = max(max_dd, dd)
    return max_dd
