"""One agent at a time per output folder.

Running `watch` and `run` on the same folders at the same moment would process
the same file twice. The lock is an OS file lock: it is released automatically
if the process crashes, so a stale lock file never blocks the next run.
"""

from __future__ import annotations

import os
from pathlib import Path


class AlreadyRunning(Exception):
    pass


class FolderLock:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._fh = None

    def acquire(self) -> "FolderLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            fh.close()
            raise AlreadyRunning(
                f"Another invoice-agent is already working on {self.path.parent}. Stop it first."
            ) from exc
        self._fh = fh
        return self

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        finally:
            self._fh.close()
            self._fh = None

    def __enter__(self) -> "FolderLock":
        return self.acquire()

    def __exit__(self, *exc) -> None:
        self.release()
