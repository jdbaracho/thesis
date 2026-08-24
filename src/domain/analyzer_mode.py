"""Which Presidio recognizers participate in a redaction job."""

from __future__ import annotations

from enum import Enum


__all__ = ["AnalyzerMode"]


class AnalyzerMode(str, Enum):
    """Analyzer configuration selected per-job.

    * ``SIMPLE`` — only the default Presidio recognizers (no LLM pass).
    * ``HYBRID`` — default Presidio recognizers plus
      :class:`BasicLangExtractRecognizer` (previous ``use_llm=True`` behaviour).
    * ``LLM`` — only :class:`BasicLangExtractRecognizer`; the default Presidio
      recognizers are skipped.
    """

    SIMPLE = "simple"
    HYBRID = "hybrid"
    LLM = "llm"
