"""One background worker: script generation and renders are CPU/GPU heavy, so they run one at a time.

Each job's status is mirrored to output/<slug>/job.json so a server restart can tell the user a job was
interrupted (the queue itself is in-memory)."""

import json
import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from vidgen.fsutil import write_atomic

log = logging.getLogger(__name__)
JOB_FILE = "job.json"
ACTIVE = ("queued", "running")


@dataclass
class JobStatus:
    slug: str
    kind: str                      # brief | script | render
    status: str = "queued"         # queued | running | done | error | interrupted
    stages: dict[str, str] = field(default_factory=dict)  # stage → run | skip | done 1.2s | ...
    current: str = ""
    error: str = ""
    queued_at: float = field(default_factory=time.time)
    finished_at: float = 0.0
    options: dict = field(default_factory=dict)  # what resume needs to re-submit (e.g. then_render)
    out_dir: Path | None = field(default=None, repr=False, compare=False)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("out_dir")
        return d

    def save(self) -> None:
        if self.out_dir is not None and self.out_dir.exists():
            write_atomic(self.out_dir / JOB_FILE, json.dumps(self.to_dict(), ensure_ascii=False))


def load_job(out_dir: Path) -> JobStatus | None:
    p = out_dir / JOB_FILE
    if not p.exists():
        return None
    try:
        return JobStatus(**json.loads(p.read_text(encoding="utf-8")), out_dir=out_dir)
    except (ValueError, TypeError):
        return None


def mark_interrupted(out_dir: Path) -> bool:
    """On server start: a job recorded as queued/running can't still be running. Returns True if changed."""
    job = load_job(out_dir)
    if job is None or job.status not in ACTIVE:
        return False
    job.status, job.finished_at = "interrupted", time.time()
    job.save()
    return True


Work = Callable[[JobStatus], None]


class JobQueue:
    def __init__(self):
        self._q: queue.Queue[tuple[JobStatus, Work]] = queue.Queue()
        self._jobs: dict[str, JobStatus] = {}  # latest job per slug
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="vidgen-worker")
        self._thread.start()

    def submit(self, slug: str, kind: str, work: Work, out_dir: Path | None = None,
               options: dict | None = None) -> JobStatus:
        with self._lock:
            active = self._jobs.get(slug)
            if active and active.status in ACTIVE:
                raise RuntimeError(f"{slug} already has a {active.kind} job {active.status}")
            job = JobStatus(slug=slug, kind=kind, options=options or {}, out_dir=out_dir)
            self._jobs[slug] = job
        job.save()
        self._q.put((job, work))
        return job

    def get(self, slug: str) -> JobStatus | None:
        return self._jobs.get(slug)

    def busy(self, slug: str) -> bool:
        job = self._jobs.get(slug)
        return bool(job and job.status in ACTIVE)

    def wait_idle(self, timeout: float = 30) -> None:
        """Test helper: block until nothing is queued or running."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._q.unfinished_tasks == 0:
                return
            time.sleep(0.05)
        raise TimeoutError("jobs still running")

    def _loop(self) -> None:
        while True:
            job, work = self._q.get()
            job.status = "running"
            try:
                job.save()
                work(job)
                job.status = "done"
            except Exception as e:  # surface to the UI instead of killing the worker
                log.exception("job %s/%s failed", job.slug, job.kind)
                job.status, job.error = "error", str(e)
            finally:
                job.current = ""
                job.finished_at = time.time()
                try:
                    job.save()
                except OSError:  # e.g. the video folder was deleted meanwhile
                    log.warning("could not persist job %s", job.slug)
                self._q.task_done()
