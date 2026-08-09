# PDF Redactor API

A FastAPI service that wraps [`PDFRedactor`](src/pdf_redactor.py). Clients upload
one or more PDFs, the server redacts each one (Presidio + optional
LangExtract LLM pass) and returns a ZIP containing every redacted PDF and its
`.xlsx` translation table.

The API is **job-based**: submit files → get a `job_id` → poll status → download
the result ZIP.

---

## 1. Install

Tested on Linux (Debian/Ubuntu) and macOS (Apple Silicon). Windows is not
supported directly — use WSL2 with the Debian/Ubuntu instructions below.

### 1.0 Prerequisites

- **Git**
  ```bash
  # Debian / Ubuntu
  sudo apt install git
  # macOS (via Homebrew, if not already present)
  brew install git
  ```
- **Python 3.10 or newer**, with the `venv` and `pip` modules. On a fresh
  Ubuntu install the standalone modules ship in separate packages:
  ```bash
  # Debian / Ubuntu
  sudo apt install python3 python3-venv python3-pip
  # macOS ships a working Python, but a Homebrew install is more predictable:
  brew install python
  ```

### 1.1 Clone the repository

```bash
git clone <this-repo-url> pdf-redactor
cd pdf-redactor
```

### 1.2 System dependencies

Two things `pip` cannot install for you:

- **Tesseract OCR** — needed by `pytesseract` for redacting scanned/image PDFs.
  The base package covers English; install the Spanish and Portuguese
  language packs too so the `language=es`/`pt` jobs OCR correctly.
  ```bash
  # Debian / Ubuntu
  sudo apt install tesseract-ocr tesseract-ocr-eng tesseract-ocr-spa tesseract-ocr-por
  # macOS (Homebrew's `tesseract` formula ships only eng/osd; language packs
  # live in the separate `tesseract-lang` formula)
  brew install tesseract tesseract-lang
  ```
  For European Portuguese, Tesseract's generic `por` traineddata is used
  (`por_BR` is Brazilian; not needed here).
- **Ollama** (only if you plan to run with `use_llm=true`) — the LangExtract
  recognizer talks to a local LLM.
  ```bash
  # Linux
  curl -fsSL https://ollama.com/install.sh | sh
  # macOS (Homebrew, or download the app from https://ollama.com)
  brew install ollama
  ```
  Then pull the model the config points at:
  ```bash
  ollama pull gemma3:12b
  ```
  Adjust `model_url` in the per-language configs under
  [`src/config/`](src/config/) (`ollama_config.en.yaml`,
  `ollama_config.es.yaml`, `ollama_config.pt.yaml`) if Ollama is not
  reachable at `http://ollama:11434` (e.g. use `http://localhost:11434`
  for a local install).

### 1.3 Python dependencies

Create and activate a virtual environment, then install everything:

```bash
# from the repo root
python3 -m venv .venv
source .venv/bin/activate   # Linux / macOS

pip install --upgrade pip
pip install -r requirements.txt
python -m spacy download en_core_web_lg
python -m spacy download es_core_news_lg
python -m spacy download pt_core_news_lg
```

`requirements.txt` covers both the HTTP layer (FastAPI, uvicorn,
python-multipart) and the redactor itself (Presidio, PyMuPDF, LangExtract,
openpyxl, …). The three spaCy models (English, Spanish, Portuguese) are
separate downloads because they are not regular PyPI packages; the analyzer
is built once at startup with all three loaded and picks the right one per
job based on the `language` form field. spaCy ships a single Portuguese
model (`pt_core_news_lg`) that covers both European and Brazilian variants.

The roughly ~2 GB of spaCy models (~570 MB each) are downloaded from
PyPI; expect a few minutes on a slow connection.

> **PyInstaller bundling.** [`bundle_tesseract.sh`](bundle_tesseract.sh)
> vendors Tesseract into `tesseract_bin/` for [`PDFRedactor.spec`](PDFRedactor.spec).
> It uses Homebrew and `dylibbundler` and is **macOS-only**; on Linux you
> would need a distro-specific packaging step (out of scope here).

### 1.4 Verify the install

Run these checks after 1.1–1.3, especially to confirm all **three languages**
(`en`, `es`, `pt`) are wired up end-to-end. Activate the venv first
(`source .venv/bin/activate`).

- **Python packages** — everything from `requirements.txt` importable, no
  broken dependency chains:
  ```bash
  pip check
  python -c "
  import fastapi, uvicorn, multipart, fitz, PIL, openpyxl, pydantic
  import pytesseract, presidio_analyzer, presidio_anonymizer
  import presidio_image_redactor, langextract, yaml, jinja2, spacy
  print('OK: all packages import')
  "
  ```

- **spaCy models** (one per language, used for NER):
  ```bash
  python -c "
  import spacy
  for m in ['en_core_web_lg', 'es_core_news_lg', 'pt_core_news_lg']:
      spacy.load(m)
      print('OK', m)
  "
  ```
  If a model is `MISSING`/raises `[E050]`, install it:
  ```bash
  python -m spacy download <model-name>   # e.g. es_core_news_lg
  ```

- **Tesseract OCR + language packs** (needed for scanned/image PDFs):
  ```bash
  tesseract --list-langs
  ```
  You should see `eng`, `spa`, `por` (plus `osd`). On Debian/Ubuntu the base
  `tesseract-ocr` package only ships `eng`+`osd` — the `spa`/`por` traineddata
  come from separate packages, so a missing `spa`/`por` here is common even
  after "install" step 1.2. Fix with:
  ```bash
  # Debian / Ubuntu
  sudo apt install tesseract-ocr-spa tesseract-ocr-por
  # macOS (Homebrew's base `tesseract` formula lacks language packs)
  brew install tesseract-lang
  ```

- **Ollama + LLM model** (only needed if you run with `use_llm=true`):
  ```bash
  ollama list
  ```
  Confirm the model referenced by `model_url`/model name in
  [`src/config/`](src/config/) (`ollama_config.en.yaml`, `.es.yaml`,
  `.pt.yaml` — currently `gemma3:12b`) is in the list. If not:
  ```bash
  ollama pull gemma3:12b
  ```
  If `ollama list` fails to connect, make sure the Ollama service is running
  (`ollama serve`, or check `systemctl status ollama`) and that the
  `model_url` in each per-language config actually points at it (default
  `http://ollama:11434`; use `http://localhost:11434` for a local install).

---

## 2. Run

```bash
# From the repo root:
python -m src.job_controller
# or, equivalently:
uvicorn src.job_controller:app --host 127.0.0.1 --port 8000
```

Defaults: `http://127.0.0.1:8000`. Configuration via environment variables:

| Var                            | Default     | Purpose                                                   |
| ------------------------------ | ----------- | --------------------------------------------------------- |
| `HOST`                         | `127.0.0.1` | Bind address                                              |
| `PORT`                         | `8000`      | Bind port                                                 |
| `PDF_REDACTOR_API_WORKERS`     | `1`         | Concurrent redaction workers (LLM contention if you raise it) |
| `PDF_REDACTOR_API_LOG_LEVEL`   | `INFO`      | Logging level                                             |
| `PDF_REDACTOR_WARNINGS`        | *(unset)*   | Set to any non-empty value to surface every Python warning (deprecations, resource leaks, third-party notices) via the logger. Use when upgrading dependencies. Overrides the narrow suppressions in [`src/warning_filters.py`](src/warning_filters.py). |

Example with a custom port and log level:

```bash
HOST=0.0.0.0 PORT=8765 PDF_REDACTOR_API_LOG_LEVEL=WARNING python -m src.job_controller
```

Interactive docs live at `http://<host>:<port>/docs` (Swagger UI) and
`/redoc`, but the recipes below cover the practical workflows.

---

## 3. Endpoints at a glance

| Method | Path                    | Purpose                                            |
| ------ | ----------------------- | -------------------------------------------------- |
| GET    | `/health`               | Liveness probe                                     |
| POST   | `/jobs`                 | Upload PDFs + start redaction. Returns `202` + job |
| GET    | `/jobs`                 | List every tracked job                             |
| DELETE | `/jobs`                 | Delete every tracked job and its files             |
| GET    | `/jobs/{job_id}`        | Job status                                         |
| GET    | `/jobs/{job_id}/result` | Download `application/zip` when completed          |
| DELETE | `/jobs/{job_id}`        | Delete job and its files                           |

### Job status values

`pending → running → completed`   (or `→ failed` on unrecoverable errors)

### Job JSON shape

```json
{
  "id": "03cd65f0ea25440ead1109c6f17a2dc1",
  "status": "completed",
  "file_count": 2,
  "created_at": "2026-07-03T03:04:08.721208Z",
  "started_at": "2026-07-03T03:04:08.722062Z",
  "finished_at": "2026-07-03T03:04:08.749193Z",
  "error": null,
  "result_url": "/jobs/03cd65f0ea25440ead1109c6f17a2dc1/result"
}
```

---

## 4. `curl` recipes

Assume `BASE=http://127.0.0.1:8000`.

### 4.1 Health check

```bash
curl -sS "$BASE/health"
# {"status":"ok"}
```

### 4.2 Submit a single PDF

`use_llm` (default `true`) and `language` (default `"en"`) are optional form fields.

```bash
curl -sS -X POST "$BASE/jobs" \
  -F "files=@input/sample.pdf;type=application/pdf" \
  -F "use_llm=true" \
  -F "language=en"
```

Response (`202 Accepted`):

```json
{ "id": "abc...", "status": "pending", "file_count": 1, "result_url": null, ... }
```

### 4.3 Submit several PDFs at once

Repeat the `-F "files=@…"` flag once per file.

```bash
curl -sS -X POST "$BASE/jobs" \
  -F "files=@input/sample.pdf;type=application/pdf" \
  -F "files=@input/text_image.pdf;type=application/pdf" \
  -F "use_llm=true" \
  -F "language=en"
```

Files sharing the same name are automatically de-duplicated in the ZIP
(`sample_redacted.pdf`, `sample_2_redacted.pdf`, …).

### 4.4 Poll status

```bash
JOB_ID=abc123...
curl -sS "$BASE/jobs/$JOB_ID"
```

Response mirrors the JSON shape above. Poll until `status` is `completed`
(or `failed`).

### 4.5 Download the ZIP

```bash
curl -sS -o result.zip -O -J "$BASE/jobs/$JOB_ID/result"
unzip -l result.zip
```

The archive is flat and contains, per input PDF:

- `<stem>_redacted.pdf` — the redacted document
- `<stem>_redacted.xlsx` — the translation table (one row per detected entity)

If a specific input PDF failed to open/redact, you'll instead get
`<stem>_error.txt` with the traceback — the batch is never dropped for one bad
file.

### 4.6 Delete a job

```bash
curl -sS -X DELETE "$BASE/jobs/$JOB_ID"
# HTTP 204, no body. Workdir removed from disk.
```

### 4.7 List every job

```bash
curl -sS "$BASE/jobs"
# [ { "id": "...", "status": "completed", ... }, ... ]
```

### 4.8 Delete every job

```bash
curl -sS -X DELETE "$BASE/jobs"
# {"deleted": 3}
```

### 4.9 End-to-end one-liner

```bash
BASE=http://127.0.0.1:8000
JOB_ID=$(curl -sS -X POST "$BASE/jobs" \
  -F "files=@input/sample.pdf;type=application/pdf" \
  -F "use_llm=false" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')

# poll every 2 seconds
until [ "$(curl -sS "$BASE/jobs/$JOB_ID" \
        | python3 -c 'import sys,json;print(json.load(sys.stdin)["status"])')" \
        = "completed" ]; do sleep 2; done

curl -sS -O -J "$BASE/jobs/$JOB_ID/result"
```

---

## 5. Python client example

```python
import time
import requests

BASE = "http://127.0.0.1:8000"

with open("input/sample.pdf", "rb") as f1, open("input/text_image.pdf", "rb") as f2:
    r = requests.post(
        f"{BASE}/jobs",
        files=[
            ("files", ("sample.pdf",     f1, "application/pdf")),
            ("files", ("text_image.pdf", f2, "application/pdf")),
        ],
        data={"use_llm": "true", "language": "en"},
    )
r.raise_for_status()
job_id = r.json()["id"]

# poll
while True:
    status = requests.get(f"{BASE}/jobs/{job_id}").json()
    if status["status"] in ("completed", "failed"):
        break
    time.sleep(1)

if status["status"] == "failed":
    raise RuntimeError(status["error"])

# download
zip_bytes = requests.get(f"{BASE}/jobs/{job_id}/result").content
with open(f"{job_id}.zip", "wb") as out:
    out.write(zip_bytes)
```

---

## 6. Request parameters

| Field       | Type            | Default | Notes                                                                   |
| ----------- | --------------- | ------- | ----------------------------------------------------------------------- |
| `files`     | `UploadFile[]`  | —       | One or more PDFs. `.pdf` extension **or** `application/pdf` MIME type   |
| `language`  | `str`           | `"en"`  | One of `en`, `es`, `pt` (European Portuguese). Selects the spaCy model, the Tesseract OCR dictionary, and the LangExtract prompt/examples. |
| `use_llm`   | `bool`          | `true`  | Toggle the `BasicLangExtractRecognizer` LLM-backed pass                 |

## 7. Status codes

| Code | When                                                              |
| ---- | ----------------------------------------------------------------- |
| 202  | Job accepted                                                      |
| 204  | Job deleted                                                       |
| 400  | Empty file list                                                   |
| 404  | Unknown `job_id`                                                  |
| 409  | Result requested but job not `completed`                          |
| 410  | Result file missing on disk (e.g. server restart cleared workdir) |
| 415  | Upload isn't a PDF                                                |
| 422  | Malformed multipart body                                          |
| 500  | Failed job — response body includes `detail` with the error       |

---

## 8. Where the files live

Every job gets a workdir at `output/api_jobs/<job_id>/` containing:

```
uploads/000/<original_name>.pdf
uploads/001/<original_name>.pdf
redacted/<stem>_redacted.pdf
redacted/<stem>_redacted.xlsx
result.zip
```

`DELETE /jobs/{job_id}` removes the entire directory. Job state is
**in-memory only**, so restarting the server clears status entries even though
leftover workdirs stay on disk (delete manually if needed).

---

## 9. Notes & limits

- **Concurrency**: default is a single worker thread. Bump
  `PDF_REDACTOR_API_WORKERS` only if your LLM backend can handle parallel
  calls (Ollama typically cannot).
- **Auth**: none. Bind to `127.0.0.1` unless you add your own reverse proxy /
  auth layer.
- **Upload size**: FastAPI's `UploadFile` spools past ~1MB to disk, so
  ≤100 MB PDFs are fine. Gigabyte-scale uploads would need chunked transfer.
- **`use_llm=false`** is dramatically faster (skips the LangExtract step) but
  has weaker recall.

---

## 10. Upgrading `presidio-anonymizer`

`presidio-anonymizer` is **pinned** in [`requirements.txt`](requirements.txt)
(currently `==2.2.363`). This is intentional: `resolve_conflicts()` in
[`src/presidio_extensions/presidio_utils.py`](src/presidio_extensions/presidio_utils.py)
calls three underscore-prefixed methods on `AnonymizerEngine`
(`_copy_recognizer_results`, `_remove_conflicts_and_get_text_manipulation_data`,
`_merge_entities_with_spaces_between`). Presidio does not expose a public
equivalent, so any upstream refactor can silently break conflict resolution.

An import-time guard in `presidio_utils.py` emits a `DeprecationWarning` when
the installed version differs from the pinned/tested one, so you don't have to
remember this on your own.

**To upgrade:**

1. Bump the pin in [`requirements.txt`](requirements.txt) and reinstall:
   ```bash
   pip install -r requirements.txt --upgrade
   ```
2. Run the app once — the guard will warn:
   ```
   DeprecationWarning: presidio-anonymizer <new-version> differs from tested 2.2.363; private API calls in resolve_conflicts() may have moved or changed signature.
   ```
3. Open
   [`src/presidio_extensions/presidio_utils.py`](src/presidio_extensions/presidio_utils.py)
   and confirm the three private methods still exist on `AnonymizerEngine`
   with the same signatures. Quick check:
   ```bash
   python -c "
   from presidio_anonymizer import AnonymizerEngine
   a = AnonymizerEngine()
   for name in ('_copy_recognizer_results',
                '_remove_conflicts_and_get_text_manipulation_data',
                '_merge_entities_with_spaces_between'):
       print(name, '->', hasattr(a, name))
   "
   ```
   All three must print `True`. If any prints `False`, the API has changed —
   check the [Presidio changelog](https://github.com/microsoft/presidio/blob/main/CHANGELOG.md)
   and either adapt `resolve_conflicts()` to the new API or stay on the
   previous version.
4. Redact a known-good PDF (any file with overlapping entity spans, e.g. a
   name near a phone number) and confirm the translation table looks right.
5. Update `_TESTED_ANONYMIZER_VERSION` in
   [`src/presidio_extensions/presidio_utils.py`](src/presidio_extensions/presidio_utils.py)
   to the new version so the guard falls silent again.

