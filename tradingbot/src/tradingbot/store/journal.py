"""Atomic order, fill, cash, cost and protection records.

A fill identity belongs to one immutable execution. An acknowledgement is not a fill.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import sqlite3

from ..app.portfolio import Position, PositionTracker
from ..domain import Side
from ..risk.state import trading_day


def number(value, *, positive=False):
    try:
        if isinstance(value, bool):
            raise ValueError("Boolean is not a monetary value")
        result = Decimal(str(value))
    except (InvalidOperation, TypeError) as exc:
        raise ValueError("Invalid numeric value") from exc
    if not result.is_finite() or (result <= 0 if positive else result < 0):
        raise ValueError("Invalid numeric value")
    return result


def text(value):
    return format(number(value).normalize(), "f")


def identity(payload):
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


class Journal:
    def __init__(self, path, initial_cash, quote="USDT", reset_hour=0, clock=None):
        self.path = str(Path(path).resolve())
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.quote = quote.upper()
        self.reset_hour = reset_hour
        initial = number(initial_cash)
        with self.connection() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS account_state (
              id INTEGER PRIMARY KEY CHECK(id=1), cash TEXT NOT NULL, quote TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS order_journal (
              id TEXT PRIMARY KEY, payload TEXT NOT NULL, state TEXT NOT NULL,
              exchange_id TEXT, reason TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS fill_journal (
              id TEXT PRIMARY KEY, order_id TEXT NOT NULL, payload TEXT NOT NULL,
              net TEXT NOT NULL, trade_id INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS position_journal (
              symbol TEXT PRIMARY KEY, units TEXT NOT NULL, cost TEXT NOT NULL,
              price TEXT NOT NULL, opened TEXT NOT NULL, stop TEXT, take_profit TEXT,
              protection TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS day_equity (
              day TEXT PRIMARY KEY, opening TEXT NOT NULL);
            """)
            if db.execute("SELECT 1 FROM account_state").fetchone() is None:
                if db.execute("SELECT 1 FROM trades LIMIT 1").fetchone():
                    raise RuntimeError("Existing trade history needs a verified journal migration")
            db.execute(
                "INSERT OR IGNORE INTO account_state VALUES(1,?,?)", (str(initial), self.quote)
            )
            if db.execute("SELECT quote FROM account_state").fetchone()[0] != self.quote:
                raise ValueError("Journal quote currency changed")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def transaction(self):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.execute("COMMIT")
            except BaseException:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise

    def prepare(self, order, stop=None, take_profit=None, reason=""):
        if not isinstance(order.symbol, str) or len(order.symbol.split("/")) != 2:
            raise ValueError("Invalid order symbol")
        base, quote = order.symbol.split("/")
        if not base or quote != self.quote:
            raise ValueError("Order is outside the account quote currency")
        number(order.amount, positive=True)
        number(order.reference_price, positive=True)
        if order.price is not None:
            number(order.price, positive=True)
        for value in [stop, take_profit]:
            if value is not None:
                number(value, positive=True)
        if order.side is Side.BUY and stop is None:
            raise ValueError("Entry needs saved protection before submission")
        if not order.client_order_id:
            raise ValueError("Order identity is required")
        payload = identity(
            dict(
                symbol=order.symbol,
                side=order.side.value,
                amount=text(order.amount),
                price=None if order.price is None else text(order.price),
                reference=text(order.reference_price),
                stop=None if stop is None else text(stop),
                take_profit=None if take_profit is None else text(take_profit),
                role=order.role.value,
            )
        )
        with self.transaction() as db:
            old = db.execute(
                "SELECT payload FROM order_journal WHERE id=?", (order.client_order_id,)
            ).fetchone()
            if old:
                if (
                    identity({k: v for k, v in json.loads(old[0]).items() if k != "created"})
                    != payload
                ):
                    raise ValueError("Conflicting order identity")
                return False
            if (
                order.side is Side.BUY
                and db.execute(
                    "SELECT 1 FROM order_journal WHERE state IN ('prepared','unknown','open','partial')"
                ).fetchone()
            ):
                raise RuntimeError("Pending account order blocks a new entry")
            # A pending outcome for this symbol forbids a new economic order.
            if db.execute(
                "SELECT 1 FROM order_journal WHERE json_extract(payload,'$.symbol')=? "
                "AND state IN ('prepared','unknown','open','partial')",
                (order.symbol,),
            ).fetchone():
                raise RuntimeError("An earlier order needs reconciliation")
            plan = json.loads(payload)
            plan["created"] = self.clock().astimezone(timezone.utc).isoformat()
            payload = identity(plan)
            db.execute(
                "INSERT INTO order_journal(id,payload,state,reason) VALUES(?,?,?,?)",
                (order.client_order_id, payload, "prepared", reason),
            )
            return True

    def order(self, key):
        with self.connection() as db:
            row = db.execute("SELECT * FROM order_journal WHERE id=?", (key,)).fetchone()
            return dict(row) if row else None

    def pending(self):
        with self.connection() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM order_journal WHERE state IN ('prepared','unknown','open','partial')"
                )
            ]

    def cash(self):
        with self.connection() as db:
            return number(db.execute("SELECT cash FROM account_state").fetchone()[0])

    def snapshot(self):
        tracker = PositionTracker()
        with self.connection() as db:
            db.execute("BEGIN")
            tracker.cash_quote = float(
                number(db.execute("SELECT cash FROM account_state").fetchone()[0])
            )
            for row in db.execute("SELECT * FROM position_journal"):
                units, cost = (
                    number(row["units"], positive=True),
                    number(row["cost"], positive=True),
                )
                tracker.positions[row["symbol"]] = Position(
                    row["symbol"],
                    float(units),
                    float(number(row["price"], positive=True)),
                    datetime.fromisoformat(row["opened"]),
                    take_profit=None
                    if row["take_profit"] is None
                    else float(number(row["take_profit"], positive=True)),
                    stop=None if row["stop"] is None else float(number(row["stop"], positive=True)),
                    cost_basis=float(cost),
                )
            db.execute("COMMIT")
        return tracker

    def filled_units(self, key):
        with self.connection() as db:
            return sum(
                (
                    number(json.loads(r[0])["units"])
                    for r in db.execute("SELECT payload FROM fill_journal WHERE order_id=?", (key,))
                ),
                Decimal(0),
            )

    def apply_execution(self, key, execution, db):
        """Apply an immutable execution in the caller's transaction."""
        order = db.execute("SELECT * FROM order_journal WHERE id=?", (key,)).fetchone()
        if order is None:
            raise ValueError("Execution has no prepared order")
        plan = json.loads(order["payload"])
        required = ["id", "units", "price", "fee", "fee_currency", "timestamp"]
        if any(k not in execution for k in required) or not execution["id"]:
            raise ValueError("Incomplete execution record")
        units = number(execution["units"], positive=True)
        price = number(execution["price"], positive=True)
        fee = number(execution["fee"])
        currency = execution["fee_currency"].upper()
        base, quote = plan["symbol"].split("/")
        if currency not in (base, quote) or quote != self.quote:
            raise ValueError("Fee currency needs an explicit conversion ledger")
        ts = datetime.fromisoformat(execution["timestamp"])
        if ts.tzinfo is None:
            raise ValueError("Execution timestamp needs a timezone")
        ts = ts.astimezone(timezone.utc)
        created = datetime.fromisoformat(plan["created"])
        if ts > self.clock().astimezone(timezone.utc) + timedelta(
            seconds=5
        ) or ts < created - timedelta(seconds=5):
            raise ValueError("Execution time is outside the order lifetime")
        event = dict(
            units=text(units),
            price=text(price),
            fee=text(fee),
            fee_currency=currency,
            timestamp=ts.isoformat(),
            symbol=plan["symbol"],
            side=plan["side"],
        )
        payload = identity(event)
        old = db.execute("SELECT * FROM fill_journal WHERE id=?", (execution["id"],)).fetchone()
        if old:
            if old["payload"] != payload or old["order_id"] != key:
                raise ValueError("Conflicting duplicate execution")
            return None
        total = sum(
            (
                number(json.loads(r[0])["units"])
                for r in db.execute("SELECT payload FROM fill_journal WHERE order_id=?", (key,))
            ),
            Decimal(0),
        )
        if total + units > number(plan["amount"]):
            raise ValueError("Execution exceeds requested units")
        cash = number(db.execute("SELECT cash FROM account_state").fetchone()[0])
        pos = db.execute(
            "SELECT * FROM position_journal WHERE symbol=?", (plan["symbol"],)
        ).fetchone()
        held = number(pos["units"]) if pos else Decimal(0)
        cost = number(pos["cost"]) if pos else Decimal(0)
        quote_fee = fee if currency == quote else Decimal(0)
        base_fee = fee if currency == base else Decimal(0)
        fee_quote = quote_fee + base_fee * price
        gross_cost = units * price
        net = Decimal(0)
        if plan["side"] == "buy":
            received = units - base_fee
            if received <= 0:
                raise ValueError("No net received units")
            cash -= gross_cost + quote_fee
            cost += gross_cost + quote_fee
            held += received
            stop = plan["stop"] if not pos else pos["stop"]
            if stop is None:
                raise ValueError("Entry needs saved protection")
            db.execute(
                "INSERT OR REPLACE INTO position_journal VALUES(?,?,?,?,?,?,?,?)",
                (
                    plan["symbol"],
                    str(held),
                    str(cost),
                    str(cost / held),
                    pos["opened"] if pos else ts.isoformat(),
                    str(stop),
                    plan["take_profit"],
                    "pending",
                ),
            )
        else:
            consumed = units + base_fee
            if consumed > held:
                raise ValueError("Execution exceeds tracked holdings")
            released = cost if consumed == held else cost * consumed / held
            proceeds = gross_cost - quote_fee
            cash += proceeds
            net = proceeds - released
            held -= consumed
            cost -= released
            if held:
                db.execute(
                    "UPDATE position_journal SET units=?,cost=?,price=?,protection=? WHERE symbol=?",
                    (str(held), str(cost), str(cost / held), "pending", plan["symbol"]),
                )
            else:
                db.execute("DELETE FROM position_journal WHERE symbol=?", (plan["symbol"],))
        if cash < 0:
            raise ValueError("Fill would create negative cash")
        db.execute("UPDATE account_state SET cash=? WHERE id=1", (str(cash),))
        # The reporting row, counter and inventory changes are in this transaction.
        cur = db.execute(
            """INSERT INTO trades
          (ts,symbol,side,price,amount,cost_quote,fee_quote,role,is_entry,realized_pnl,
           reason,valuation_pct,client_order_id,risk_pct,net_pnl)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                ts.astimezone(timezone.utc)
                .replace(tzinfo=None)
                .isoformat(" ", timespec="microseconds"),
                plan["symbol"],
                plan["side"],
                float(price),
                float(units),
                float(gross_cost),
                float(fee_quote),
                "taker",
                plan["side"] == "buy",
                float(net),
                order["reason"],
                None,
                key,
                None,
                float(net),
            ),
        )
        day = trading_day(ts, self.reset_hour).isoformat()
        db.execute("INSERT OR IGNORE INTO daily_counters VALUES(?,0,0,0)", (day,))
        if plan["side"] == "buy":
            # Count one accepted economic order; all partial notionals are included.
            first = total == 0
            db.execute(
                "UPDATE daily_counters SET trades=trades+?,traded_notional=traded_notional+? WHERE day=?",
                (int(first), float(gross_cost), day),
            )
        else:
            db.execute(
                "UPDATE daily_counters SET realized_pnl=realized_pnl+? WHERE day=?",
                (float(net), day),
            )
        db.execute(
            "INSERT INTO fill_journal VALUES(?,?,?,?,?)",
            (execution["id"], key, payload, str(net), cur.lastrowid),
        )
        return dict(
            trade_id=cur.lastrowid,
            net=float(net),
            realized=float(net),
            symbol=plan["symbol"],
            side=plan["side"],
            is_entry=plan["side"] == "buy",
            price=float(price),
            amount=float(units),
            fee_quote=float(fee_quote),
            ts=ts,
            entry_price=None,
        )

    def reconcile(self, key, status, executions=(), exchange_id=None, fault=None):
        if status not in ("unknown", "open", "partial", "filled", "canceled", "rejected"):
            raise ValueError("Invalid order state")
        applied = []
        with self.transaction() as db:
            previous = db.execute("SELECT * FROM order_journal WHERE id=?", (key,)).fetchone()
            if previous is None:
                raise ValueError("Unknown order")
            for execution in executions:
                result = self.apply_execution(key, execution, db)
                if result:
                    applied.append(result)
            plan = json.loads(previous["payload"])
            total = sum(
                (
                    number(json.loads(r[0])["units"])
                    for r in db.execute("SELECT payload FROM fill_journal WHERE order_id=?", (key,))
                ),
                Decimal(0),
            )
            target = number(plan["amount"])
            if total == target:
                status = "filled"
            if status == "filled" and total != target:
                if total == 0:
                    raise ValueError("Closed acknowledgement lacks all execution records")
                status = "partial"
            if total and total < target and status in ("open", "unknown"):
                status = "partial"
            if previous["state"] in ("filled", "canceled", "rejected") and status in (
                "open",
                "unknown",
                "partial",
            ):
                raise ValueError("Order state moved backwards")
            db.execute(
                "UPDATE order_journal SET state=?,exchange_id=COALESCE(?,exchange_id) WHERE id=?",
                (status, exchange_id, key),
            )
            if fault:
                fault()
        return applied

    def day_change(self, now, equity):
        equity = number(equity)
        day = trading_day(now, self.reset_hour).isoformat()
        with self.transaction() as db:
            db.execute("INSERT OR IGNORE INTO day_equity VALUES(?,?)", (day, str(equity)))
            opening = number(
                db.execute("SELECT opening FROM day_equity WHERE day=?", (day,)).fetchone()[0]
            )
        return float(equity - opening)
