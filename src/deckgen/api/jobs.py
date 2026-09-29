"""Фоновые задачи с прогрессом по этапам и журналом (один процесс, без Redis — достаточно для сервиса на одной GPU)."""
from __future__ import annotations

import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Job:
    id: str
    kind: str
    status: str = "queued"          # queued | running | done | error
    stage: str = ""
    progress: float = 0.0
    result: Any = None
    error: str | None = None
    log: list[str] = field(default_factory=list)
    started: float = field(default_factory=time.time)
    finished: float | None = None

    def say(self, msg: str) -> None:
        self.log.append(f"{time.time() - self.started:6.1f}s  {msg}")
        del self.log[:-200]

    def view(self) -> dict:
        return {"id": self.id, "kind": self.kind, "status": self.status, "stage": self.stage, "progress": round(self.progress, 3),
                "result": self.result, "error": self.error, "log": self.log[-60:],
                "elapsed": round((self.finished or time.time()) - self.started, 1)}


STORE_DEFAULT_PARALLEL = 2


class JobStore:
    def __init__(self, max_parallel: int = 2):
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(max_parallel)   # генерации на одной GPU — не больше двух одновременно

    def submit(self, kind: str, fn: Callable[[Job], Any]) -> Job:
        job = Job(id=uuid.uuid4().hex[:10], kind=kind)
        with self._lock:
            self._jobs[job.id] = job

        def run():
            with self._slots:
                job.status = "running"
                try:
                    job.result = fn(job)
                    job.status, job.progress = "done", 1.0
                except Exception as e:  # noqa: BLE001
                    job.status, job.error = "error", f"{e}"
                    job.say(traceback.format_exc(limit=4))
                finally:
                    job.finished = time.time()

        threading.Thread(target=run, daemon=True).start()
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)
