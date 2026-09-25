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
        for attempt in range(8):  # Windows: a reader or antivirus may hold the target briefly
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == 7:
                    raise
                time.sleep(0.05 * 2 ** attempt)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
