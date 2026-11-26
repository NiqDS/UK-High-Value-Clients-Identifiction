# Isolated AWS test

This procedure tests a separate source copy. It does not start a bot service.
It does not use production credentials or databases.

Use the exact build commit from the handoff. Do not run the service installer.
Do not pull into the production directory for this test.

1. Download the source into a new temporary directory.
2. Verify the downloaded commit against the handoff.
3. Create a separate Python environment. Install the test dependencies there.
4. Run `scripts/offline_test.py` in a Linux network namespace.
5. Send the test count and any failure text to the reviewer. Do not send `.env`.

The handoff contains a complete command block with the commit fixed in it.
The expected local result is 446 passing tests.

The tests block Python network access and `.env` access. The Linux namespace
also blocks network access from native child programs. Dependency installation
needs network access before this isolation step.

Historical replay scripts need the audit data files. They are not required for
the server regression suite. The handoff records their input hashes and results.

A passing server test does not authorize live trading. EU private authentication,
USDC strategy tests, and native stop certification remain release gates.
