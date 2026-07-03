# PyInstaller spec for the PDF Redactor GUI.
# Build with:  pyinstaller PDFRedactor.spec
# Output:      dist/PDF Redactor.app   (macOS)  or  dist/PDF Redactor/  (other OS)

from PyInstaller.utils.hooks import collect_all, collect_submodules
import os

datas = []
binaries = []
hiddenimports = []

# Bundle the prepared tesseract folder (binary + dylibs + tessdata).
# Run ./bundle_tesseract.sh once before building.
if os.path.isdir("tesseract_bin"):
    datas.append(("tesseract_bin", "tesseract_bin"))
else:
    raise SystemExit(
        "tesseract_bin/ missing. Run ./bundle_tesseract.sh before building."
    )

# Bundle Presidio + spaCy + the English model so the app is self-contained.
for pkg in (
    "presidio_analyzer",
    "presidio_image_redactor",
    "presidio_anonymizer",
    "spacy",
    "thinc",
    "en_core_web_lg",
    "openpyxl",
):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

hiddenimports += collect_submodules("spacy")
hiddenimports += [
    "en_core_web_lg",
    "pytesseract",
    "openpyxl",
    "extensions",
    "extensions.custom_image_redactor",
]

# Include the local extensions package source (so relative imports work).
datas.append(("extensions", "extensions"))


a = Analysis(
    ["gui.py"],
    pathex=[os.path.abspath(".")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PDF Redactor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,        # no terminal window
    disable_windowed_traceback=False,
    argv_emulation=True,  # macOS: allow drag-and-drop onto the app
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="PDF Redactor",
)

app = BUNDLE(
    coll,
    name="PDF Redactor.app",
    icon=None,
    bundle_identifier="com.thesis.pdfredactor",
)
