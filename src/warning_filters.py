"""Narrow suppressions for third-party warnings we cannot fix upstream.

Imported at the top of the process entry points (``src.job_controller`` and
``gui``) so the filters are registered before Presidio, langextract, PyMuPDF,
and friends are loaded.

Setting ``PDF_REDACTOR_WARNINGS=1`` in the environment causes each entry point
to override these with ``warnings.simplefilter("always")`` so the full set of
warnings can be re-audited when upgrading dependencies.

Every entry documents *what* it silences and *why*. Remove an entry once the
upstream project ships a fix so we don't hide a new, distinct warning of the
same shape.
"""

import logging
import warnings

# ---------------------------------------------------------------------------
# 1. ``warnings.warn()`` sources -- catchable via warnings.filterwarnings.
# ---------------------------------------------------------------------------

# SWIG-generated C extensions pulled in transitively (Tesseract/PyMuPDF chain)
# emit one warning per SWIG type (``swigvarlink``, ``SwigPyObject``,
# ``SwigPyPacked``) once during import on newer Pythons. No user-facing impact.
warnings.filterwarnings(
    "ignore",
    message=r"builtin type \w+ has no __module__ attribute",
    category=DeprecationWarning,
)

# ``absl-py`` (pulled in by ``langextract`` via ``google-genai``) still
# references the private typing alias ``_UnionGenericAlias`` slated for removal
# in Python 3.17. Silence until absl-py releases a compatible version.
warnings.filterwarnings(
    "ignore",
    message=r".*_UnionGenericAlias.*",
    category=DeprecationWarning,
)

# ``pydicom`` (pulled in by ``presidio-image-redactor``) also emits the below
# via ``warnings.warn`` in some import paths.
warnings.filterwarnings(
    "ignore",
    message=r".*pydicom\.pixel_data_handlers.*",
    category=DeprecationWarning,
)


# ---------------------------------------------------------------------------
# 2. ``logger.warning()`` sources -- need logging filters, not warnings ones.
# ---------------------------------------------------------------------------


class _DropMessageContaining(logging.Filter):
    """Drop log records whose message contains ``needle``."""

    def __init__(self, needle: str) -> None:
        super().__init__()
        self._needle = needle

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        return self._needle not in record.getMessage()


# ``pydicom`` announces the v4.0 removal of ``pydicom.pixel_data_handlers`` via
# its own logger at import time. We don't import that module ourselves; it's
# reached transitively through ``presidio-image-redactor``. Drop just that one
# message so other ``pydicom`` warnings still surface.
logging.getLogger("pydicom").addFilter(
    _DropMessageContaining("pydicom.pixel_data_handlers")
)

# spaCy's NER emits fixed labels (CARDINAL, WORK_OF_ART, DATE, ...) which
# Presidio's default mapping doesn't cover. Presidio then logs
# "Entity X is not mapped to a Presidio entity, but keeping anyway" per token,
# per page -- extremely noisy. We *want* Presidio to keep unmapped labels
# (LangExtract-generated entities have arbitrary AI-generated types we cannot
# enumerate ahead of time), we just don't want the warning. Drop only that
# specific line so other ``presidio-analyzer`` warnings still surface.
logging.getLogger("presidio-analyzer").addFilter(
    _DropMessageContaining("is not mapped to a Presidio entity")
)

