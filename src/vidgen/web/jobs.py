"""One background worker: script generation and renders are CPU/GPU heavy, so they run one at a time."""

import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field

log = logging.getLogger(__name__)


@dataclass
class JobStatus:
    slug: str
    kind: str                      # script | render
    status: str = "queued"         # queued | running | done | error
    stages: dict[str, str] = field(default_factory=dict)  # stage → run | skip | done 1.2s | ...
    current: str = ""
    error: str = ""
    queued_at: float = field(default_factory=time.time)
    finished_at: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


Work = Callable[[JobStatus], None]


class JobQueue:
    def __init__(self):
        self._q: queue.Queue[tuple[JobStatus, Work]] = queue.Queue()
        self._jobs: dict[str, JobStatus] = {}  # latest job per slug
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="vidgen-worker")
        self._thread.start()

    def submit(self, slug: str, kind: str, work: Work) -> JobStatus:
        with self._lock:
            active = self._jobs.get(slug)
            if active and active.status in ("queued", "running"):
                raise RuntimeError(f"{slug} already has a {active.kind} job {active.status}")
            job = JobStatus(slug=slug, kind=kind)
            self._jobs[slug] = job
        self._q.put((job, work))
        return job

    def get(self, slug: str) -> JobStatus | None:
        return self._jobs.get(slug)

    def busy(self, slug: str) -> bool:
        job = self._jobs.get(slug)
        return bool(job and job.status in ("queued", "running"))

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
                work(job)
                job.status = "done"
            except Exception as e:  # surface to the UI instead of killing the worker
                log.exception("job %s/%s failed", job.slug, job.kind)
                job.status, job.error = "error", str(e)
            finally:
                job.current = ""
                job.finished_at = time.time()
                self._q.task_done()
