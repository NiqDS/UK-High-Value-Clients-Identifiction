"""Exercise the standalone guard without relying on real network failures."""
import runpy
import sys
from pathlib import Path

def test_guard_inherits_into_child_python(tmp_path):
    # The helper runs a fresh interpreter, preventing permanent hooks in pytest.
    import os
    import subprocess
    module = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'scripts/offline_test.py'))
    (tmp_path / 'sitecustomize.py').write_text(module['GUARD'])
    env = dict(os.environ, PYTHONPATH=str(tmp_path))
    probe = '''
import socket
from pathlib import Path
for action in [lambda: socket.create_connection(('127.0.0.1', 1)),
               lambda: socket.getaddrinfo('example.com', 443),
               lambda: Path('.env').read_text()]:
    try:
        action()
    except RuntimeError:
        continue
    raise SystemExit('guard did not reject operation')
print('guard verified')
'''
    result = subprocess.run([sys.executable, '-c', probe], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert 'guard verified' in result.stdout
