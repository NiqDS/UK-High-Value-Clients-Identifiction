"""Durable submission protocol. No venue adapter is certified for live use."""

from __future__ import annotations
import hashlib
import json
from .models import ExecStatus, ExecutionResult


class PaperVenue:
    def __init__(self, fees, quote):
        self.fees, self.quote = fees, quote

    async def submit(self, order, record):
        return await self.lookup(record)

    async def lookup(self, record):
        plan = json.loads(record["payload"])
        price = plan["price"] or plan["reference"]
        rate = self.fees.taker_fee_pct if plan["role"] == "taker" else self.fees.maker_fee_pct
        from ..store.journal import number

        units, price = number(plan["amount"]), number(price)
        return dict(
            status="filled",
            exchange_id="paper:" + record["id"],
            executions=[
                dict(
                    id="paper:" + record["id"] + ":1",
                    units=str(units),
                    price=str(price),
                    fee=str(units * price * number(rate) / 100),
                    fee_currency=self.quote,
                    timestamp=plan["created"],
                )
            ],
        )


class JournalBroker:
    def __init__(self, journal, venue):
        self.journal, self.venue = journal, venue

    def exit_identity(self, symbol):
        pos = self.journal.snapshot().get(symbol)
        if pos is None:
            raise ValueError("No tracked position for exit")
        with self.journal.connection() as db:
            rows = list(
                db.execute(
                    "SELECT state,payload FROM order_journal WHERE "
                    "json_extract(payload,'$.symbol')=? AND "
                    "json_extract(payload,'$.side')='sell'",
                    (symbol,),
                )
            )
        sequence = sum(r["state"] in ("canceled", "rejected") for r in rows)
        payload = f"exit|{symbol}|{pos.entry_ts.isoformat()}|{sequence}"
        return "tb-x" + hashlib.sha256(payload.encode()).hexdigest()[:22]

    async def _recover(self, order, record):
        ack = await self.venue.lookup(record)
        if ack is None:
            self.journal.reconcile(record["id"], "unknown")
            raise RuntimeError("Order lookup is inconclusive; submission remains blocked")
        return self._apply(order, record["id"], ack)

    def _apply(self, order, key, ack):
        state = ack["status"]
        if state == "canceled" and ack.get("final") is not True:
            state = "unknown"
        receipts = self.journal.reconcile(
            key, state, ack.get("executions", ()), ack.get("exchange_id")
        )
        status = self.journal.order(key)["state"]
        states = {
            "filled": ExecStatus.FILLED,
            "partial": ExecStatus.PARTIAL,
            "rejected": ExecStatus.REJECTED,
            "canceled": ExecStatus.SKIPPED,
        }
        return ExecutionResult(
            states.get(status, ExecStatus.OPEN), order, accounted=True, receipts=receipts
        )

    async def place_order(self, order):
        record = self.journal.order(order.client_order_id)
        if record is not None:
            if record["state"] in ("filled", "canceled", "rejected"):
                return ExecutionResult(
                    ExecStatus.SKIPPED, order, accounted=True, reason="durable duplicate"
                )
            return await self._recover(order, record)
        self.journal.prepare(order, order.stop_price, order.take_profit_price, order.reason)
        record = self.journal.order(order.client_order_id)
        try:
            ack = await self.venue.submit(order, record)
        except BaseException:
            self.journal.reconcile(record["id"], "unknown")
            raise
        return self._apply(order, record["id"], ack)

    async def recover_all(self):
        for record in self.journal.pending():
            await self._recover(None, record)

    async def cancel_order(self, client_order_id, symbol):
        # Cancellation is not proof that no late fill exists.
        raise RuntimeError("Cancel requires venue reconciliation; no blind cancellation")
