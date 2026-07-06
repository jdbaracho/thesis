"""FastAPI service exposing :class:`PDFRedactor` as a job-based HTTP API.

Clients POST one or more PDFs to ``/jobs``, poll ``/jobs/{id}`` until the job
completes, and download a ZIP archive containing every redacted PDF and its
``.xlsx`` translation table from ``/jobs/{id}/result``.

Run locally with::

    python api.py

or::

    uvicorn api:app --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import dataclasses
import datetime as _dt
import enum
import functools
import io
import logging
import os
import shutil
import tempfile
import threading
import traceback
import uuid
import zipfile
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from contextlib import asynccontextmanager

from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field


logger = logging.getLogger(__name__)


#: Root directory that holds one subdirectory per job.
JOB_ROOT: Path = Path(__file__).resolve().parent / "output" / "api_jobs"


__all__ = ["app", "Job", "JobManager", "JobStatus", "JOB_ROOT"]


# --------------------------------------------------------------------------- #
# Job model + manager                                                         #
# --------------------------------------------------------------------------- #


class JobStatus(str, enum.Enum):
    """Lifecycle states of a redaction job."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


def _utcnow() -> _dt.datetime:
    """Return an aware UTC ``datetime`` (JSON-serialisable via ``isoformat``)."""
    return _dt.datetime.now(_dt.timezone.utc)


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
    created_at: _dt.datetime = dataclasses.field(default_factory=_utcnow)
    started_at: Optional[_dt.datetime] = None
    finished_at: Optional[_dt.datetime] = None
    error: Optional[str] = None
    result_path: Optional[Path] = None


class JobManager:
    """Thread-safe registry mapping ``job_id`` → :class:`Job`.

    The manager owns filesystem layout for jobs: every :meth:`create` allocates
    a fresh directory under :data:`JOB_ROOT`, and :meth:`delete` removes it.
    Job state itself is kept in memory only.
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

    # -- convenience -------------------------------------------------------- #

    def all(self) -> List[Job]:
        """Snapshot of currently tracked jobs."""
        with self._lock:
            return list(self._jobs.values())


#: Process-wide singleton used by the API layer (Phase 4).
job_manager = JobManager()


# --------------------------------------------------------------------------- #
# Redactor cache + worker                                                     #
# --------------------------------------------------------------------------- #

#: Number of concurrent redaction workers. Kept at 1 by default because
#: :class:`PDFRedactor` typically fronts a single Ollama/LangExtract process
#: and parallel calls will contend for it.
_WORKER_COUNT: int = max(1, int(os.environ.get("PDF_REDACTOR_API_WORKERS", "1")))

#: Executor that runs :func:`process_job` off the event loop.
_executor: concurrent.futures.ThreadPoolExecutor = (
    concurrent.futures.ThreadPoolExecutor(
        max_workers=_WORKER_COUNT,
        thread_name_prefix="pdf-redactor",
    )
)


@functools.lru_cache(maxsize=None)
def get_redactor(language: str, use_llm: bool):  # noqa: ANN201 - forward ref
    """Return a cached :class:`PDFRedactor` for ``(language, use_llm)``.

    Building the redactor loads the Presidio analyzer and, when ``use_llm`` is
    true, the LangExtract recognizer — both expensive. The cache keeps one
    instance per unique key for the process lifetime.

    Imports are lazy so that importing :mod:`api` (e.g. for tests) does not
    require the heavy Presidio / PyMuPDF stack.
    """
    from pdf_redactor import PDFRedactor  # local import: heavy deps

    logger.info(
        "Building PDFRedactor (language=%s, use_llm=%s)", language, use_llm
    )
    return PDFRedactor(use_llm=use_llm, language=language)


def _safe_stem(path: Path) -> str:
    """Return ``path.stem`` scrubbed of path separators."""
    return path.stem.replace("/", "_").replace("\\", "_") or "file"


def _redact_one(
    input_path: Path,
    output_dir: Path,
    language: str,
    use_llm: bool,
    output_stem: Optional[str] = None,
) -> List[Path]:
    """Redact a single PDF and return the artefact paths that should be zipped.

    On success returns ``[<stem>_redacted.pdf, <stem>_redacted.xlsx]``. On
    failure the exception is caught, logged, and reported as a small
    ``<stem>_error.txt`` file so one bad PDF cannot fail an otherwise valid
    batch.
    """
    import fitz  # local import: heavy dep

    from pdf import save_translation_table_xlsx  # reuse existing helper

    stem = output_stem or _safe_stem(input_path)
    pdf_out = output_dir / f"{stem}_redacted.pdf"
    xlsx_out = output_dir / f"{stem}_redacted.xlsx"

    try:
        redactor = get_redactor(language=language, use_llm=use_llm)
        doc = fitz.open(input_path)
        try:
            doc, translation_table = redactor.redact(doc)
            doc.save(pdf_out)
        finally:
            doc.close()
        save_translation_table_xlsx(translation_table, str(xlsx_out))
        return [pdf_out, xlsx_out]
    except Exception as exc:  # noqa: BLE001 - convert to error artefact
        logger.exception("Failed to redact %s", input_path)
        error_path = output_dir / f"{stem}_error.txt"
        error_path.write_text(
            f"Failed to redact {input_path.name}: {exc}\n\n"
            f"{traceback.format_exc()}",
            encoding="utf-8",
        )
        return [error_path]


def _build_result_zip(artefacts: Iterable[Path], zip_path: Path) -> None:
    """Zip ``artefacts`` (flat, no directory structure) into ``zip_path``."""
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for artefact in artefacts:
            zf.write(artefact, arcname=artefact.name)


def process_job(
    job_id: str,
    input_paths: List[Path],
    language: str,
    use_llm: bool,
) -> None:
    """Worker entry point: redact every input PDF and produce ``result.zip``.

    Updates the job's status in :data:`job_manager` as it progresses. Any
    unrecoverable error (e.g. inability to open the workdir) marks the job
    ``FAILED``; per-file errors are captured inside the ZIP instead.
    """
    job = job_manager.get(job_id)
    if job is None:
        logger.warning("process_job: unknown job %s", job_id)
        return

    job_manager.update(
        job_id, status=JobStatus.RUNNING, started_at=_utcnow()
    )

    try:
        redacted_dir = job.workdir / "redacted"
        redacted_dir.mkdir(parents=True, exist_ok=True)

        artefacts: List[Path] = []
        used_stems: Dict[str, int] = {}
        for input_path in input_paths:
            base = _safe_stem(input_path)
            count = used_stems.get(base, 0)
            used_stems[base] = count + 1
            stem = base if count == 0 else f"{base}_{count + 1}"
            artefacts.extend(
                _redact_one(
                    input_path,
                    redacted_dir,
                    language,
                    use_llm,
                    output_stem=stem,
                )
            )

        zip_path = job.workdir / "result.zip"
        _build_result_zip(artefacts, zip_path)

        job_manager.update(
            job_id,
            status=JobStatus.COMPLETED,
            finished_at=_utcnow(),
            result_path=zip_path,
        )
        logger.info("Job %s completed (%d artefacts)", job_id, len(artefacts))
    except Exception as exc:  # noqa: BLE001 - top-level worker guard
        logger.exception("Job %s failed", job_id)
        job_manager.update(
            job_id,
            status=JobStatus.FAILED,
            finished_at=_utcnow(),
            error=str(exc) or exc.__class__.__name__,
        )


def submit_job(
    job_id: str,
    input_paths: List[Path],
    language: str,
    use_llm: bool,
) -> concurrent.futures.Future:
    """Enqueue ``process_job`` on the module-level executor."""
    return _executor.submit(
        process_job, job_id, input_paths, language, use_llm
    )


# --------------------------------------------------------------------------- #
# FastAPI app + endpoints                                                     #
# --------------------------------------------------------------------------- #


class JobResponse(BaseModel):
    """JSON view of a :class:`Job` returned by the API."""

    id: str
    status: JobStatus
    file_count: int
    created_at: _dt.datetime
    started_at: Optional[_dt.datetime] = None
    finished_at: Optional[_dt.datetime] = None
    error: Optional[str] = None
    result_url: Optional[str] = Field(
        default=None,
        description="Populated only when status == 'completed'.",
    )


def _job_to_response(job: Job) -> JobResponse:
    """Serialise a :class:`Job` for JSON responses."""
    return JobResponse(
        id=job.id,
        status=job.status,
        file_count=job.file_count,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        error=job.error,
        result_url=(
            f"/jobs/{job.id}/result"
            if job.status is JobStatus.COMPLETED
            else None
        ),
    )


@asynccontextmanager
async def _lifespan(_app: "FastAPI"):
    """Startup/shutdown hooks: ensure job root exists, cleanly stop the pool."""
    logging.basicConfig(
        level=os.environ.get("PDF_REDACTOR_API_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    JOB_ROOT.mkdir(parents=True, exist_ok=True)
    logger.info(
        "PDF Redactor API starting (workers=%d, job_root=%s)",
        _WORKER_COUNT,
        JOB_ROOT,
    )
    try:
        yield
    finally:
        logger.info("PDF Redactor API shutting down; stopping executor")
        _executor.shutdown(wait=False, cancel_futures=True)


app = FastAPI(
    title="PDF Redactor API",
    description=(
        "Upload one or more PDFs to `/jobs` to have them redacted with "
        "`PDFRedactor`. The API is job-based: poll `/jobs/{id}` and download "
        "`/jobs/{id}/result` (a ZIP containing every redacted PDF and its "
        "XLSX translation table) when the job completes."
    ),
    version="1.0.0",
    lifespan=_lifespan,
)


def _validate_pdf_upload(upload: UploadFile) -> None:
    """Reject uploads that clearly aren't PDFs."""
    name = (upload.filename or "").lower()
    ctype = (upload.content_type or "").lower()
    if not name.endswith(".pdf") and ctype != "application/pdf":
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Not a PDF: {upload.filename!r}",
        )


async def _spool_upload(upload: UploadFile, dest: Path) -> None:
    """Stream ``upload`` to ``dest`` in fixed-size chunks."""
    chunk_size = 1024 * 1024  # 1 MiB
    with dest.open("wb") as fh:
        while True:
            chunk = await upload.read(chunk_size)
            if not chunk:
                break
            fh.write(chunk)
    await upload.close()


@app.get("/health", tags=["meta"])
async def health() -> Dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}


@app.post(
    "/jobs",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["jobs"],
)
async def create_job(
    files: List[UploadFile] = File(
        ..., description="One or more PDF files to redact."
    ),
    language: str = Form(
        "en", description="Language code passed to Presidio (default 'en')."
    ),
    use_llm: bool = Form(
        True,
        description=(
            "Enable the BasicLangExtractRecognizer. Set to false to skip "
            "the LLM-backed pass (much faster, weaker recall)."
        ),
    ),
) -> JobResponse:
    """Accept one or more PDF uploads and enqueue a redaction job."""
    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one PDF file is required.",
        )
    for upload in files:
        _validate_pdf_upload(upload)

    job = job_manager.create(file_count=len(files))
    uploads_dir = job.workdir / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)

    input_paths: List[Path] = []
    try:
        for idx, upload in enumerate(files):
            safe_name = Path(upload.filename or f"file_{idx}.pdf").name
            # Each upload lands in its own subdirectory so we can preserve the
            # original filename (and therefore stem) even when clients submit
            # multiple files that share a name.
            slot = uploads_dir / f"{idx:03d}"
            slot.mkdir(parents=True, exist_ok=True)
            dest = slot / safe_name
            await _spool_upload(upload, dest)
            input_paths.append(dest)
    except Exception:
        # Upload failed halfway through — cancel the job and re-raise.
        job_manager.delete(job.id)
        raise

    submit_job(job.id, input_paths, language=language, use_llm=use_llm)
    return _job_to_response(job)


@app.get("/jobs/{job_id}", response_model=JobResponse, tags=["jobs"])
async def get_job(job_id: str) -> JobResponse:
    """Return the current status of ``job_id``."""
    job = job_manager.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown job {job_id!r}",
        )
    return _job_to_response(job)


@app.get("/jobs/{job_id}/result", tags=["jobs"])
async def get_job_result(job_id: str) -> Response:
    """Stream the ZIP archive of redacted PDFs + XLSX tables."""
    job = job_manager.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown job {job_id!r}",
        )
    if job.status is JobStatus.FAILED:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": job.error or "Job failed."},
        )
    if job.status is not JobStatus.COMPLETED or job.result_path is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Job {job_id!r} is {job.status.value!r}; result not ready.",
        )
    if not job.result_path.exists():
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Result archive is no longer available on disk.",
        )
    return FileResponse(
        path=job.result_path,
        media_type="application/zip",
        filename=f"{job.id}.zip",
    )


@app.delete(
    "/jobs/{job_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["jobs"],
)
async def delete_job(job_id: str) -> Response:
    """Remove ``job_id`` and every artefact on disk."""
    if not job_manager.delete(job_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown job {job_id!r}",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "api:app",
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8000")),
        reload=False,
    )

