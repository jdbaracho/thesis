"""Minimal reproducer for BasicLangExtractRecognizer (English only).

Purpose
-------
Isolate the LangExtract-backed LLM recognizer from the rest of the
`PDFRedactor` pipeline (PyMuPDF, OCR, image analyzer, multi-language
NLP engine, custom conflict resolution, etc.) so LLM-side problems
(Ollama connectivity, prompt/examples loading, model output parsing,
timeouts, empty results, ...) can be diagnosed in isolation.

Run
---
    python -m tutorial.basic_langextract

Prerequisites
-------------
- Ollama reachable at the URL declared in `src/config/ollama_config.en.yaml`
  (default in that file: `http://ollama:11434`; if you're running locally,
  edit it to `http://localhost:11434`).
- The model referenced in that config pulled: `ollama pull gemma3:12b`.
- English spaCy model: `python -m spacy download en_core_web_lg`.
"""

from pathlib import Path

from presidio_analyzer.predefined_recognizers.third_party.basic_langextract_recognizer import (
    BasicLangExtractRecognizer,
)


# Reuse the exact English config the real pipeline uses.
CONFIG_PATH = (
    Path(__file__).resolve().parent.parent
    / "src" / "config" / "ollama_config.en.yaml"
)

SAMPLE_TEXT = (
    "My name is John Smith and I live in New York. "
    "You can reach me at john.smith@example.com or (555) 123-4567. "
    "My SSN is 123-45-6789."
)


def main() -> None:
    lx_recognizer = BasicLangExtractRecognizer(
        config_path=str(CONFIG_PATH),
        supported_language="en",
    )

    print(f"Config: {CONFIG_PATH}")
    print(f"Model:    {lx_recognizer.model_id}")
    print(f"Provider: {lx_recognizer.provider}")
    print(f"Provider kwargs: {lx_recognizer.provider_kwargs}")
    print()
    print("Input text:")
    print(SAMPLE_TEXT)
    print()

    results = lx_recognizer.analyze(text=SAMPLE_TEXT)

    print(f"Got {len(results)} result(s):")
    for r in results:
        snippet = SAMPLE_TEXT[r.start:r.end]
        print(
            f"  - {r.entity_type:15s} "
            f"score={r.score:.2f} "
            f"[{r.start}:{r.end}] {snippet!r} "
            f"(recognizer={r.analysis_explanation.recognizer if r.analysis_explanation else '?'})"
        )


if __name__ == "__main__":
    main()
