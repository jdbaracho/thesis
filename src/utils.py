"""Helper utilities for the FastAPI controller in :mod:`src.job_controller`."""

from __future__ import annotations

import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from fastapi import HTTPException, UploadFile, status


__all__ = [
    "utcnow",
    "safe_stem",
    "build_zip",
    "validate_pdf_upload",
    "spool_upload",
]


def utcnow() -> datetime:
    """Return an aware UTC ``datetime`` (JSON-serialisable via ``isoformat``)."""
    return datetime.now(timezone.utc)


def safe_stem(path: Path) -> str:
    """Return ``path.stem`` scrubbed of path separators."""
    return path.stem.replace("/", "_").replace("\\", "_") or "file"


def build_zip(artefacts: Iterable[Path], zip_path: Path) -> None:
    """Zip ``artefacts`` (flat, no directory structure) into ``zip_path``."""
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for artefact in artefacts:
            zf.write(artefact, arcname=artefact.name)


def validate_pdf_upload(upload: UploadFile) -> None:
    """Reject uploads that clearly aren't PDFs."""
    name = (upload.filename or "").lower()
    ctype = (upload.content_type or "").lower()
    if not name.endswith(".pdf") and ctype != "application/pdf":
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Not a PDF: {upload.filename!r}",
        )


async def spool_upload(upload: UploadFile, dest: Path) -> None:
    """Stream ``upload`` to ``dest`` in fixed-size chunks."""
    chunk_size = 1024 * 1024  # 1 MiB
    with dest.open("wb") as fh:
        while True:
            chunk = await upload.read(chunk_size)
            if not chunk:
                break
            fh.write(chunk)
    await upload.close()
