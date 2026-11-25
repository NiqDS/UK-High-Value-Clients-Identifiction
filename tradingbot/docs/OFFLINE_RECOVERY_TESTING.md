# Offline recovery tests

This recovery branch is based on `d21de702`, the existing Claude branch plus
the channel-distance helper. It is for isolated testing, not a live release.

## What changed

- Position and override files are scoped by database and paper/live mode.
- Legacy shared live state requires explicit reconciliation before migration.
- Paper adapters receive no exchange credentials, including for status queries.
- Missing live balances block entries without substituting simulated funds.
- Unavailable orders or uncertain cancellations block further submissions.
- Startup reconciliation validates all prices before mutating positions.
- Corrupt live position files stop startup; startup failures close the adapter.

## Test without exchange access

Use a clean checkout/archive in a separate directory. Do not copy `.env`,
production databases, positions, or runtime overrides. Do not run the trading
CLI, `run`, Compose, or either existing systemd service for this test.

Create a dedicated Python 3.12 virtual environment and install:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]' 'ccxt==4.5.75'
.venv/bin/python scripts/offline_test.py
```

Installation downloads packages. The test helper runs tests with temporary
state, scrubs exchange/Telegram/SMTP environment variables, refuses `.env`
access, verifies the source package location, and installs a Python IP-network
guard inherited by Python subprocesses. It is a test helper, not a sandbox for
untrusted arbitrary native code.

For operating-system network isolation on Linux, use the absolute paths:

```bash
sudo unshare --net -- /absolute/test/.venv/bin/python /absolute/test/scripts/offline_test.py
```

No Bybit key is needed. A nonzero exit means failure: retain the complete
summary/traceback. Do not restart production to investigate test failures.

## Limits before live rollout

The tests do not prove EU authentication, exchange fill behavior, or
profitability. The new state layout deliberately blocks unattended adoption
of the legacy shared position/override files. Reconcile holdings and orders
with the exchange before migrating live state. Reports and research-output
files still need further per-instance isolation. Full durable order/fill
reconciliation, accounting fixes and strategy evaluation remain separate work.

Do not apply this branch directly over the modified server checkout or copy
the read-only recovery key into a running trading service.
