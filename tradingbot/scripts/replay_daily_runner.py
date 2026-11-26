"""Replay the actual paper runner against stored daily and hourly prices."""

from __future__ import annotations
import argparse
import asyncio
import bisect
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import yaml
from tradingbot.__main__ import _build_runner
from tradingbot.config import Config, Secrets, Settings
from tradingbot.backtest.data import load_csv
from tradingbot.exchange.models import Ticker

HOUR = 3600000
DAY = 86400000


def ms(value):
    return int(datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp() * 1000)


async def replay(data_root, start, end):
    root = Path(__file__).resolve().parents[1]
    cfg = Config(**yaml.safe_load((root / "config.bybit-daily-paper.yaml").read_text()))
    coins = [s.split("/")[0] for s in cfg.exchange.symbols_allowlist]
    daily = {c: load_csv(data_root / f"{c}_1d.csv") for c in coins}
    hourly = {c: {b.timestamp: b for b in load_csv(data_root / f"{c}_1h.csv")} for c in coins}
    timestamps = {c: [b.timestamp for b in daily[c]] for c in coins}
    cursor = [start]
    end = end + 23 * HOUR
    for c in coins:
        if set(range(start, end + HOUR, HOUR)) - hourly[c].keys():
            raise ValueError("Missing replay hours")
    with tempfile.TemporaryDirectory(prefix="daily-runner-replay-") as temp:
        cfg.app.db_url = f"sqlite:///{temp}/account.db"
        runner, _, _ = _build_runner(Settings(config=cfg, secrets=Secrets(_env_file=None)))
        runner.clock = lambda: datetime.fromtimestamp(cursor[0] / 1000, timezone.utc)
        runner.pipeline.health = None

        async def ticker(symbol):
            bar = hourly[symbol.split("/")[0]][cursor[0]]
            return Ticker(
                symbol, bar.open * 0.9995, bar.open * 1.0005, bar.open, bar.volume, 0, cursor[0]
            )

        async def candles(symbol, timeframe, limit):
            c = symbol.split("/")[0]
            index = bisect.bisect_right(timestamps[c], cursor[0])
            return daily[c][max(0, index - limit) : index]

        runner.adapter.fetch_ticker = ticker
        runner.adapter.fetch_ohlcv = candles
        runner.adapter.round_amount = lambda symbol, units: units
        runner.adapter.market_min_cost = lambda symbol: 5.0
        equity = []
        peak = cfg.app.paper_equity
        dd = 0
        steps = 0
        try:
            for t in range(start, end + HOUR, HOUR):
                cursor[0] = t
                runner._equity_cache = None
                for symbol in cfg.exchange.symbols_allowlist:
                    await runner.run_once(symbol, runner.clock())
                    steps += 1
                state = runner.journal.snapshot()
                value = state.cash_quote + sum(
                    p.units * hourly[s.split("/")[0]][t].close for s, p in state.positions.items()
                )
                peak = max(peak, value)
                dd = max(dd, (1 - value / peak) * 100)
                equity.append([t, value])
            records = runner.trade_log.all()
            closed = [t for t in records if not t.is_entry]
            return dict(
                start=start,
                end=end,
                runner_passes=steps,
                entries=sum(t.is_entry for t in records),
                exits=len(closed),
                cash=float(runner.journal.cash()),
                held=list(runner.journal.snapshot().positions),
                final_equity=equity[-1][1],
                net_return_pct=(equity[-1][1] / cfg.app.paper_equity - 1) * 100,
                max_hourly_close_drawdown_pct=dd,
                closed_net_pnl=sum(t.net_pnl for t in closed),
                fees_paid=sum(t.fee_quote for t in records),
                equity=equity,
                input_sha256={
                    str(p.name): hashlib.sha256(p.read_bytes()).hexdigest()
                    for c in coins
                    for p in [data_root / f"{c}_1d.csv", data_root / f"{c}_1h.csv"]
                },
                limits="Simulated fills at hourly bid/ask. No lot-size, order-book or private venue certification. Health state is assumed healthy. No reports or Telegram calls.",
            )
        finally:
            await runner.adapter.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("data_root", type=Path)
    p.add_argument("--start", default="2026-06-01")
    p.add_argument("--end", default="2026-11-24")
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    result = asyncio.run(replay(a.data_root, ms(a.start), ms(a.end)))
    a.out.write_text(json.dumps(result, indent=2))
    print(
        json.dumps(
            {k: v for k, v in result.items() if k not in ["equity", "input_sha256"]}, indent=2
        )
    )
