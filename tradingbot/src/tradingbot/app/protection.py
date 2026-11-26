"""Protection acknowledgement protocol. No live venue adapter is certified."""

import hashlib
from ..store.journal import number


class ProtectionManager:
    def __init__(self, journal, venue):
        self.journal, self.venue = journal, venue

    async def ensure(self, symbol):
        pos = self.journal.snapshot().get(symbol)
        if pos is None:
            return
        if pos.stop is None:
            raise RuntimeError("Held position has no saved stop")
        key = (
            "tb-p"
            + hashlib.sha256(f"{symbol}|{pos.entry_ts.isoformat()}".encode()).hexdigest()[:22]
        )
        # The venue must reconcile/amend the same protective identity, not add stops blindly.
        ack = await self.venue.ensure_stop(key, symbol, pos.units, pos.stop)
        if (
            ack is None
            or ack.get("state") != "open"
            or not ack.get("id")
            or number(ack.get("units")) != number(pos.units)
            or number(ack.get("stop"), positive=True) != number(pos.stop, positive=True)
        ):
            raise RuntimeError("Standing protection is not confirmed")
        with self.journal.transaction() as db:
            row = db.execute(
                "SELECT units,stop FROM position_journal WHERE symbol=?", (symbol,)
            ).fetchone()
            if (
                row is None
                or number(row["units"]) != number(pos.units)
                or number(row["stop"]) != number(pos.stop)
            ):
                raise RuntimeError("Position changed during protection acknowledgement")
            db.execute(
                "UPDATE position_journal SET protection=? WHERE symbol=?",
                ("confirmed:" + str(ack["id"]), symbol),
            )
