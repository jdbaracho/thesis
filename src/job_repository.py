"""Thread-safe registry of :class:`Job` instances.

This module owns the :class:`JobRepository` class plus the filesystem layout
under :data:`JOB_ROOT` (``<src>/output/api_jobs``), where each job gets its
own subdirectory named after its ``uuid4`` hex id.

The repository serves two concerns:

* An in-memory ``job_id`` → :class:`Job` mapping guarded by a
  :class:`threading.Lock`, exposing :meth:`~JobRepository.create`,
  :meth:`~JobRepository.get`, :meth:`~JobRepository.get_all`,
  :meth:`~JobRepository.update`, :meth:`~JobRepository.delete`, and
  :meth:`~JobRepository.delete_all`.
* Ownership of each job's workdir on disk — created eagerly by
  :meth:`~JobRepository.create` and recursively removed by
  :meth:`~JobRepository.delete` / :meth:`~JobRepository.delete_all`.

Job state itself is kept in memory only; restarting the process forgets
every tracked job (their workdirs remain on disk until cleaned up
externally).

The process-wide :data:`job_repository` singleton is the entry point used
by the API layer and the redaction worker.
"""

from __future__ import annotations

import logging
import shutil
import threading
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from src.domain.job import Job


logger = logging.getLogger(__name__)


#: Root directory that holds one subdirectory per job.
JOB_ROOT: Path = Path(__file__).resolve().parent / "output" / "api_jobs"


__all__ = [
    "JOB_ROOT",
    "JobRepository",
    "job_repository",
]


class JobRepository:
    """Thread-safe registry mapping ``job_id`` → :class:`Job`.

    The repository owns filesystem layout for jobs: every :meth:`create`
    allocates a fresh directory under :data:`JOB_ROOT`, and :meth:`delete`
    removes it. Job state itself is kept in memory only.
    """

    def __init__(self, root: Path = JOB_ROOT) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()

    # -- lifecycle ---------------------------------------------------------- #

    def create(self, file_count: int = 0) -> Job:
        """Register a new pending job and prepare its workdir."""
        job_id = uuid.uuid4().hex
        workdir = self._root / job_id
        workdir.mkdir(parents=True, exist_ok=False)
        job = Job(id=job_id, workdir=workdir, file_count=file_count)
        with self._lock:
            self._jobs[job_id] = job
        logger.info("Job %s created (files=%d)", job_id, file_count)
        return job

    def get(self, job_id: str) -> Optional[Job]:
        """Return the job or ``None`` if unknown."""
        with self._lock:
            return self._jobs.get(job_id)

    def get_all(self) -> List[Job]:
        """Snapshot of currently tracked jobs."""
        with self._lock:
            return list(self._jobs.values())

    def update(self, job_id: str, **fields: object) -> Optional[Job]:
        """Atomically mutate fields on ``job_id``.

        Silently returns ``None`` for unknown jobs so callers can ignore
        races with :meth:`delete`.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            for key, value in fields.items():
                if not hasattr(job, key):
                    raise AttributeError(
                        f"Job has no attribute {key!r}"
                    )
                setattr(job, key, value)
            return job

    def delete(self, job_id: str) -> bool:
        """Drop the job entry and remove its workdir. Returns ``True`` if removed."""
        with self._lock:
            job = self._jobs.pop(job_id, None)
        if job is None:
            return False
        shutil.rmtree(job.workdir, ignore_errors=True)
        logger.info("Job %s deleted", job_id)
        return True

    def delete_all(self) -> int:
        """Drop every tracked job and remove its workdir. Returns the count removed."""
        with self._lock:
            jobs = list(self._jobs.values())
            self._jobs.clear()
        for job in jobs:
            shutil.rmtree(job.workdir, ignore_errors=True)
        if jobs:
            logger.info("Deleted %d job(s)", len(jobs))
        return len(jobs)


#: Process-wide singleton used by the API layer.
job_repository = JobRepository()
