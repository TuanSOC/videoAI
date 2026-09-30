"""One owner per video folder: an OS file lock on <folder>/.vidgen.lock.

Whoever runs pipeline stages on a folder (a studio job, `vidgen resume`, `vidgen series`) holds it for the whole
run. The OS releases it when the holder exits — normally, on an exception, on Ctrl-C, or when killed — so a folder
can never stay "busy" after its owner is gone (job.json, the status the UI shows, could: seen live). Byte 0 is the
lock; the holder's pid/kind/start time are written after it so another process can say who holds it.
"""

import os
import sys
import time
from pathlib import Path

LOCK_FILE = ".vidgen.lock"


class FolderBusyError(RuntimeError):
    pass


if sys.platform == "win32":
    import msvcrt

    def _try_lock(fd: int) -> bool:
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _try_lock(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


def holder(out_dir: Path) -> str:
    """Who holds the lock, as its holder wrote it ("" if unknown)."""
    try:
        with open(out_dir / LOCK_FILE, "rb") as f:
            f.seek(1)
            return f.read(200).decode("utf-8", "replace").strip()
    except OSError:
        return ""


class FolderLock:
    """Context manager; raises FolderBusyError if another owner (another process or another job here) holds it."""

    def __init__(self, out_dir: Path, kind: str):
        self.out_dir, self.kind, self._fd = Path(out_dir), kind, None

    def __enter__(self) -> "FolderLock":
        self.out_dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.out_dir / LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o644)
        if not _try_lock(fd):
            os.close(fd)
            who = holder(self.out_dir)
            raise FolderBusyError(f"{self.out_dir.name} is being worked on{f' ({who})' if who else ''} — "
                                  "wait for it to finish, then try again")
        info = f" {self.kind} pid={os.getpid()} since={time.strftime('%H:%M:%S')}".encode()
        os.lseek(fd, 1, os.SEEK_SET)
        os.write(fd, info)
        os.ftruncate(fd, 1 + len(info))
        self._fd = fd
        return self

    def __exit__(self, *exc) -> None:
        if self._fd is not None:
            try:
                _unlock(self._fd)
            finally:
                os.close(self._fd)
                self._fd = None


def is_locked(out_dir: Path) -> bool:
    """True while some owner holds the folder (checked by trying the lock, then letting go)."""
    if not (Path(out_dir) / LOCK_FILE).exists():
        return False
    try:
        with FolderLock(out_dir, "probe"):
            return False
    except FolderBusyError:
        return True
