"""JSON view of a :class:`Job` returned by the API."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from src.domain.job_status import JobStatus


__all__ = ["JobResponse"]


class JobResponse(BaseModel):
    """JSON view of a :class:`Job` returned by the API."""

    id: str
    status: JobStatus
    file_count: int
    created_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error: Optional[str] = None
    result_url: Optional[str] = Field(
        default=None,
        description="Populated only when status == 'completed'.",
    )
