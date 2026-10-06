"""Debug tool: dump the exact text the redactor sees from a PDF.

This does NOT redact anything. For each page it reports:
- the page text layer extracted via ``PDFRedactor._extract_page_text`` (the
  string handed to Presidio's text analyzer), and
- the OCR text for every embedded image via ``image_analyzer.analyze`` (the
  string handed to the image analyzer).

Use it to check whether poor redaction results come from bad text extraction
(e.g. garbled OCR, missing characters, words split across spans) rather than
from the recognizers.

Usage::

    python extract_text.py input/sample.pdf
    python extract_text.py input/sample.pdf --language es
    python extract_text.py input/sample.pdf --output output/sample_extracted.txt
"""

from __future__ import annotations

import argparse
import io
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image

from src.pdf_redactor import LANGUAGE_CONFIG, PDFRedactor


def dump_pdf_text(pdf_path: Path, language: str, mode: str) -> str:
    """Return the text (page layer + image OCR) the redactor would analyze."""
    redactor = PDFRedactor(mode=mode, language=language)
    doc = fitz.open(str(pdf_path))
    sections: list[str] = []

    try:
        for page in doc:
            page_no = page.number + 1

            full_text, _ = redactor._extract_page_text(page)
            sections.append(
                f"===== PAGE {page_no} — TEXT LAYER =====\n"
                f"{full_text if full_text.strip() else '(no text layer)'}"
            )

            processed_xrefs: set[int] = set()
            for img_info in page.get_images(full=True):
                xref = img_info[0]
                if xref in processed_xrefs:
                    continue
                processed_xrefs.add(xref)

                try:
                    img_data = doc.extract_image(xref)
                    pil_image = Image.open(io.BytesIO(img_data["image"]))
                except (OSError, KeyError, RuntimeError) as exc:
                    sections.append(
                        f"----- PAGE {page_no} — IMAGE xref {xref}: "
                        f"unreadable ({exc}) -----"
                    )
                    continue

                try:
                    _, ocr_text = redactor.image_analyzer.analyze(
                        pil_image,
                        ocr_kwargs={"lang": redactor.tesseract_lang},
                        language=redactor.language,
                    )
                except Exception as exc:  # noqa: BLE001 - mirror redactor: log and skip
                    sections.append(
                        f"----- PAGE {page_no} — IMAGE xref {xref}: "
                        f"OCR failed ({exc}) -----"
                    )
                    pil_image.close()
                    continue

                pil_image.close()
                sections.append(
                    f"----- PAGE {page_no} — IMAGE xref {xref} (OCR) -----\n"
                    f"{ocr_text if ocr_text.strip() else '(no OCR text)'}"
                )
    finally:
        doc.close()

    return "\n\n".join(sections)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("pdf", type=Path, help="Path to the input PDF.")
    parser.add_argument(
        "--language",
        default="en",
        choices=sorted(LANGUAGE_CONFIG),
        help="Language code for text analysis and OCR (default: en).",
    )
    parser.add_argument(
        "--mode",
        default="simple",
        choices=["simple", "hybrid", "llm"],
        help=(
            "Analyzer mode to build (default: simple). Extraction is identical "
            "across modes; simple avoids loading the LLM recognizer."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help=(
            "Where to write the extracted text. Defaults to "
            "output/<pdf-stem>_extracted.txt."
        ),
    )
    args = parser.parse_args()

    if not args.pdf.is_file():
        parser.error(f"PDF not found: {args.pdf}")

    output = args.output or Path("output") / f"{args.pdf.stem}_extracted.txt"
    output.parent.mkdir(parents=True, exist_ok=True)

    text = dump_pdf_text(args.pdf, language=args.language, mode=args.mode)

    output.write_text(text, encoding="utf-8")
    print(text)
    print(f"\n[extract_text] wrote {output}")


if __name__ == "__main__":
    main()
