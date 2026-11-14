"""Image checks and re-encoding.

Submitted images are decoded with Pillow (PNG, JPEG or WebP only, one frame), turned to sRGB, and written as fresh
WebP files with no metadata at all: no EXIF, GPS, XMP or ICC profile survives. Thumbnails are made from the clean
copy.
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageCms, ImageOps

from common import MAX_IMAGE_BYTES, MAX_LONG_EDGE, MIN_LONG_EDGE, THUMB_EDGE

Image.MAX_IMAGE_PIXELS = MAX_LONG_EDGE * MAX_LONG_EDGE + 1
ALLOWED_FORMATS = {"PNG", "JPEG", "WEBP"}
WEBP_QUALITY = 88
THUMB_QUALITY = 80


class ImageError(Exception):
    pass


def _open_checked(data: bytes, allowed: set[str]) -> Image.Image:
    if len(data) > MAX_IMAGE_BYTES:
        raise ImageError("larger than 2 MB")
    try:
        im = Image.open(io.BytesIO(data), formats=sorted(allowed))
    except Image.DecompressionBombError:
        raise ImageError("too many pixels") from None
    except Exception:
        raise ImageError("not a PNG, JPEG or WebP image") from None
    if im.format not in allowed:
        raise ImageError("not a PNG, JPEG or WebP image")
    if getattr(im, "is_animated", False) or getattr(im, "n_frames", 1) != 1:
        raise ImageError("animated images are not allowed")
    w, h = im.size
    if max(w, h) < MIN_LONG_EDGE or max(w, h) > MAX_LONG_EDGE:
        raise ImageError(f"the long edge is {max(w, h)} px; it must be {MIN_LONG_EDGE} to {MAX_LONG_EDGE} px")
    if min(w, h) < 16:
        raise ImageError("the image is too narrow")
    try:
        im.load()
    except Exception:
        raise ImageError("the image is damaged or cut off") from None
    return im


def _to_srgb(im: Image.Image) -> Image.Image:
    icc = im.info.get("icc_profile")
    mode = "RGBA" if ("A" in im.getbands() or "transparency" in im.info) else "RGB"
    im = im.convert(mode)
    if icc:
        try:
            src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            im = ImageCms.profileToProfile(im, src, ImageCms.createProfile("sRGB"), outputMode=mode)
        except Exception:
            pass  # a broken profile: keep the pixels as they are
    clean = Image.new(mode, im.size)
    clean.paste(im)
    return clean


def reencode(data: bytes) -> tuple[bytes, bytes]:
    """Returns (image.webp, thumb.webp) bytes for a submitted PNG/JPEG/WebP image, metadata stripped."""
    with _open_checked(data, ALLOWED_FORMATS) as im:
        oriented = ImageOps.exif_transpose(im)
        clean = _to_srgb(oriented)
    out = io.BytesIO()
    clean.save(out, "WEBP", quality=WEBP_QUALITY, method=6, exif=b"", icc_profile=None, xmp=b"")
    full = out.getvalue()
    if len(full) > MAX_IMAGE_BYTES:
        raise ImageError("still larger than 2 MB after re-encoding")
    thumb_img = clean.copy()
    thumb_img.thumbnail((THUMB_EDGE, THUMB_EDGE), Image.Resampling.LANCZOS)
    tout = io.BytesIO()
    thumb_img.save(tout, "WEBP", quality=THUMB_QUALITY, method=6, exif=b"", icc_profile=None, xmp=b"")
    return full, tout.getvalue()


def check_published(path: Path, *, thumb: bool) -> str | None:
    """Checks a WebP file already in the repo. Returns an error message or None."""
    if path.is_symlink() or not path.is_file():
        return "is not a regular file"
    data = path.read_bytes()
    if len(data) > MAX_IMAGE_BYTES:
        return "is larger than 2 MB"
    try:
        with Image.open(io.BytesIO(data), formats=["WEBP"]) as im:
            if im.format != "WEBP":
                return "is not a WebP image"
            if getattr(im, "is_animated", False) or getattr(im, "n_frames", 1) != 1:
                return "is animated"
            long_edge = max(im.size)
            im.load()
            if thumb and long_edge > THUMB_EDGE:
                return f"is {long_edge} px; thumbnails are at most {THUMB_EDGE} px"
            if not thumb and not (MIN_LONG_EDGE <= long_edge <= MAX_LONG_EDGE):
                return f"has a long edge of {long_edge} px ({MIN_LONG_EDGE}-{MAX_LONG_EDGE} allowed)"
            if im.info.get("exif") or im.info.get("icc_profile") or im.info.get("xmp"):
                return "still carries metadata"
    except Exception:
        return "does not decode as WebP"
    return None


def check_source(path: Path) -> str | None:
    """Checks an image in a pull request before CI re-encodes it (PNG, JPEG or WebP; metadata may still be there).
    Returns an error message or None."""
    if path.is_symlink() or not path.is_file():
        return "is not a regular file"
    try:
        reencode(path.read_bytes())
    except ImageError as e:
        return str(e)
    return None


def _has_metadata(path: Path) -> bool:
    with Image.open(path, formats=["WEBP"]) as im:
        return bool(im.info.get("exif") or im.info.get("icc_profile") or im.info.get("xmp"))


def normalize(images_dir: Path) -> list[str]:
    """Turns every image set under images/ (the default set and images/<lang>/) into clean WebP files with
    thumbnails: PNG/JPEG sources and WebP files that still carry metadata are re-encoded (and the source removed),
    a missing thumbnail is made from the WebP. Clean files are left alone, so running it again changes nothing.
    Returns the paths it wrote or removed, relative to images_dir."""
    changed: list[str] = []
    dirs = [images_dir] + sorted(p for p in images_dir.iterdir() if p.is_dir() and not p.is_symlink())
    for d in dirs:
        for n in range(1, 5):
            webp, thumb = d / f"{n}.webp", d / f"{n}-thumb.webp"
            sources = [d / f"{n}.{ext}" for ext in ("png", "jpg", "jpeg") if (d / f"{n}.{ext}").is_file()]
            if sources or (webp.is_file() and _has_metadata(webp)):
                src = sources[0] if sources else webp
                full, small = reencode(src.read_bytes())
                webp.write_bytes(full)
                thumb.write_bytes(small)
                for s in sources:
                    s.unlink()
                changed += [p.relative_to(images_dir).as_posix() for p in [webp, thumb, *sources]]
            elif webp.is_file() and not thumb.is_file():
                with Image.open(webp, formats=["WEBP"]) as im:
                    small = im.convert("RGBA" if "A" in im.getbands() else "RGB")
                small.thumbnail((THUMB_EDGE, THUMB_EDGE), Image.Resampling.LANCZOS)
                out = io.BytesIO()
                small.save(out, "WEBP", quality=THUMB_QUALITY, method=6, exif=b"", icc_profile=None, xmp=b"")
                thumb.write_bytes(out.getvalue())
                changed.append(thumb.relative_to(images_dir).as_posix())
    return changed


def review_copy(path: Path, edge: int = 1280) -> bytes:
    """A smaller JPEG of a published image for the AI review."""
    with Image.open(path, formats=["WEBP"]) as im:
        im = im.convert("RGB")
        im.thumbnail((edge, edge), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        im.save(out, "JPEG", quality=85)
        return out.getvalue()
