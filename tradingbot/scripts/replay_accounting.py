"""Replay verified research fills through the production journal. No network."""

from __future__ import annotations
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import tempfile
from tradingbot.store import make_session_factory, TradeLog
from tradingbot.store.journal import Journal, number
from tradingbot.execution.models import Order, LiquidityRole
from tradingbot.domain import Side, OrderType
from tradingbot.reporting.weekly import WeeklyReporter


def replay(path):
    cases = json.loads(Path(path).read_text())
    results = []
    for name, case in cases.items():
        if not name.endswith(("_sleeves", "_pooled")):
            continue
        with tempfile.TemporaryDirectory(prefix="journal-history-") as temp:
            db = Path(temp) / "test.db"
            sf = make_session_factory(f"sqlite:///{db}")
            now = [datetime.now(timezone.utc)]
            j = Journal(db, 206, "USDT", clock=lambda: now[0])
            events = []
            for i, t in enumerate(case["trades"]):
                events.extend([(t["entry_t"], "buy", i, t), (t["exit_t"], "sell", i, t)])
            events.sort(key=lambda e: e[0])
            remaining = list(events)
            while remaining:
                time = remaining[0][0]
                group = [e for e in remaining if e[0] == time]
                remaining = [e for e in remaining if e[0] != time]
                now[0] = datetime.fromtimestamp(time / 1000, timezone.utc)
                while group:
                    advanced = False
                    for event in list(group):
                        _, side, index, t = event
                        units = number(t["units"])
                        price = number(t["entry"] if side == "buy" else t["exit"])
                        pos = j.snapshot().get(t["coin"] + "/USDT")
                        if side == "sell" and (pos is None or number(pos.units) < units):
                            continue
                        if side == "buy" and j.cash() < units * price * Decimal("1.001"):
                            continue
                        cid = f"{name}:{index}:{side}"
                        order = Order(
                            cid,
                            t["coin"] + "/USDT",
                            Side(side),
                            float(units),
                            OrderType.MARKET,
                            LiquidityRole.TAKER,
                            float(price),
                            float(price),
                            stop_price=float(price / 2) if side == "buy" else None,
                        )
                        # This is accounting parity. The saved stop is not a tested strategy change.
                        j.prepare(order, order.stop_price)
                        fill = dict(
                            id=cid + ":fill",
                            units=str(units),
                            price=str(price),
                            fee=str(units * price * Decimal(".001")),
                            fee_currency="USDT",
                            timestamp=now[0].isoformat(),
                        )
                        j.reconcile(cid, "filled", [fill])
                        j.reconcile(cid, "filled", [fill])
                        group.remove(event)
                        advanced = True
                    if not advanced:
                        raise AssertionError("No feasible chronological fill order")
            expected = number(case["equity"][-1])
            error = abs(j.cash() - expected)
            assert error < Decimal("1e-8"), (name, error)
            assert not j.snapshot().positions
            rows = TradeLog(sf).all()
            assert len(rows) == 2 * len(case["trades"])
            stats = WeeklyReporter(TradeLog(sf), net_accounting=True).compute(
                datetime(2010, 1, 1, tzinfo=timezone.utc), datetime(2100, 1, 1, tzinfo=timezone.utc)
            )
            assert abs(Decimal(str(stats.net_pnl)) - (j.cash() - 206)) < Decimal("1e-8")
            results.append(
                dict(
                    case=name,
                    trades=len(case["trades"]),
                    fills=len(rows),
                    final_cash=str(j.cash()),
                    expected_final_cash=str(expected),
                    absolute_error=str(error),
                    net_pnl=stats.net_pnl,
                )
            )
    return dict(
        checks=results,
        notes="Accounting parity only. No decision, liquidity or stop execution certification. Duplicate delivery was checked for each fill.",
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("input")
    p.add_argument("--out", required=True)
    a = p.parse_args()
    result = replay(a.input)
    Path(a.out).write_text(json.dumps(result, indent=2))
    print("Historical journal cases passed:", len(result["checks"]))
