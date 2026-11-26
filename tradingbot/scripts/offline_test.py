"""Run this checkout's tests without credentials, .env access, or IP networking.

The guard is inherited by Python subprocesses. On Linux, additionally run this
inside `unshare --net` to enforce network isolation at the operating-system level.
No trading application entry point is started by this script.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]

GUARD = '''
import os
import socket
import sys

def denied(*args, **kwargs):
    raise RuntimeError("OFFLINE TEST: IP networking is disabled")

original_connect = socket.socket.connect
original_connect_ex = socket.socket.connect_ex
original_bind = socket.socket.bind
original_sendto = socket.socket.sendto

def guarded(operation):
    def call(sock, *args, **kwargs):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            return denied()
        return operation(sock, *args, **kwargs)
    return call

socket.socket.connect = guarded(original_connect)
socket.socket.connect_ex = guarded(original_connect_ex)
socket.socket.bind = guarded(original_bind)
socket.socket.sendto = guarded(original_sendto)
if hasattr(socket.socket, "sendmsg"):
    socket.socket.sendmsg = guarded(socket.socket.sendmsg)
socket.create_connection = denied
socket.getaddrinfo = denied

def audit(event, args):
    if event == "open":
        path = args[0]
        if isinstance(path, (str, bytes, os.PathLike)):
            if os.fsdecode(path).replace("\\\\", "/").rsplit("/", 1)[-1] == ".env":
                raise RuntimeError("OFFLINE TEST: .env reads/writes are disabled")
sys.addaudithook(audit)
os.environ["TRADINGBOT_OFFLINE_ACTIVE"] = "1"
'''


def main() -> int:
    # A misplaced invocation must never silently run the installed production copy.
    if not (ROOT / 'src/tradingbot').is_dir():
        raise SystemExit('Cannot locate source checkout')
    with tempfile.TemporaryDirectory(prefix='tradingbot-offline-') as scratch:
        work = Path(scratch)
        (work / 'sitecustomize.py').write_text(GUARD)
        env = dict(os.environ)
        prefixes = ('EXCHANGE_', 'TELEGRAM_', 'SMTP_', 'PYTEST_', 'TRADINGBOT_')
        for name in list(env):
            if name.upper().startswith(prefixes):
                del env[name]
        env['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        env['PYTHONPATH'] = os.pathsep.join([scratch, str(ROOT / 'src'), str(ROOT)])
        code = '''
import os
import sys
from pathlib import Path
import socket
assert os.environ.get("TRADINGBOT_OFFLINE_ACTIVE") == "1", "Network guard failed to load"
for probe in [lambda: socket.create_connection(("127.0.0.1", 1)),
              lambda: Path(".env").read_text()]:
    try:
        probe()
    except RuntimeError:
        pass
    else:
        raise SystemExit("Isolation self-check failed")
import tradingbot
source = Path(tradingbot.__file__).resolve()
expected = Path(sys.argv[1]) / "src/tradingbot"
assert source.is_relative_to(expected), "Wrong tradingbot checkout imported"
print("Offline isolation verified; testing:", expected, flush=True)
import pytest
raise SystemExit(pytest.main(["-q", "-p", "pytest_asyncio.plugin", "-p", "no:cacheprovider",
                             "--rootdir=" + sys.argv[1], sys.argv[1] + "/tests"]))
'''
        print('Tests use temporary state; credentials and .env are unavailable.', flush=True)
        return subprocess.run([sys.executable, '-c', code, str(ROOT)], cwd=work,
                              env=env, check=False).returncode


if __name__ == '__main__':
    raise SystemExit(main())
