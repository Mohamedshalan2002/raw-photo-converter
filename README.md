# RAW Photo Converter

Turn camera RAW files into ordinary photos you can drop straight into an iPhone — as **HEIC** or **JPG** — without losing the shooting data (date, camera, lens, exposure, GPS).

Comes as a small Windows GUI app with drag & drop, and also runs from the command line for batch jobs.

---

## Features

- **Reads RAW from almost any camera:** NEF/NRW · CR2/CR3/CRW · ARW/SRF/SR2 · DNG · ORF · RW2 · RAF · PEF · SRW · RAW · RWL · MRW · X3F · IIQ · MOS · ERF · KDC · DCR · MEF · 3FR · FFF · PTX · RWZ · TIF
- **Two output formats:** HEIC (iPhone native, roughly half the size) or JPG (opens anywhere)
- **Keeps the shooting data:** date taken, camera make/model, lens, shutter/aperture/ISO, GPS — plus the output file's own timestamp is set to the capture date, so library sorting stays correct
- **Auto-rotates** by the camera's orientation, including the awkward 90° portrait cases
- **Three quality levels:** full resolution, 4032 px (iPhone width), or 2560 px
- **Never overwrites:** a name clash becomes `photo-1.heic`
- **No install, no internet:** a single self-contained folder

---

## Quick start

### Use the built app

1. Open the `dist/raw-photo-converter` folder.
2. Double-click `raw-photo-converter.exe`.
3. Drag photos or folders onto the big drop area (or use **اختيار ملفات** / *Choose files*).
4. Pick a format and quality, then press **ابدأ التحويل** / *Start*.

> The `.exe` needs the `_internal` folder next to it — to move it, move the whole folder (or make a shortcut).

### Run from source

```bash
pip install rawpy pillow pillow-heif piexif exifread tkinterdnd2
python app.py           # GUI
```

Python 3.13 with Tk 8.6 is what this was built and tested on. `tkinterdnd2` is optional — without it the app still works, you just use the file picker instead of dragging.

---

## Command line

The same executable doubles as a batch converter:

```bash
# whole folder to HEIC, balanced quality
raw-converter "D:\Photos" -o "D:\Photos\Converted" -f heic -q balanced

# specific files to JPG
raw-converter "D:\a.NEF" "D:\b.CR2" -f jpg -q small
```

| Flag | Values | Meaning |
| --- | --- | --- |
| `-f`, `--format` | `heic` \| `jpg` | Output format (default `heic`) |
| `-q`, `--quality` | `best` \| `balanced` \| `small` | Resolution/quality preset (default `balanced`) |
| `-o`, `--out` | path | Output folder (default: `Converted` beside each input) |

Return codes: `0` all converted, `2` some failed, `1` nothing supported found.

---

## Quality presets

| Preset | Longest side | Typical output size |
| --- | --- | --- |
| `best` | unchanged | ~5–14 MB per photo |
| `balanced` | 4032 px | ~0.7–3.4 MB per photo |
| `small` | 2560 px | ~0.3–0.8 MB per photo |

Measured on 11 RAW files from 10 cameras (224 MB in): `balanced` produced 17 MB as HEIC / 16.6 MB as JPG.

### Why these numbers

HEIC encoding uses `x265`. Its default preset takes ~12–15 s for a 24 MP frame, which makes a batch painful. Setting the encoder to `ultrafast` cuts that to ~1.7 s at the same file size and quality (measured within ~0.02 dB PSNR at matched bitrate), so that is what the converter uses.

HEIC only wins at lower bitrates; at very high quality JPG is marginally more efficient. `balanced` and `small` therefore favour HEIC, and `best` produces near-identical sizes in both formats.

---

## What happens to the EXIF

The camera's own standard EXIF is carried across — date taken, make, model, lens, shutter, aperture, ISO, GPS, orientation — and reset correctly for the new pixel dimensions.

Two things are deliberately **dropped**:

- **MakerNotes.** Their internal byte offsets point into the original RAW layout, so they would be corrupt after re-encoding, and they add 30–125 KB of unusable data per file.
- **The embedded thumbnail.** It shows the RAW preview, which no longer matches the re-encoded pixels.

A file that had no camera metadata at all (say a screenshot) gets no EXIF block written, rather than an empty one — which some readers flag as corrupt.

---

## Verified behaviour

Tested end to end on 11 files from 10 cameras — Nikon, Canon, Sony, Fuji, Panasonic, Olympus, Pentax, Samsung, OnePlus — across NEF, CR2, ARW, RAF, RW2, ORF, PEF, SRW and DNG:

- all outputs decode cleanly
- capture date, camera model and lens survive in every file
- output file timestamps match the capture dates
- no EXIF integrity warnings
- portrait shots come out upright

---

## Building the executable

```bash
pip install pyinstaller
pyinstaller --clean build.spec
```

`build.spec` handles the fiddly parts: `pillow_heif` ships libheif as a hash-named DLL, `rawpy` ships `raw_r.dll`, and `tkinterdnd2` keeps a Tcl/Tk payload in a data directory — none of which PyInstaller finds on its own.

---

## Project layout

| File | Purpose |
| --- | --- |
| `core.py` | Conversion engine: RAW decoding, quality presets, EXIF handling, CLI |
| `app.py` | Tkinter GUI, drag & drop, worker thread |
| `build.spec` | PyInstaller build configuration |
| `اقرأني.md` | Full Arabic user guide |

---

## Notes and limits

- **Windows only as shipped.** The code is portable (Tkinter + Pillow + libraw), but the build here targets Windows x64.
- **Unsigned binary.** Windows SmartScreen will warn on first run: *More info → Run anyway*.
- **HEIC needs an HEVC encoder.** If the build lacks one, the app says so and falls back to JPG automatically.
- **Tested on Python 3.13.0 with Tk 8.6.** Other versions should work but are untested.

## License

MIT — see [LICENSE](LICENSE).
