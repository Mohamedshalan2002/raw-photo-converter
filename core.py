"""محرّك تحويل صور الكاميرا (RAW) إلى صور عادية (HEIC / JPG).

Engine that turns camera RAW files into ordinary photos you can put on an iPhone.
No GUI here — see app.py for the window, or run this file with --cli for batch use.
"""

from __future__ import annotations

import io
import os
import struct
import sys
from dataclasses import dataclass, field
from datetime import datetime

# --- libraw (rawpy) ---------------------------------------------------------
try:
    import rawpy
except Exception:  # pragma: no cover - reported to the UI instead
    rawpy = None

import exifread
import piexif
import pillow_heif
from PIL import Image, ImageCms, ImageOps

pillow_heif.register_heif_opener()

# HEVC encoding is very slow unless we relax the encoder preset; at these
# settings it is ~7x faster and produces the same file size at equal quality.
_HEIC_ENC = {"preset": "ultrafast"}

# Extensions we advertise. Content sniffing is used as well, so an odd
# extension still gets a fair chance.
RAW_EXTENSIONS = (
    ".nef", ".nrw", ".cr2", ".cr3", ".crw", ".arw", ".srf", ".sr2", ".dng",
    ".orf", ".rw2", ".raf", ".pef", ".srw", ".raw", ".rwl", ".mrw", ".x3f",
    ".3fr", ".fff", ".iiq", ".mos", ".erf", ".kdc", ".dcr", ".mef", ".ptx",
    ".rwz", ".srf", ".cap", ".tif", ".tiff",
)
PLAIN_EXTENSIONS = (".jpg", ".jpeg", ".png", ".bmp", ".webp", ".heic", ".heif", ".tif", ".tiff")
SUPPORTED_EXTENSIONS = tuple(sorted(set(RAW_EXTENSIONS + PLAIN_EXTENSIONS)))

# --- quality presets --------------------------------------------------------
# Tuned by measuring size/quality across 10 cameras (see the notes in the guide):
#   "best"     keeps original resolution, visually lossless.
#   "balanced" fits 4032px (iPhone native width) at roughly 1/5 the size.
#   "small"    fits 2560px, for sending or saving space.
PRESETS = {
    "best": {"max_px": None, "heic_q": 78, "jpg_q": 92, "jpg_subsampling": 0},
    "balanced": {"max_px": 4032, "heic_q": 58, "jpg_q": 88, "jpg_subsampling": 2},
    "small": {"max_px": 2560, "heic_q": 50, "jpg_q": 82, "jpg_subsampling": 2},
}
DEFAULT_PRESET = "balanced"

PRESET_LABELS_AR = {
    "best": "أفضل جودة (بدون تصغير)",
    "balanced": "متوازن — موصى به",
    "small": "حجم صغير",
}

FORMAT_LABELS_AR = {
    "heic": "HEIC — صيغة الآيفون (حجم أصغر)",
    "jpg": "JPG — تفتح على أي جهاز",
}


_HEIF_OK: bool | None = None


def heif_available() -> bool:
    """True when this machine can actually write HEIC files.

    Checked once: the plugin must be registered with Pillow *and* libheif must
    expose a real HEVC encoder (a build without x265 can open HEIC but not save).
    """
    global _HEIF_OK
    if _HEIF_OK is not None:
        return _HEIF_OK
    try:
        if "HEIF" not in Image.SAVE:
            _HEIF_OK = False
            return _HEIF_OK
        info = pillow_heif.libheif_info() or {}
        encoders = info.get("encoders") or {}
        _HEIF_OK = any("x265" in str(k).lower() or "hevc" in str(v).lower()
                       for k, v in encoders.items())
    except Exception:
        _HEIF_OK = False
    return bool(_HEIF_OK)


def rawpy_available() -> bool:
    return rawpy is not None


# ---------------------------------------------------------------------------
# Format sniffing
# ---------------------------------------------------------------------------

_RAW_MAGIC = (
    b"FUJIFILMCCD-RAW",          # Fuji RAF
    b"II*\x00",                   # TIFF little endian (NEF, CR2, ARW, DNG, ...)
    b"MM\x00*",                   # TIFF big endian
    b"IIU\x00",                   # Panasonic RW2 (II + 0x55)
    b"IIRO",                     # Olympus ORF (little endian)
    b"MMOR",                     # Olympus ORF (big endian)
    b"IIRS",                     # Olympus ORF variant
    b"II\x1a\x00\x00\x00HEAPCCDR",  # Canon CRW
    b"FOVb",                     # Sigma X3F
    b"\x89PNG",                  # some DNGs are wrapped
)


def looks_like_raw(path: str) -> bool:
    """Guess from the first bytes whether libraw should be given a try."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(16)
    except OSError:
        return False
    return any(head.startswith(m) for m in _RAW_MAGIC)


def _is_plain_image(path: str) -> bool:
    ext = os.path.splitext(path)[1].lower()
    if ext in (".jpg", ".jpeg", ".png", ".bmp", ".webp", ".heic", ".heif"):
        return True
    try:
        with open(path, "rb") as fh:
            head = fh.read(12)
    except OSError:
        return False
    return head.startswith(b"\xff\xd8\xff") or head.startswith(b"\x89PNG")


# ---------------------------------------------------------------------------
# EXIF: take the camera's own block and carry it across unchanged
# ---------------------------------------------------------------------------

# Tags that describe the raw container / thumbnail and are meaningless (or
# actively misleading) once the pixels have been re-encoded.
_DROP_0TH = (34665, 34853, 330, 513, 514, 273, 279, 278, 259, 262, 258, 277, 284)
_DROP_EXIF = (37500, 40964, 41728, 41730)

# The camera's own date/make/model plus exposure data are what viewers and the
# iPhone photo library actually sort and display on.
_MEANINGFUL_0TH = (271, 272, 306)          # Make, Model, DateTime


def _load_exif_dict(path: str) -> dict | None:
    """Return a piexif-style dict for a RAW file, or None if unavailable."""
    try:
        return piexif.load(path)
    except Exception:
        pass
    # Fuji RAF is not a TIFF container: its EXIF lives in an embedded JPEG.
    blob = _raf_embedded_jpeg(path)
    if blob:
        try:
            return piexif.load(blob)
        except Exception:
            return None
    return None


def _raf_embedded_jpeg(path: str) -> bytes | None:
    """Pull the JPEG preview out of a Fuji RAF file."""
    try:
        with open(path, "rb") as fh:
            header = fh.read(0x64)
            if not header.startswith(b"FUJIFILMCCD-RAW"):
                return None
            offset = struct.unpack(">I", header[0x54:0x58])[0]
            length = struct.unpack(">I", header[0x58:0x5C])[0]
            if not (0 < length < 60_000_000) or offset <= 0:
                return None
            fh.seek(offset)
            return fh.read(length)
    except Exception:
        return None


def _exif_date(exif_dict: dict) -> str | None:
    for ifd, tag in (("Exif", piexif.ExifIFD.DateTimeOriginal),
                     ("Exif", piexif.ExifIFD.DateTimeDigitized),
                     ("0th", piexif.ImageIFD.DateTime)):
        value = exif_dict.get(ifd, {}).get(tag)
        if value:
            try:
                text = value.decode("ascii", "ignore") if isinstance(value, bytes) else str(value)
            except Exception:
                continue
            if len(text) >= 19:
                return text[:19]
    return None


def _fallback_exif_dict(path: str) -> dict | None:
    """Build a small EXIF block from exifread when the raw block cannot be dumped."""
    try:
        with open(path, "rb") as fh:
            tags = exifread.process_file(fh, details=False)
    except Exception:
        return None
    if not tags:
        return None

    def one(name):
        tag = tags.get(name)
        if tag is None:
            return None
        values = getattr(tag, "values", None)
        if values is not None and len(values) == 1:
            return values[0]
        return tag

    def as_ratio(value):
        if value is None:
            return None
        try:
            if isinstance(value, str) and "/" in value:
                num, den = value.split("/")
                return (int(num), int(den))
            if isinstance(value, tuple) and len(value) == 2:
                return (int(value[0]), int(value[1]))
            return (int(value), 1)
        except Exception:
            return None

    def as_bytes(value):
        if value is None:
            return None
        return str(value).strip().encode("ascii", "ignore") or None

    out = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}
    if (v := as_bytes(one("Image Make"))) is not None:
        out["0th"][piexif.ImageIFD.Make] = v
    if (v := as_bytes(one("Image Model"))) is not None:
        out["0th"][piexif.ImageIFD.Model] = v
    if (v := as_bytes(one("Image DateTime"))) is not None:
        out["0th"][piexif.ImageIFD.DateTime] = v[:19]
    if (v := as_ratio(one("EXIF FNumber"))) is not None:
        out["Exif"][piexif.ExifIFD.FNumber] = v
    if (v := as_ratio(one("EXIF ExposureTime"))) is not None:
        out["Exif"][piexif.ExifIFD.ExposureTime] = v
    if (v := as_ratio(one("EXIF FocalLength"))) is not None:
        out["Exif"][piexif.ExifIFD.FocalLength] = v
    try:
        if (v := one("EXIF ISOSpeedRatings")) is not None:
            out["Exif"][piexif.ExifIFD.ISOSpeedRatings] = int(str(v))
    except Exception:
        pass
    for src, dst in (("EXIF DateTimeOriginal", piexif.ExifIFD.DateTimeOriginal),
                     ("EXIF DateTimeDigitized", piexif.ExifIFD.DateTimeDigitized)):
        if (v := as_bytes(one(src))) is not None:
            out["Exif"][dst] = v[:19]
    if (v := as_bytes(one("EXIF LensModel"))) is not None:
        out["Exif"][piexif.ExifIFD.LensModel] = v
    return out


def build_exif(path: str, size: tuple[int, int]) -> bytes | None:
    """Return a ready-to-embed EXIF block with correct dimensions/orientation.

    The camera's own standard EXIF (date taken, body, lens, exposure, GPS) is
    preserved. MakerNotes are dropped: their internal offsets point into the
    original raw layout and would be corrupt after re-encoding, while adding
    well over a hundred kilobytes of unusable data.
    """
    data = _load_exif_dict(path)
    if data is None:
        data = _fallback_exif_dict(path)
    if data is None:
        return None

    for key in ("0th", "Exif", "GPS"):
        data.setdefault(key, {})
    for tag in _DROP_0TH:
        data["0th"].pop(tag, None)
    for tag in _DROP_EXIF:
        data["Exif"].pop(tag, None)

    width, height = int(size[0]), int(size[1])
    data["0th"][piexif.ImageIFD.Orientation] = 1
    data["0th"][piexif.ImageIFD.ImageWidth] = width
    data["0th"][piexif.ImageIFD.ImageLength] = height

    # PixelXDimension/PixelYDimension live in the Exif sub-IFD. Writing them
    # when the source had no Exif data at all leaves an Exif IFD holding
    # nothing but these two tags, which some readers flag as corrupt; the
    # dimensions in the 0th IFD above already carry the real numbers.
    if data["Exif"]:
        data["Exif"][piexif.ExifIFD.PixelXDimension] = width
        data["Exif"][piexif.ExifIFD.PixelYDimension] = height

    # No thumbnail: the raw preview no longer matches these pixels.
    data["1st"] = {}
    data["thumbnail"] = None

    # A file with no camera metadata of its own gets no EXIF block at all.
    has_content = bool(data["Exif"] or data["GPS"] or data["1st"]) or any(
        tag in data["0th"] for tag in _MEANINGFUL_0TH)
    if not has_content:
        return None

    try:
        return piexif.dump(data)
    except Exception:
        fallback = _fallback_exif_dict(path)
        if fallback is None:
            return None
        fallback["0th"][piexif.ImageIFD.Orientation] = 1
        try:
            return piexif.dump(fallback)
        except Exception:
            return None


# ---------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------

def _srgb_icc() -> bytes:
    try:
        return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    except Exception:
        return b""


_SRGB_ICC = _srgb_icc()


def _apply_flip(image: Image.Image, flip: int) -> Image.Image:
    """Match libraw's flip codes, for previews decoded outside rawpy."""
    if flip == 3:
        return image.rotate(180, expand=True)
    if flip == 5:
        return image.rotate(90, expand=True)
    if flip == 6:
        return image.rotate(270, expand=True)
    return image


def read_preview(path: str, max_px: int = 360) -> Image.Image | None:
    """Fast, cheap preview: the camera's own embedded JPEG, no raw decoding."""
    try:
        if looks_like_raw(path) and os.path.splitext(path)[1].lower() == ".raf":
            blob = _raf_embedded_jpeg(path)
            if blob:
                image = Image.open(io.BytesIO(blob))
                image.load()
                return ImageOps.exif_transpose(image).convert("RGB")
        if rawpy is not None and looks_like_raw(path):
            with rawpy.imread(path) as raw:
                flip = raw.sizes.flip
                try:
                    thumb = raw.extract_thumb()
                except Exception:
                    thumb = None
            if thumb is not None:
                if thumb.format == rawpy.ThumbFormat.JPEG:
                    image = Image.open(io.BytesIO(thumb.data))
                else:
                    image = Image.fromarray(thumb.data)
                image.load()
                image = _apply_flip(ImageOps.exif_transpose(image).convert("RGB"), flip)
                image.thumbnail((max_px, max_px), Image.LANCZOS)
                return image
        image = Image.open(path)
        image.load()
        image = ImageOps.exif_transpose(image).convert("RGB")
        image.thumbnail((max_px, max_px), Image.LANCZOS)
        return image
    except Exception:
        return None


def decode(path: str) -> tuple[Image.Image, str]:
    """Decode any supported file to an upright sRGB image.

    Returns (image, source_kind) where source_kind is "raw" or "photo".
    The raw decoder is tried first for raw-looking files, but a failure falls
    back to the standard image loader so an unusual container never blocks a
    conversion that Pillow could still handle.
    """
    wants_raw = looks_like_raw(path) or os.path.splitext(path)[1].lower() in RAW_EXTENSIONS
    if rawpy is not None and wants_raw:
        try:
            with rawpy.imread(path) as raw:
                rgb = raw.postprocess(
                    use_camera_wb=True,
                    output_bps=8,
                    output_color=rawpy.ColorSpace.sRGB,
                    no_auto_bright=False,
                    gamma=(2.222, 4.5),
                )
            return Image.fromarray(rgb), "raw"
        except Exception:
            pass  # fall through to the ordinary image path

    image = Image.open(path)
    image.load()
    return ImageOps.exif_transpose(image).convert("RGB"), "photo"


def _resize(image: Image.Image, max_px: int | None) -> Image.Image:
    if not max_px:
        return image
    longest = max(image.size)
    if longest <= max_px:
        return image
    ratio = max_px / longest
    new_size = (max(1, round(image.size[0] * ratio)), max(1, round(image.size[1] * ratio)))
    return image.resize(new_size, Image.LANCZOS)


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------

@dataclass
class Result:
    source: str
    output: str | None = None
    ok: bool = False
    message: str = ""
    kind: str = "raw"


def unique_path(path: str, taken: set[str] | None = None) -> str:
    """Never silently overwrite: append -1, -2, ... when needed."""
    if not os.path.exists(path) and (taken is None or path.lower() not in taken):
        return path
    stem, ext = os.path.splitext(path)
    index = 1
    while True:
        candidate = f"{stem}-{index}{ext}"
        if not os.path.exists(candidate) and (taken is None or candidate.lower() not in taken):
            return candidate
        index += 1


def convert_one(
    source: str,
    dest_dir: str,
    fmt: str = "heic",
    preset: str = DEFAULT_PRESET,
    taken: set[str] | None = None,
    keep_timestamps: bool = True,
) -> Result:
    """Convert one file. Never raises: problems come back in Result.message."""
    result = Result(source=source)
    settings = PRESETS.get(preset, PRESETS[DEFAULT_PRESET])
    fmt = fmt.lower()
    if fmt == "heic" and not heif_available():
        fmt = "jpg"
        result.message = "HEIC غير متاح على هذا الجهاز، تم الحفظ بصيغة JPG."

    try:
        image, kind = decode(source)
        result.kind = kind
        image = _resize(image, settings["max_px"])
        if image.mode != "RGB":
            image = image.convert("RGB")

        base = os.path.splitext(os.path.basename(source))[0]
        os.makedirs(dest_dir, exist_ok=True)
        suffix = ".heic" if fmt == "heic" else ".jpg"
        target = unique_path(os.path.join(dest_dir, base + suffix), taken)
        if taken is not None:
            taken.add(target.lower())

        exif_bytes = build_exif(source, image.size)

        if fmt == "heic":
            save_kwargs = {"quality": settings["heic_q"], "enc_params": dict(_HEIC_ENC)}
            if exif_bytes:
                save_kwargs["exif"] = exif_bytes
            image.save(target, format="HEIF", **save_kwargs)
        else:
            save_kwargs = {
                "quality": settings["jpg_q"],
                "optimize": True,
                "progressive": True,
                "subsampling": settings["jpg_subsampling"],
            }
            if _SRGB_ICC:
                save_kwargs["icc_profile"] = _SRGB_ICC
            image.save(target, format="JPEG", **save_kwargs)
            if exif_bytes:
                # piexif.insert is the reliable route for JPEG EXIF.
                try:
                    piexif.insert(exif_bytes, target)
                except Exception:
                    pass

        if keep_timestamps:
            _copy_file_time(source, target, exif_bytes)

        result.output = target
        result.ok = True
    except Exception as exc:  # noqa: BLE001 - surfaced to the user
        result.message = f"{type(exc).__name__}: {exc}"
    return result


def _copy_file_time(source: str, target: str, exif_bytes: bytes | None) -> None:
    """Give the new file the photo's own date so sorting stays correct."""
    stamp = None
    if exif_bytes:
        text = _exif_date_text(exif_bytes)
        if text:
            try:
                stamp = datetime.strptime(text, "%Y:%m:%d %H:%M:%S").timestamp()
            except Exception:
                stamp = None
    if stamp is None:
        try:
            stamp = os.path.getmtime(source)
        except OSError:
            return
    try:
        os.utime(target, (stamp, stamp))
    except OSError:
        pass


def _exif_date_text(exif_bytes: bytes) -> str | None:
    try:
        parsed = piexif.load(exif_bytes)
    except Exception:
        return None
    return _exif_date(parsed)


def collect_inputs(paths: list[str]) -> list[str]:
    """Expand folders, keep only supported files, de-duplicate, keep order."""
    out: list[str] = []
    seen: set[str] = set()

    def add(candidate: str) -> None:
        lowered = candidate.lower()
        if lowered in seen:
            return
        ext = os.path.splitext(candidate)[1].lower()
        if ext not in SUPPORTED_EXTENSIONS and not looks_like_raw(candidate):
            return
        seen.add(lowered)
        out.append(candidate)

    for raw in paths:
        if os.path.isdir(raw):
            for root, _dirs, files in os.walk(raw):
                for name in sorted(files):
                    add(os.path.join(root, name))
        elif os.path.isfile(raw):
            add(raw)
    return out


# ---------------------------------------------------------------------------
# Command line mode (handy for bulk runs)
# ---------------------------------------------------------------------------

def _cli(argv: list[str]) -> int:
    import argparse

    # A windowed (no-console) build has no real stdout, and the Windows console
    # code page may not hold Arabic. Never let reporting kill a finished job.
    def say(text: str) -> None:
        stream = sys.stdout
        if stream is None:
            return
        try:
            stream.write(text + "\n")
        except UnicodeEncodeError:
            encoding = getattr(stream, "encoding", None) or "utf-8"
            try:
                stream.write(text.encode(encoding, "replace").decode(encoding, "replace") + "\n")
            except Exception:
                pass
        except Exception:
            pass
        try:
            stream.flush()
        except Exception:
            pass

    parser = argparse.ArgumentParser(
        prog="app.py",
        description="Convert camera RAW files to HEIC/JPG.",
    )
    parser.add_argument("inputs", nargs="+", help="files or folders")
    parser.add_argument("-o", "--out", default=None,
                        help="output folder (default: Converted next to each input)")
    parser.add_argument("-f", "--format", choices=("heic", "jpg"), default="heic")
    parser.add_argument("-q", "--quality", choices=tuple(PRESETS), default=DEFAULT_PRESET)
    args = parser.parse_args(argv)

    files = collect_inputs(args.inputs)
    if not files:
        say("لم يتم العثور على صور مدعومة.")
        return 1

    taken: set[str] = set()
    done = 0
    for index, source in enumerate(files, 1):
        dest_dir = args.out or os.path.join(os.path.dirname(os.path.abspath(source)), "Converted")
        result = convert_one(source, dest_dir, args.format, args.quality, taken)
        mark = "OK " if result.ok else "ERR"
        say(f"[{index}/{len(files)}] {mark} {os.path.basename(source)} -> {result.output or result.message}")
        done += 1 if result.ok else 0
    say(f"\nتم تحويل {done} من {len(files)}.")
    return 0 if done else 2


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
