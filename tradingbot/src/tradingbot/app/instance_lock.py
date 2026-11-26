"""One process per resolved database. Lock lifetime follows process lifetime."""

from pathlib import Path
import fcntl


class InstanceLock:
    def __init__(self, path):
        self.path = Path(path)
        self.file = None

    def acquire(self):
        if self.file is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            handle.close()
            raise RuntimeError("Another runner owns this account database") from None
        self.file = handle

    def release(self):
        if self.file is not None:
            fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
            self.file.close()
            self.file = None
