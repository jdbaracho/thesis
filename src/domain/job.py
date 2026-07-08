"""Server-side record of a single redaction job."""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.domain.job_response import JobResponse
from src.domain.job_status import JobStatus


__all__ = ["Job"]


def _utcnow() -> datetime:
    """Return an aware UTC ``datetime`` (JSON-serialisable via ``isoformat``)."""
    return datetime.now(timezone.utc)


@dataclasses.dataclass
class Job:
    """Server-side record of a single redaction job.

    Attributes
    ----------
    id:
        Opaque UUID string; used in every job-scoped URL.
    workdir:
        Directory on disk that owns every artefact for this job (uploads,
        intermediate outputs, and ``result.zip``).
    status:
        Current :class:`JobStatus`.
    file_count:
        Number of input PDFs supplied by the client.
    created_at / started_at / finished_at:
        Timestamps recorded as the job moves through its lifecycle.
    error:
        Populated with a short human-readable message when ``status`` is
        :attr:`JobStatus.FAILED`.
    result_path:
        Absolute path to ``result.zip``. Set once the job reaches
        :attr:`JobStatus.COMPLETED`.
    """

    id: str
    workdir: Path
    status: JobStatus = JobStatus.PENDING
    file_count: int = 0
    created_at: datetime = dataclasses.field(default_factory=_utcnow)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error: Optional[str] = None
    result_path: Optional[Path] = None

    def to_response(self) -> JobResponse:
        """Serialise this job for JSON responses."""
        return JobResponse(
            id=self.id,
            status=self.status,
            file_count=self.file_count,
            created_at=self.created_at,
            started_at=self.started_at,
            finished_at=self.finished_at,
            error=self.error,
            result_url=(
                f"/jobs/{self.id}/result"
                if self.status is JobStatus.COMPLETED
                else None
            ),
        )
