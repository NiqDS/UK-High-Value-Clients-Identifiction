"""Tolerant loader for trade-log files dropped in the bad-trades folder.

Our own bot writes losing trades here as JSONL; you also upload EXTERNAL bots'
logs, whose formats we don't control. So the loader is deliberately forgiving:
it accepts JSON, JSONL, or CSV, and maps a wide set of common field names onto a
canonical :class:`TradeSample`. Rows it cannot make sense of are skipped, not
fatal — one weird external file must never break the weekly loop.

A manifest (``.processed.json`` in the folder) records which files have been
assessed, so each run picks up only NEW drops unless a full re-scan is asked for.
"""

from __future__ import annotations

import csv
import io
import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# canonical field -> accepted aliases (lower-cased, checked in order)
_ALIASES: dict[str, tuple[str, ...]] = {
    "symbol": ("symbol", "pair", "ticker", "instrument", "market", "asset", "coin"),
    "side": ("side", "direction", "action", "type"),
    "entry_price": ("entry_price", "entry", "open", "open_price", "avg_entry", "price_in"),
    "exit_price": ("exit_price", "exit", "close", "close_price", "price_out"),
    "pnl": ("pnl", "realized_pnl", "profit", "net", "net_pnl", "result", "return", "p&l"),
    "risk_pct": ("risk_pct", "risk", "risk_percent", "risk%", "risk_pct_equity"),
    "reason": ("reason", "note", "tag", "comment", "exit_reason", "label"),
    "ts": ("ts", "timestamp", "time", "date", "datetime", "closed_at", "exit_time"),
    "source": ("source", "bot", "strategy", "origin", "account"),
}


@dataclass
class TradeSample:
    source: str                 # which file / bot it came from
    symbol: str = ""
    side: str = ""
    entry_price: float | None = None
    exit_price: float | None = None
    pnl: float | None = None
    risk_pct: float | None = None
    reason: str = ""
    ts: str = ""

    @property
    def is_loss(self) -> bool:
        if self.pnl is not None:
            return self.pnl < 0
        if self.entry_price and self.exit_price:  # infer from prices (assume long)
            return self.exit_price < self.entry_price
        return False


def _num(v) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace("$", "").replace("%", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _map_row(row: dict, source: str) -> TradeSample | None:
    low = {str(k).strip().lower(): v for k, v in row.items()}
    picked: dict[str, object] = {}
    for canon, aliases in _ALIASES.items():
        for a in aliases:
            if a in low and low[a] not in (None, ""):
                picked[canon] = low[a]
                break
    if not picked.get("symbol") and picked.get("pnl") is None and not picked.get("exit_price"):
        return None  # nothing usable
    # an explicit source/bot field in the row wins over the filename, so our own
    # logs tag as 'live' and external bots keep their own identity
    row_source = str(picked.get("source", "")).strip()
    return TradeSample(
        source=row_source or source,
        symbol=str(picked.get("symbol", "")).upper(),
        side=str(picked.get("side", "")).lower(),
        entry_price=_num(picked.get("entry_price")),
        exit_price=_num(picked.get("exit_price")),
        pnl=_num(picked.get("pnl")),
        risk_pct=_num(picked.get("risk_pct")),
        reason=str(picked.get("reason", "")),
        ts=str(picked.get("ts", "")),
    )


def parse_text(text: str, source: str) -> list[TradeSample]:
    """Parse one file's content (JSON / JSONL / CSV) into samples."""
    text = text.strip()
    if not text:
        return []
    out: list[TradeSample] = []
    # 1. JSON (array, or {"trades": [...]}, or a single object)
    try:
        obj = json.loads(text)
        rows = obj.get("trades", obj) if isinstance(obj, dict) else obj
        if isinstance(rows, dict):
            rows = [rows]
        if isinstance(rows, list):
            for r in rows:
                if isinstance(r, dict):
                    s = _map_row(r, source)
                    if s:
                        out.append(s)
            return out
    except (json.JSONDecodeError, AttributeError):
        pass
    # 2. JSONL (one object per line)
    jsonl: list[TradeSample] = []
    looked_like_jsonl = False
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
            looked_like_jsonl = True
            if isinstance(r, dict):
                s = _map_row(r, source)
                if s:
                    jsonl.append(s)
        except json.JSONDecodeError:
            looked_like_jsonl = False
            break
    if looked_like_jsonl:
        return jsonl
    # 3. CSV
    try:
        reader = csv.DictReader(io.StringIO(text))
        for r in reader:
            s = _map_row(r, source)
            if s:
                out.append(s)
    except (csv.Error, ValueError):
        logger.warning("could not parse %s as JSON/JSONL/CSV — skipping", source)
    return out


def load_file(path: Path) -> list[TradeSample]:
    try:
        return parse_text(path.read_text(errors="replace"), path.name)
    except Exception:
        logger.exception("failed to read %s — skipping", path)
        return []


def append_own_loss(folder: str | Path, *, symbol: str, side: str, entry_price: float,
                    exit_price: float, pnl: float, risk_pct: float | None,
                    reason: str, ts: str, bucket: str = "") -> None:
    """Append one of the bot's OWN losing trades to the folder as JSONL — a
    human-readable record of what went wrong (the loop still assesses own trades
    from the complete DB, since win-rates need winners too). ``bucket`` (e.g. an
    ISO week 'own_losses_2026W24') keeps each period a separate file. Tagged
    source='live'. Best-effort — never raises into the trading loop."""
    try:
        d = Path(folder)
        d.mkdir(parents=True, exist_ok=True)
        rec = {"source": "live", "symbol": symbol, "side": side,
               "entry_price": entry_price, "exit_price": exit_price, "pnl": pnl,
               "risk_pct": risk_pct, "reason": reason, "ts": ts}
        name = f"own_losses_{bucket}.jsonl" if bucket else "own_losses.jsonl"
        with (d / name).open("a") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:
        logger.exception("could not append own loss to %s", folder)


_MANIFEST = ".processed.json"
_DATA_SUFFIXES = {".json", ".jsonl", ".csv", ".log", ".txt", ".ndjson"}


def scan_folder(folder: str | Path, only_new: bool = True) -> tuple[list[TradeSample], list[str]]:
    """Load samples from every data file in ``folder``. With ``only_new`` (the
    weekly default) files already in the manifest are skipped. Returns
    (samples, filenames_processed_this_run)."""
    d = Path(folder)
    if not d.exists():
        return [], []
    manifest = d / _MANIFEST
    seen: set[str] = set()
    if only_new and manifest.exists():
        try:
            seen = set(json.loads(manifest.read_text()).get("processed", []))
        except Exception:
            seen = set()
    samples: list[TradeSample] = []
    processed_now: list[str] = []
    for f in sorted(d.iterdir()):
        if f.name == _MANIFEST or f.name.startswith(".") or f.suffix.lower() not in _DATA_SUFFIXES:
            continue
        if only_new and f.name in seen:
            continue
        rows = load_file(f)
        samples.extend(rows)
        processed_now.append(f.name)
        logger.info("learning: ingested %d sample(s) from %s", len(rows), f.name)
    if processed_now:
        try:
            manifest.write_text(json.dumps({"processed": sorted(seen | set(processed_now))}, indent=2))
        except Exception:
            logger.exception("could not update learning manifest")
    return samples, processed_now
