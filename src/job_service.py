"""Business logic for the PDF redaction job API.

This module owns:

* The cached :class:`~src.pdf_redactor.PDFRedactor` factory :func:`get_redactor`.
* The public job lifecycle API used by the HTTP controller:
  :func:`create_job`, :func:`get_job`, :func:`list_jobs`, :func:`delete_job`,
  and :func:`delete_all_jobs`.
* The background worker pool (a :class:`concurrent.futures.ThreadPoolExecutor`
  sized by ``PDF_REDACTOR_API_WORKERS``) and its management helpers
  :func:`worker_count` and :func:`shutdown_executor`.
* The private worker pipeline — :func:`_submit_job`, :func:`_process_job`,
  and :func:`_redact_one` — that redacts each uploaded PDF, captures per-file
  failures as ``<stem>_error.txt`` artefacts, and packages the results into
  ``result.zip``.

The FastAPI layer in :mod:`src.job_controller` is a thin controller over this
service; the :class:`~src.job_repository.JobRepository` singleton
:data:`~src.job_repository.job_repository` lives in :mod:`src.job_repository`.
"""

from __future__ import annotations

import concurrent.futures
import functools
import logging
import os
import traceback
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import UploadFile

from src.domain.job import Job
from src.domain.job_status import JobStatus
from src.job_repository import job_repository
from src.utils import build_zip, safe_stem, spool_upload, utcnow


logger = logging.getLogger(__name__)


__all__ = [
    "create_job",
    "delete_all_jobs",
    "delete_job",
    "get_job",
    "get_redactor",
    "list_jobs",
    "shutdown_executor",
    "worker_count",
]


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


def worker_count() -> int:
    """Return the number of concurrent redaction workers."""
    return _WORKER_COUNT


def shutdown_executor() -> None:
    """Stop the worker pool. Safe to call at process shutdown."""
    _executor.shutdown(wait=False, cancel_futures=True)


@functools.lru_cache(maxsize=None)
def get_redactor(language: str, use_llm: bool):  # noqa: ANN201 - forward ref
    """Return a cached :class:`PDFRedactor` for ``(language, use_llm)``.

    Building the redactor loads the Presidio analyzer and, when ``use_llm`` is
    true, the LangExtract recognizer — both expensive. The cache keeps one
    instance per unique key for the process lifetime.

    Imports are lazy so that importing this module (e.g. for tests) does not
    require the heavy Presidio / PyMuPDF stack.
    """
    from src.pdf_redactor import PDFRedactor  # local import: heavy deps

    logger.info(
        "Building PDFRedactor (language=%s, use_llm=%s)", language, use_llm
    )
    return PDFRedactor(use_llm=use_llm, language=language)


def get_job(job_id: str) -> Optional[Job]:
    """Return the job or ``None`` if unknown."""
    return job_repository.get(job_id)


def list_jobs() -> List[Job]:
    """Return a snapshot of every currently tracked job."""
    return job_repository.get_all()


async def create_job(
    files: List[UploadFile],
    language: str,
    use_llm: bool,
) -> Job:
    """Create a new job entry, spool uploads to its workdir, and enqueue it."""
    job = job_repository.create(file_count=len(files))
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
            await spool_upload(upload, dest)
            input_paths.append(dest)
    except Exception:
        # Upload failed halfway through — cancel the job and re-raise.
        job_repository.delete(job.id)
        raise

    _submit_job(job.id, input_paths, language=language, use_llm=use_llm)
    return job


def delete_job(job_id: str) -> bool:
    """Drop the job entry and remove its workdir. Returns ``True`` if removed."""
    return job_repository.delete(job_id)


def delete_all_jobs() -> int:
    """Drop every tracked job and remove its workdir. Returns the count removed."""
    return job_repository.delete_all()


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

    from src.translation_table import save_translation_table_xlsx

    stem = output_stem or safe_stem(input_path)
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


def _process_job(
    job_id: str,
    input_paths: List[Path],
    language: str,
    use_llm: bool,
) -> None:
    """Worker entry point: redact every input PDF and produce ``result.zip``.

    Updates the job's status in :data:`job_repository` as it progresses. Any
    unrecoverable error (e.g. inability to open the workdir) marks the job
    ``FAILED``; per-file errors are captured inside the ZIP instead.
    """
    job = job_repository.get(job_id)
    if job is None:
        logger.warning("process_job: unknown job %s", job_id)
        return

    job_repository.update(
        job_id, status=JobStatus.RUNNING, started_at=utcnow()
    )

    try:
        redacted_dir = job.workdir / "redacted"
        redacted_dir.mkdir(parents=True, exist_ok=True)

        artefacts: List[Path] = []
        used_stems: Dict[str, int] = {}
        for input_path in input_paths:
            base = safe_stem(input_path)
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
        build_zip(artefacts, zip_path)

        job_repository.update(
            job_id,
            status=JobStatus.COMPLETED,
            finished_at=utcnow(),
            result_path=zip_path,
        )
        logger.info("Job %s completed (%d artefacts)", job_id, len(artefacts))
    except Exception as exc:  # noqa: BLE001 - top-level worker guard
        logger.exception("Job %s failed", job_id)
        job_repository.update(
            job_id,
            status=JobStatus.FAILED,
            finished_at=utcnow(),
            error=str(exc) or exc.__class__.__name__,
        )


def _submit_job(
    job_id: str,
    input_paths: List[Path],
    language: str,
    use_llm: bool,
) -> concurrent.futures.Future:
    """Enqueue ``process_job`` on the module-level executor."""
    return _executor.submit(
        _process_job, job_id, input_paths, language, use_llm
    )
