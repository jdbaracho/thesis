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

Job state is persisted to a ``job.json`` sidecar inside each job's workdir on
every :meth:`~JobRepository.create` / :meth:`~JobRepository.update`, and the
in-memory map is rehydrated from disk on startup (:meth:`~JobRepository.load`).
Restarting the process therefore preserves every tracked job. Jobs that were
still ``pending``/``running`` when the process stopped are reloaded as
``failed`` (interrupted), since their worker no longer exists.

:data:`JOB_ROOT` defaults to ``<src>/output/api_jobs`` but can be overridden
with the ``PDF_REDACTOR_DATA_DIR`` environment variable so the data can live
outside the code checkout (e.g. a systemd ``StateDirectory``) and survive
redeploys.

The process-wide :data:`job_repository` singleton is the entry point used
by the API layer and the redaction worker.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from src.domain.job import Job
from src.domain.job_status import JobStatus


logger = logging.getLogger(__name__)


def _default_job_root() -> Path:
    """Resolve the job root, honouring ``PDF_REDACTOR_DATA_DIR`` when set."""
    data_dir = os.environ.get("PDF_REDACTOR_DATA_DIR")
    base = Path(data_dir) if data_dir else Path(__file__).resolve().parent / "output"
    return base / "api_jobs"


#: Name of the per-job metadata sidecar written inside each workdir.
JOB_META_FILENAME = "job.json"

#: Root directory that holds one subdirectory per job.
JOB_ROOT: Path = _default_job_root()


__all__ = [
    "JOB_ROOT",
    "JobRepository",
    "job_repository",
]


class JobRepository:
    """Thread-safe registry mapping ``job_id`` → :class:`Job`.

    The repository owns filesystem layout for jobs: every :meth:`create`
    allocates a fresh directory under :data:`JOB_ROOT`, and :meth:`delete`
    removes it. Job metadata is persisted to a ``job.json`` sidecar in each
    workdir and rehydrated on startup so state survives process restarts.
    """

    def __init__(self, root: Path = JOB_ROOT) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()
        self.load()

    # -- persistence -------------------------------------------------------- #

    def _persist(self, job: Job) -> None:
        """Atomically write ``job``'s metadata to its workdir sidecar."""
        meta_path = job.workdir / JOB_META_FILENAME
        try:
            job.workdir.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(
                dir=str(job.workdir), prefix=".job.", suffix=".tmp"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(job.to_dict(), fh, indent=2)
                os.replace(tmp_name, meta_path)
            except BaseException:
                # Best-effort cleanup of the temp file on any failure.
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise
        except OSError:
            logger.exception("Failed to persist job %s metadata", job.id)

    def load(self) -> int:
        """Rehydrate the in-memory map from ``job.json`` sidecars on disk.

        Called once at construction. Jobs left ``pending``/``running`` by a
        previous process are reloaded as ``failed`` (their worker is gone) and
        rewritten so the on-disk record matches. Returns the number loaded.
        """
        loaded: Dict[str, Job] = {}
        interrupted: List[Job] = []
        for meta_path in self._root.glob(f"*/{JOB_META_FILENAME}"):
            try:
                data = json.loads(meta_path.read_text(encoding="utf-8"))
                job = Job.from_dict(data)
            except (OSError, ValueError, KeyError):
                logger.exception("Skipping unreadable job metadata at %s", meta_path)
                continue
            if job.status in (JobStatus.PENDING, JobStatus.RUNNING):
                job.status = JobStatus.FAILED
                if not job.error:
                    job.error = "Interrupted by server restart"
                interrupted.append(job)
            loaded[job.id] = job
        with self._lock:
            self._jobs = loaded
        for job in interrupted:
            self._persist(job)
        if loaded:
            logger.info(
                "Loaded %d job(s) from %s (%d marked interrupted)",
                len(loaded), self._root, len(interrupted),
            )
        return len(loaded)

    # -- lifecycle ---------------------------------------------------------- #

    def create(self, file_count: int = 0, **fields: object) -> Job:
        """Register a new pending job and prepare its workdir.

        Extra ``**fields`` are forwarded to the :class:`Job` constructor so
        callers can seed per-job settings (``language``, ``mode``,
        ``model_id``) at creation time.
        """
        job_id = uuid.uuid4().hex
        workdir = self._root / job_id
        workdir.mkdir(parents=True, exist_ok=False)
        job = Job(id=job_id, workdir=workdir, file_count=file_count, **fields)
        with self._lock:
            self._jobs[job_id] = job
        self._persist(job)
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
        self._persist(job)
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
