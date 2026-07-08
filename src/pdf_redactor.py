"""Reusable PDF redaction class built on Presidio + PyMuPDF.

The class takes a `fitz.Document`, applies text + image redactions in place
using Presidio recognizers (including a `BasicLangExtractRecognizer` by
default), and returns the mutated document together with a translation
table describing every detected entity.
"""

from __future__ import annotations

import io
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, TypedDict

import fitz  # PyMuPDF
from PIL import Image, ImageDraw, ImageFont

from presidio_analyzer import AnalyzerEngine, RecognizerResult
from presidio_analyzer.predefined_recognizers.third_party.basic_langextract_recognizer import (
    BasicLangExtractRecognizer,
)
from presidio_image_redactor.entities import ImageRecognizerResult

from src.presidio_extensions.custom_image_analyzer import CustomImageAnalyzerEngine
from src.presidio_extensions.presidio_utils import resolve_conflicts


logger = logging.getLogger(__name__)


#: Absolute path to the default LangExtract config, resolved against this
#: module's location so callers can run from any working directory.
DEFAULT_CONFIG_PATH: str = str(
    Path(__file__).resolve().parent
    / "config"
    / "ollama_config.yaml"
)


class TranslationEntry(TypedDict):
    """One row of the translation table produced by :meth:`PDFRedactor.redact`."""

    id: Optional[str]
    scores: Dict[str, float]


#: Mapping from detected entity text to its :class:`TranslationEntry`.
TranslationTable = Dict[str, TranslationEntry]

#: Queued text redaction: ``(page, rect, entity_text)``.
PendingTextRedaction = Tuple[fitz.Page, fitz.Rect, str]

#: Queued image redaction:
#: ``(page, xref, pil_image, [(bbox, entity_text), ...])``.
PendingImageRedaction = Tuple[
    fitz.Page,
    int,
    Image.Image,
    List[Tuple[ImageRecognizerResult, str]],
]


def _load_font(size: int) -> ImageFont.ImageFont:
    """Return a truetype font at ``size`` px, falling back to PIL's default."""
    for candidate in ("Helvetica.ttc", "Arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(candidate, size)
        except (IOError, OSError):
            continue
    return ImageFont.load_default()


class PDFRedactor:
    """Redact text + image PII in a `fitz.Document`.

    Parameters
    ----------
    analyzer:
        Pre-configured Presidio `AnalyzerEngine`. When ``None`` (default) a
        new engine is built and, if ``use_llm`` is ``True``, a
        `BasicLangExtractRecognizer` is registered using ``config_path``.
    image_analyzer:
        Pre-configured `CustomImageAnalyzerEngine`. When ``None`` (default)
        one is built on top of ``self.analyzer``.
    config_path:
        Path to the YAML config used to construct the default
        `BasicLangExtractRecognizer`. Ignored when ``analyzer`` is provided
        or when ``use_llm`` is ``False``.
    use_llm:
        When ``True`` (default), register a `BasicLangExtractRecognizer` on
        the default analyzer. Ignored when ``analyzer`` is provided.
    language:
        Language code passed to the analyzer for text detection. Defaults to
        ``"en"``.
    """

    def __init__(
        self,
        analyzer: Optional[AnalyzerEngine] = None,
        image_analyzer: Optional[CustomImageAnalyzerEngine] = None,
        config_path: "str | os.PathLike[str]" = DEFAULT_CONFIG_PATH,
        use_llm: bool = True,
        language: str = "en",
    ) -> None:
        if analyzer is None:
            analyzer = AnalyzerEngine()
            if use_llm:
                analyzer.registry.add_recognizer(
                    BasicLangExtractRecognizer(config_path=str(config_path))
                )
        self.analyzer = analyzer

        if image_analyzer is None:
            image_analyzer = CustomImageAnalyzerEngine(analyzer_engine=self.analyzer)
        self.image_analyzer = image_analyzer

        self.language = language

    # ------------------------------------------------------------------ public

    def redact(
        self, doc: fitz.Document
    ) -> Tuple[fitz.Document, TranslationTable]:
        """Redact ``doc`` in place and return ``(doc, translation_table)``.

        The translation table maps every detected entity_text to::

            {"id": "PERSON-1", "scores": {"PERSON": 0.85, ...}}
        """
        translation_table: TranslationTable = {}
        pending_redactions: List[PendingTextRedaction] = []
        pending_image_redactions: List[PendingImageRedaction] = []
        processed_xrefs: Set[int] = set()

        for page in doc:
            self._analyze_page_images(
                page,
                doc,
                translation_table,
                pending_image_redactions,
                processed_xrefs,
            )
            self._analyze_page_text(page, translation_table, pending_redactions)

        self._finalize_translation_table(translation_table)
        self._draw_image_redactions(pending_image_redactions, translation_table)
        self._apply_text_redactions(pending_redactions, translation_table)

        return doc, translation_table

    # ----------------------------------------------------------------- private

    @staticmethod
    def _process_results(
        results: List[RecognizerResult],
        text: str,
        translation_table: TranslationTable,
    ) -> None:
        """Accumulate detections into ``translation_table`` (max score per type)."""
        for result in results:
            entity_text = text[result.start:result.end]
            if entity_text not in translation_table:
                translation_table[entity_text] = {
                    "id": None,
                    "scores": {result.entity_type: result.score},
                }
            else:
                scores = translation_table[entity_text]["scores"]
                if result.entity_type not in scores:
                    scores[result.entity_type] = result.score
                else:
                    scores[result.entity_type] = max(
                        scores[result.entity_type], result.score
                    )

    def _analyze_page_images(
        self,
        page: fitz.Page,
        doc: fitz.Document,
        translation_table: TranslationTable,
        pending_image_redactions: List[PendingImageRedaction],
        processed_xrefs: Set[int],
    ) -> None:
        """OCR every image on ``page`` and queue redactions for later drawing.

        Images shared across multiple pages (identified by ``xref``) are OCR'd
        and queued only once; PyMuPDF's ``page.replace_image`` then updates
        every page that references the same xref.
        """
        for img_info in page.get_images(full=True):
            xref = img_info[0]
            if xref in processed_xrefs:
                continue
            processed_xrefs.add(xref)

            try:
                img_data = doc.extract_image(xref)
                pil_image = Image.open(io.BytesIO(img_data["image"]))

                bboxes, text = self.image_analyzer.analyze(
                    pil_image, language=self.language
                )
            except Exception as exc:  # noqa: BLE001 - log and skip bad images
                logger.warning(
                    "Skipping image xref %s on page %s: %s",
                    xref,
                    page.number,
                    exc,
                )
                continue

            self._process_results(bboxes, text, translation_table)

            if not bboxes:
                pil_image.close()
                continue

            # Capture entity_text for each box now (we have the OCR'd `text`);
            # actual drawing/replacing happens after ids are assigned.
            image_entries = [(box, text[box.start:box.end]) for box in bboxes]
            pending_image_redactions.append(
                (page, xref, pil_image, image_entries)
            )

    def _analyze_page_text(
        self,
        page: fitz.Page,
        translation_table: TranslationTable,
        pending_redactions: List[PendingTextRedaction],
    ) -> None:
        """Run Presidio on each text span of ``page`` and queue redaction rects."""
        text_dict = page.get_text("rawdict")

        for block in text_dict["blocks"]:
            if block["type"] != 0:  # Skip non-text blocks (e.g. images)
                continue

            for line in block["lines"]:
                for span in line["spans"]:
                    chars = span.get("chars", [])
                    text = "".join(c["c"] for c in chars)

                    if not text.strip() or not chars:
                        continue

                    results = self.analyzer.analyze(text=text, language=self.language)
                    results = resolve_conflicts(text, results)

                    self._process_results(results, text, translation_table)

                    for result in results:
                        matched_chars = chars[result.start:result.end]
                        if not matched_chars:
                            continue

                        # Combine individual character bounding boxes
                        x0 = min(c["bbox"][0] for c in matched_chars)
                        y0 = min(c["bbox"][1] for c in matched_chars)
                        x1 = max(c["bbox"][2] for c in matched_chars)
                        y1 = max(c["bbox"][3] for c in matched_chars)

                        entity_text = text[result.start:result.end]
                        pending_redactions.append(
                            (page, fitz.Rect(x0, y0, x1, y1), entity_text)
                        )

    @staticmethod
    def _finalize_translation_table(translation_table: TranslationTable) -> None:
        """Assign a sequential ``id`` (e.g. ``"PERSON-1"``) to every entry."""
        type_counters: Dict[str, int] = {}
        for entry in translation_table.values():
            scores = entry["scores"]
            if not scores:
                entry["id"] = None
                continue
            top_type = max(scores.items(), key=lambda kv: kv[1])[0]
            type_counters[top_type] = type_counters.get(top_type, 0) + 1
            entry["id"] = f"{top_type}-{type_counters[top_type]}"

    @staticmethod
    def _draw_image_redactions(
        pending_image_redactions: List[PendingImageRedaction],
        translation_table: TranslationTable,
    ) -> None:
        """Draw labeled boxes onto OCR'd images and push them back into the PDF."""
        for page, xref, pil_image, image_entries in pending_image_redactions:
            draw = ImageDraw.Draw(pil_image)
            for box, entity_text in image_entries:
                x0 = box.left
                y0 = box.top
                x1 = x0 + box.width
                y1 = y0 + box.height
                draw.rectangle([x0, y0, x1, y1], fill=(255, 255, 255))

                label = translation_table.get(entity_text, {}).get("id") or ""
                if not label:
                    continue

                fontsize = max(8, int(box.height * 0.6))
                font = _load_font(fontsize)
                tx0, ty0, tx1, ty1 = font.getbbox(label)
                tw, th = tx1 - tx0, ty1 - ty0
                tx = x0 + (box.width - tw) / 2 - tx0
                ty = y0 + (box.height - th) / 2 - ty0
                draw.text((tx, ty), label, fill=(0, 0, 0), font=font)

            with io.BytesIO() as output:
                pil_image.save(output, format="PNG")
                page.replace_image(xref, stream=output.getvalue())
            pil_image.close()

    @staticmethod
    def _apply_text_redactions(
        pending_redactions: List[PendingTextRedaction],
        translation_table: TranslationTable,
    ) -> None:
        """Add labeled redact annotations, then apply them per affected page."""
        pages_to_apply = set()
        for page, rect, entity_text in pending_redactions:
            label = translation_table.get(entity_text, {}).get("id") or ""
            # Scale font to fit the box height; PyMuPDF will clip if the label
            # is wider than the rect (entity ids are often longer than the
            # original text).
            fontsize = max(1.0, rect.height * 0.6)
            page.add_redact_annot(
                rect,
                text=label,
                fontname="helv",
                fontsize=fontsize,
                text_color=(0, 0, 0),
                align=fitz.TEXT_ALIGN_CENTER,
            )
            pages_to_apply.add(page)

        for page in pages_to_apply:
            # Apply redactions: removes the underlying text from the content
            # stream and draws the labeled black rectangles in its place.
            page.apply_redactions()
