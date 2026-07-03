"""Demo driver: redact a PDF using `PDFRedactor` and export the results.

Produces two files next to each other:
- ``output/text_image_redacted.pdf`` — the redacted document.
- ``output/text_image_redacted.xlsx`` — the translation table.
"""

import os

import fitz  # PyMuPDF
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font

from pdf_redactor import PDFRedactor


def save_translation_table_xlsx(translation_table: dict, output_xlsx: str) -> None:
    """Persist ``translation_table`` as an Excel workbook.

    Each entity_text occupies one (merged) row, with its entity_type/score
    pairs laid out as sub-rows beside it.
    """
    wb = Workbook()
    ws = wb.active
    ws.title = "translation_table"

    headers = ["entity_text", "id", "entity_type", "score"]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)

    center = Alignment(horizontal="center", vertical="center")
    row_idx = 2
    for entity_text, entry in translation_table.items():
        scores = entry["scores"]
        if not scores:
            continue
        start_row = row_idx
        for entity_type, score in scores.items():
            ws.cell(row=row_idx, column=1, value=entity_text)
            ws.cell(row=row_idx, column=2, value=entry["id"])
            ws.cell(row=row_idx, column=3, value=entity_type)
            ws.cell(row=row_idx, column=4, value=score)
            row_idx += 1
        end_row = row_idx - 1
        if end_row > start_row:
            ws.merge_cells(start_row=start_row, start_column=1,
                           end_row=end_row, end_column=1)
            ws.merge_cells(start_row=start_row, start_column=2,
                           end_row=end_row, end_column=2)
        ws.cell(row=start_row, column=1).alignment = center
        ws.cell(row=start_row, column=2).alignment = center

    wb.save(output_xlsx)


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
