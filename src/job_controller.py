"""FastAPI controller exposing :class:`PDFRedactor` as a job-based HTTP API.

Clients POST one or more PDFs to ``/jobs``, poll ``/jobs/{id}`` until the job
completes, and download a ZIP archive containing every redacted PDF and its
``.xlsx`` translation table from ``/jobs/{id}/result``. Individual jobs may
be removed with ``DELETE /jobs/{id}``. The collection endpoints
``GET /jobs`` and ``DELETE /jobs`` list or clear every tracked job in one
call, and ``/health`` provides a simple liveness probe.

All business logic (job registry, redactor cache, worker pool) lives in
:mod:`src.job_service`; this module only wires HTTP request/response handling
onto that service. The ASGI lifespan hook configures logging, ensures the
job root directory exists, and shuts the worker executor down cleanly.

Environment variables:

* ``PDF_REDACTOR_API_LOG_LEVEL`` — log level used at startup (default
  ``INFO``).
* ``HOST`` / ``PORT`` — bind address when the module is executed directly
  (defaults ``127.0.0.1:8000``).

Run locally with::

    uvicorn src.job_controller:app --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, List

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
from fastapi.staticfiles import StaticFiles

from src import job_service
from src.domain.job_response import JobResponse
from src.domain.job_status import JobStatus
from src.job_repository import JOB_ROOT
from src.utils import validate_pdf_upload


logger = logging.getLogger(__name__)


__all__ = ["app"]


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
        job_service.worker_count(),
        JOB_ROOT,
    )
    try:
        yield
    finally:
        logger.info("PDF Redactor API shutting down; stopping executor")
        job_service.shutdown_executor()


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

_WEB_DIR = Path(__file__).resolve().parent / "web"
_INDEX_HTML = _WEB_DIR / "index.html"

app.mount(
    "/static",
    StaticFiles(directory=_WEB_DIR / "static"),
    name="static",
)


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    """Serve the single-page web UI."""
    return FileResponse(_INDEX_HTML, media_type="text/html")


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
        validate_pdf_upload(upload)

    job = await job_service.create_job(
        files=files, language=language, use_llm=use_llm
    )
    return job.to_response()


@app.get("/jobs", response_model=List[JobResponse], tags=["jobs"])
async def list_jobs() -> List[JobResponse]:
    """Return the current status of every tracked job."""
    return [job.to_response() for job in job_service.list_jobs()]


@app.delete("/jobs", tags=["jobs"])
async def delete_all_jobs() -> Dict[str, int]:
    """Remove every tracked job and its artefacts. Returns the number removed."""
    deleted = job_service.delete_all_jobs()
    return {"deleted": deleted}


@app.get("/jobs/{job_id}", response_model=JobResponse, tags=["jobs"])
async def get_job(job_id: str) -> JobResponse:
    """Return the current status of ``job_id``."""
    job = job_service.get_job(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown job {job_id!r}",
        )
    return job.to_response()


@app.get("/jobs/{job_id}/result", tags=["jobs"])
async def get_job_result(job_id: str) -> Response:
    """Stream the ZIP archive of redacted PDFs + XLSX tables."""
    job = job_service.get_job(job_id)
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
    if not job_service.delete_job(job_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown job {job_id!r}",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "src.job_controller:app",
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8000")),
        reload=False,
    )

