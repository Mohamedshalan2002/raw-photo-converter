# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build spec for the RAW photo converter.

Notes on the tricky parts:
  * pillow_heif ships libheif as a DLL with a hashed name sitting next to the
    package (e.g. libheif-30fc1b...dll) plus a compiled _pillow_heif extension.
    Neither is found automatically, so both are collected explicitly.
  * rawpy ships raw_r.dll and vcomp140.dll beside its package.
  * tkinterdnd2 keeps its Tcl/Tk "tkdnd" payload in a data directory.
"""

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

PROJECT_ROOT = Path(SPECPATH).resolve()
SITE = Path(sys.prefix) / "Lib" / "site-packages"

datas = []
binaries = []
hiddenimports = [
    "PIL._tkinter_finder",
    "pillow_heif",
    "pillow_heif.as_plugin",
    "piexif",
    "exifread",
    "tkinterdnd2",
]

# --- pillow_heif: python package + hashed libheif DLL ----------------------
heif_pkg = SITE / "pillow_heif"
if heif_pkg.is_dir():
    datas += [(str(heif_pkg), "pillow_heif")]
for dll in SITE.glob("libheif*.dll"):
    binaries.append((str(dll), "."))
for pyd in SITE.glob("_pillow_heif*.pyd"):
    binaries.append((str(pyd), "."))

# --- rawpy ------------------------------------------------------------------
rawpy_pkg = SITE / "rawpy"
if rawpy_pkg.is_dir():
    datas += [(str(rawpy_pkg), "rawpy")]
    for name in ("raw_r.dll", "vcomp140.dll"):
        candidate = rawpy_pkg / name
        if candidate.exists():
            binaries.append((str(candidate), "rawpy"))
    for pyd in rawpy_pkg.glob("_rawpy*.pyd"):
        binaries.append((str(pyd), "rawpy"))

# --- tkinterdnd2 (drag & drop payload) -------------------------------------
datas += collect_data_files("tkinterdnd2")
binaries += collect_dynamic_libs("tkinterdnd2")

a = Analysis(
    [str(PROJECT_ROOT / "app.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "matplotlib", "numpy.testing", "pytest", "setuptools", "pip",
        "pandas", "scipy", "PyQt5", "PySide2", "PySide6", "IPython",
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="raw-photo-converter",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
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
    name="raw-photo-converter",
)
