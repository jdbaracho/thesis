"""Reusable PDF redaction class built on Presidio + PyMuPDF.

The class takes a `fitz.Document`, applies text + image redactions in place
using Presidio recognizers (including a `BasicLangExtractRecognizer` by
default), and returns the mutated document together with a translation
table describing every detected entity.
"""

from __future__ import annotations

import functools
import io
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, TypedDict

import fitz  # PyMuPDF
from PIL import Image, ImageDraw, ImageFont

from presidio_analyzer import AnalyzerEngine, RecognizerResult
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_analyzer.predefined_recognizers.third_party.basic_langextract_recognizer import (
    BasicLangExtractRecognizer,
)
from presidio_image_redactor.entities import ImageRecognizerResult

from src.presidio_extensions.custom_image_analyzer import CustomImageAnalyzerEngine
from src.presidio_extensions.presidio_utils import resolve_conflicts


logger = logging.getLogger(__name__)


#: Directory holding per-language LangExtract configs.
_CONFIG_DIR: Path = Path(__file__).resolve().parent / "config"


#: Per-language wiring for the redactor. Keys are the ISO codes exposed by the
#: web UI. ``spacy_model`` is the model name loaded into Presidio's spaCy NLP
#: engine; ``tesseract_lang`` is the code passed to ``pytesseract`` for OCR;
#: ``config_path`` is the LangExtract YAML used to build a language-specific
#: :class:`BasicLangExtractRecognizer`.
#:
#: Portuguese: spaCy ships a single ``pt_core_news_lg`` covering both
#: variants; Tesseract's ``por`` traineddata is European Portuguese
#: (``por_BR`` is Brazilian), so we use ``por`` here.
LANGUAGE_CONFIG: Dict[str, Dict[str, str]] = {
    "en": {
        "spacy_model": "en_core_web_lg",
        "tesseract_lang": "eng",
        "config_path": str(_CONFIG_DIR / "ollama_config.en.yaml"),
    },
    "es": {
        "spacy_model": "es_core_news_lg",
        "tesseract_lang": "spa",
        "config_path": str(_CONFIG_DIR / "ollama_config.es.yaml"),
    },
    "pt": {
        "spacy_model": "pt_core_news_lg",
        "tesseract_lang": "por",
        "config_path": str(_CONFIG_DIR / "ollama_config.pt.yaml"),
    },
}

#: Absolute path to the default LangExtract config. Retained for backward
#: compatibility with callers that imported it directly; new code should
#: index :data:`LANGUAGE_CONFIG` instead.
DEFAULT_CONFIG_PATH: str = LANGUAGE_CONFIG["en"]["config_path"]


@functools.lru_cache(maxsize=None)
def _build_analyzer(use_llm: bool) -> AnalyzerEngine:
    """Return a multi-lingual :class:`AnalyzerEngine` shared across languages.

    Loads every spaCy model listed in :data:`LANGUAGE_CONFIG` into a single
    Presidio ``NlpEngine`` and, when ``use_llm`` is true, registers one
    :class:`BasicLangExtractRecognizer` per language (each with its own
    ``supported_language`` and per-language ``config_path``).

    Cached on ``use_llm`` so the heavy spaCy load happens once per process.
    Missing spaCy models surface as a :class:`RuntimeError` naming the exact
    ``python -m spacy download`` command required.
    """
    languages = sorted(LANGUAGE_CONFIG.keys())
    nlp_configuration = {
        "nlp_engine_name": "spacy",
        "models": [
            {"lang_code": lang, "model_name": LANGUAGE_CONFIG[lang]["spacy_model"]}
            for lang in languages
        ],
    }
    logger.info(
        "Building AnalyzerEngine (languages=%s, use_llm=%s)", languages, use_llm
    )
    try:
        nlp_engine = NlpEngineProvider(
            nlp_configuration=nlp_configuration
        ).create_engine()
    except OSError as exc:
        missing = ", ".join(
            f"python -m spacy download {LANGUAGE_CONFIG[lang]['spacy_model']}"
            for lang in languages
        )
        raise RuntimeError(
            f"Failed to load spaCy models for {languages}: {exc}. "
            f"Run: {missing}"
        ) from exc

    analyzer = AnalyzerEngine(
        nlp_engine=nlp_engine, supported_languages=languages
    )
    if use_llm:
        for lang in languages:
            analyzer.registry.add_recognizer(
                BasicLangExtractRecognizer(
                    config_path=LANGUAGE_CONFIG[lang]["config_path"],
                    supported_language=lang,
                )
            )
    return analyzer


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


#: Matches any run of whitespace (spaces, tabs, newlines) for entity-key
#: normalization.
_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_entity_text(text: str) -> str:
    """Collapse runs of whitespace to a single space and strip ends.

    Used as the canonical translation-table key so ill-formatted PDFs
    that emit e.g. ``"John   Doe"`` and ``"John Doe"`` map to the same
    entry (and therefore share the same alias id).
    """
    return _WHITESPACE_RE.sub(" ", text).strip()


class PDFRedactor:
    """Redact text + image PII in a `fitz.Document`.

    Parameters
    ----------
    analyzer:
        Pre-configured Presidio `AnalyzerEngine`. When ``None`` (default) a
        multi-lingual engine built by :func:`_build_analyzer` is used
        (shared across all instances with the same ``use_llm``).
    image_analyzer:
        Pre-configured `CustomImageAnalyzerEngine`. When ``None`` (default)
        one is built on top of ``self.analyzer``.
    use_llm:
        When ``True`` (default), each supported language gets a
        `BasicLangExtractRecognizer` registered on the default analyzer.
        Ignored when ``analyzer`` is provided.
    language:
        Language code passed to the analyzer for text detection and to
        Tesseract for OCR. Must be a key of :data:`LANGUAGE_CONFIG`
        (currently ``"en"``, ``"es"``, ``"pt"``). Defaults to ``"en"``.
    """

    def __init__(
        self,
        analyzer: Optional[AnalyzerEngine] = None,
        image_analyzer: Optional[CustomImageAnalyzerEngine] = None,
        use_llm: bool = True,
        language: str = "en",
    ) -> None:
        if language not in LANGUAGE_CONFIG:
            raise ValueError(
                f"Unsupported language {language!r}. "
                f"Supported: {sorted(LANGUAGE_CONFIG)}"
            )

        if analyzer is None:
            analyzer = _build_analyzer(use_llm)
        self.analyzer = analyzer

        if image_analyzer is None:
            image_analyzer = CustomImageAnalyzerEngine(analyzer_engine=self.analyzer)
        self.image_analyzer = image_analyzer

        self.language = language
        self.tesseract_lang = LANGUAGE_CONFIG[language]["tesseract_lang"]

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
            logger.info("Analyzing page %s/%s", page.number + 1, len(doc))
            self._analyze_page_text(page, translation_table, pending_redactions)
            self._analyze_page_images(
                page,
                doc,
                translation_table,
                pending_image_redactions,
                processed_xrefs,
            )

        self._finalize_translation_table(translation_table)
        self._apply_text_redactions(pending_redactions, translation_table)
        self._draw_image_redactions(pending_image_redactions, translation_table)

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
            entity_text = _normalize_entity_text(text[result.start:result.end])
            if not entity_text:
                continue
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
            except (OSError, KeyError, RuntimeError) as exc:
                # Narrow: extract_image can raise RuntimeError/KeyError for
                # malformed xrefs; PIL raises UnidentifiedImageError (OSError)
                # for unsupported/corrupt image streams.
                logger.warning(
                    "Skipping unreadable image xref %s on page %s: %s",
                    xref,
                    page.number,
                    exc,
                )
                continue

            try:
                bboxes, text = self.image_analyzer.analyze(
                    pil_image,
                    ocr_kwargs={"lang": self.tesseract_lang},
                    language=self.language,
                )
            except Exception as exc:  # noqa: BLE001 - OCR/Presidio pipeline is opaque; log and skip
                logger.warning(
                    "OCR failed on image xref %s on page %s: %s",
                    xref,
                    page.number,
                    exc,
                )
                pil_image.close()
                continue

            self._process_results(bboxes, text, translation_table)

            if not bboxes:
                pil_image.close()
                continue

            # Capture entity_text for each box now (we have the OCR'd `text`);
            # actual drawing/replacing happens after ids are assigned.
            image_entries = [
                (box, _normalize_entity_text(text[box.start:box.end]))
                for box in bboxes
            ]
            pending_image_redactions.append(
                (page, xref, pil_image, image_entries)
            )

    def _analyze_page_text(
        self,
        page: fitz.Page,
        translation_table: TranslationTable,
        pending_redactions: List[PendingTextRedaction],
    ) -> None:
        """Run Presidio on the full page text and queue redaction rects.

        Building a single page-level string (rather than analyzing one span at
        a time) is required because justified PDFs frequently emit each word
        as its own line/span. Analyzing per span would hide entities like
        ``"Jonathan Vance Jr."`` from the recognizer since the words would
        never appear together in the input.
        """
        full_text, char_records = self._extract_page_text(page)
        if not full_text.strip():
            return

        results = self.analyzer.analyze(text=full_text, language=self.language)
        results = resolve_conflicts(full_text, results)

        self._process_results(results, full_text, translation_table)

        for result in results:
            entity_text = _normalize_entity_text(
                full_text[result.start:result.end]
            )
            matched_chars = [
                c for c in char_records[result.start:result.end] if c is not None
            ]
            for rect in self._chars_to_line_rects(matched_chars):
                pending_redactions.append((page, rect, entity_text))

    @staticmethod
    def _extract_page_text(
        page: fitz.Page,
    ) -> Tuple[str, List[Optional[dict]]]:
        """Return ``(full_text, char_records)`` for ``page``.

        ``char_records`` is index-aligned with ``full_text``: entry ``i`` is
        the source char dict (with ``bbox``) for ``full_text[i]``, or ``None``
        for separators inserted at line/block boundaries.
        """
        text_parts: List[str] = []
        char_records: List[Optional[dict]] = []

        for block in page.get_text("rawdict")["blocks"]:
            if block["type"] != 0:  # Skip non-text blocks (e.g. images)
                continue

            for line in block["lines"]:
                for span in line["spans"]:
                    for ch in span.get("chars", []):
                        text_parts.append(ch["c"])
                        char_records.append(ch)
                # Line boundary: insert a space so adjacent words don't merge.
                text_parts.append(" ")
                char_records.append(None)
            # Block boundary: newline helps Presidio's sentence heuristics
            # without gluing paragraphs together.
            text_parts.append("\n")
            char_records.append(None)

        return "".join(text_parts), char_records

    @staticmethod
    def _chars_to_line_rects(matched_chars: List[dict]) -> List[fitz.Rect]:
        """Group ``matched_chars`` by visual line and return one rect per line.

        A single entity match may wrap across lines; emitting one rect per
        line prevents the union bbox from covering unrelated text between
        them.
        """
        if not matched_chars:
            return []

        matched_chars = sorted(
            matched_chars,
            key=lambda c: (
                round((c["bbox"][1] + c["bbox"][3]) / 2, 1),
                c["bbox"][0],
            ),
        )

        line_groups: List[List[dict]] = [[matched_chars[0]]]
        for c in matched_chars[1:]:
            y_mid = (c["bbox"][1] + c["bbox"][3]) / 2
            line_height = c["bbox"][3] - c["bbox"][1]
            prev_bbox = line_groups[-1][0]["bbox"]
            prev_mid = (prev_bbox[1] + prev_bbox[3]) / 2
            if abs(y_mid - prev_mid) < max(line_height, 1.0):
                line_groups[-1].append(c)
            else:
                line_groups.append([c])

        rects: List[fitz.Rect] = []
        for line_chars in line_groups:
            x0 = min(c["bbox"][0] for c in line_chars)
            y0 = min(c["bbox"][1] for c in line_chars)
            x1 = max(c["bbox"][2] for c in line_chars)
            y1 = max(c["bbox"][3] for c in line_chars)
            rects.append(fitz.Rect(x0, y0, x1, y1))
        return rects

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
            if pil_image.mode != "RGB":
                pil_image = pil_image.convert("RGB")
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
