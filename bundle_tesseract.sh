#!/usr/bin/env bash
# Bundle the Homebrew tesseract binary + dylibs + tessdata into ./tesseract_bin/
# so PyInstaller can include it. Run this once before building.
set -euo pipefail

cd "$(dirname "$0")"

TESS_BIN="$(command -v tesseract || true)"
if [[ -z "$TESS_BIN" ]]; then
    echo "ERROR: tesseract not found in PATH. Install with: brew install tesseract"
    exit 1
fi

if ! command -v dylibbundler >/dev/null 2>&1; then
    echo "ERROR: dylibbundler not found. Install with: brew install dylibbundler"
    exit 1
fi

BREW_PREFIX="$(brew --prefix tesseract)"
TESSDATA_SRC="$BREW_PREFIX/share/tessdata"

OUT="tesseract_bin"
rm -rf "$OUT"
mkdir -p "$OUT/libs" "$OUT/tessdata"

# Copy binary
cp "$TESS_BIN" "$OUT/tesseract"
chmod +x "$OUT/tesseract"

# Copy tessdata
cp -R "$TESSDATA_SRC"/. "$OUT/tessdata"/

# Sanity check: the three languages the redactor supports (English, Spanish,
# European Portuguese) must all have traineddata files. Homebrew's `tesseract`
# formula ships every language by default, so a missing file usually means a
# broken install or a stripped-down tessdata directory.
missing=()
for lang in eng spa por; do
    if [[ ! -f "$OUT/tessdata/${lang}.traineddata" ]]; then
        missing+=("$lang")
    fi
done
if (( ${#missing[@]} > 0 )); then
    echo "ERROR: missing tessdata for: ${missing[*]}" >&2
    echo "       Expected files in: $TESSDATA_SRC" >&2
    echo "       Reinstall tesseract or add the traineddata manually." >&2
    exit 1
fi

# Bundle dylibs and rewrite load paths so the binary uses @executable_path/libs/...
dylibbundler -of -b \
    -x "$OUT/tesseract" \
    -d "$OUT/libs" \
    -p "@executable_path/libs/" >/dev/null

echo "Tesseract bundle ready in: $OUT"
ls "$OUT"
