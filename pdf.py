"""Demo driver: redact a PDF using `PDFRedactor` and export the results.

Produces two files next to each other:
- ``output/text_image_redacted.pdf`` — the redacted document.
- ``output/text_image_redacted.xlsx`` — the translation table.
"""

import os

import fitz  # PyMuPDF

from src.pdf_redactor import PDFRedactor
from src.translation_table import save_translation_table_xlsx


if __name__ == "__main__":
    input_pdf = "input/text_image.pdf"
    output_pdf = "output/text_image_redacted.pdf"
    output_xlsx = os.path.splitext(output_pdf)[0] + ".xlsx"

    doc = fitz.open(input_pdf)
    redactor = PDFRedactor(use_llm=True)
    doc, translation_table = redactor.redact(doc)

    os.makedirs(os.path.dirname(output_pdf), exist_ok=True)
    doc.save(output_pdf)
    save_translation_table_xlsx(translation_table, output_xlsx)
