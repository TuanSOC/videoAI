"""Atomic file writes. The web UI reads JSON while the worker writes it, and the pipeline treats an
artifact's existence as "stage done" — so a file must never be observable half-written."""

import os
import tempfile
import time
from pathlib import Path


def write_atomic(path: Path, data: str | bytes) -> None:
    # unique temp name per call: the web thread and the job worker share one process
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data.encode("utf-8") if isinstance(data, str) else data)
        replace_with_retry(Path(tmp), path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def replace_with_retry(src: Path, dest: Path, attempts: int = 8) -> None:
    """os.replace; Windows antivirus or a reader briefly locks fresh files (WinError 32): wait it out."""
    for attempt in range(attempts):
        try:
            os.replace(src, dest)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.05 * 2 ** attempt)  # 0.05 s … 6.4 s total
