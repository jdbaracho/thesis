"""Lifecycle states of a redaction job."""

from __future__ import annotations

import enum


__all__ = ["JobStatus"]


class JobStatus(str, enum.Enum):
    """Lifecycle states of a redaction job."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
