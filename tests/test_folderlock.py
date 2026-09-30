"""One owner per video folder: an OS file lock, released by the OS when the holder exits — even when it is
killed or interrupted, so a folder can never stay "busy" forever."""

import json
import subprocess
import sys
import time

import pytest

from vidgen.folderlock import FolderBusyError, FolderLock, is_locked

HOLD = """
import sys, time
from pathlib import Path
from vidgen.folderlock import FolderLock
with FolderLock(Path(sys.argv[1]), "series"):
    print("locked", flush=True)
    time.sleep(60)
"""


def test_a_second_owner_is_refused_and_the_lock_frees_on_release(tmp_path):
    with FolderLock(tmp_path, "render"):
        assert is_locked(tmp_path)
        with pytest.raises(FolderBusyError):
            with FolderLock(tmp_path, "series"):
                pass
    assert not is_locked(tmp_path)
    with FolderLock(tmp_path, "series"):
        pass


def test_a_killed_holder_never_leaves_the_folder_busy(tmp_path):
    proc = subprocess.Popen([sys.executable, "-c", HOLD, str(tmp_path)], stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == "locked"
        assert is_locked(tmp_path)
        with pytest.raises(FolderBusyError, match="series"):   # says who holds it
            with FolderLock(tmp_path, "render"):
                pass
    finally:
        proc.kill()
        proc.wait()
    deadline = time.time() + 5
    while is_locked(tmp_path) and time.time() < deadline:
        time.sleep(0.05)
    assert not is_locked(tmp_path)


def test_a_stale_running_job_file_alone_is_not_busy(tmp_path):
    from vidgen.pipeline import ensure_not_busy
    (tmp_path / "job.json").write_text(json.dumps({"slug": "v", "kind": "series", "status": "running"}),
                                       encoding="utf-8")
    ensure_not_busy(tmp_path)                                  # a dead run's leftover must not block
    with FolderLock(tmp_path, "render"):
        with pytest.raises(FolderBusyError):
            ensure_not_busy(tmp_path)
