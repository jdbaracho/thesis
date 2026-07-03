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

# Bundle dylibs and rewrite load paths so the binary uses @executable_path/libs/...
dylibbundler -of -b \
    -x "$OUT/tesseract" \
    -d "$OUT/libs" \
    -p "@executable_path/libs/" >/dev/null

echo "Tesseract bundle ready in: $OUT"
ls "$OUT"
