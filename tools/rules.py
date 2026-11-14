"""Repository rules for one presets/<slug>/ folder: file layout, meta.json limits, images.

The Filee validator (filee_cli.py) checks preset.json itself; these rules check everything around it.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import images
from common import (
    DESC_MAX, DESC_MIN, FORMAT_ID_RE, HTML_RE, ISO_RE, KINDS, LANGUAGES, LICENSE, LINK_RE, LOGIN_RE,
    MAX_IMAGES, MAX_LANGUAGES, MAX_META_BYTES, MAX_PRESET_BYTES, MAX_TAGS, META_KEYS, RESERVED_SLUGS, SLUG_RE,
    MAX_WARNINGS, SEVERITIES, TITLE_MAX, TITLE_MIN, VERSION_RE, WARNING_CODE_RE, read_json,
)

IMAGE_NAME_RE = re.compile(r"^([1-4])(-thumb)?\.webp$")


def err(code: str, message: str) -> dict:
    return {"code": code, "message": message}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_layout(folder: Path) -> tuple[list[dict], list[int]]:
    """Only preset.json, meta.json and images/N.webp + images/N-thumb.webp (N = 1..4, no gaps). No links."""
    errors: list[dict] = []
    numbers: set[int] = set()
    thumbs: set[int] = set()
    if folder.is_symlink() or not folder.is_dir():
        return [err("layout", "The preset folder is missing.")], []
    for entry in sorted(folder.rglob("*")):
        rel = entry.relative_to(folder).as_posix()
        if entry.is_symlink():
            errors.append(err("layout", f"`{rel}` is a link; only regular files are allowed."))
            continue
        if entry.is_dir():
            if rel != "images":
                errors.append(err("layout", f"Unexpected folder `{rel}`."))
            continue
        if rel in ("preset.json", "meta.json"):
            continue
        m = IMAGE_NAME_RE.match(entry.name) if entry.parent.name == "images" and entry.parent.parent == folder else None
        if not m:
            errors.append(err("layout", f"Unexpected file `{rel[:80]}`."))
            continue
        (thumbs if m.group(2) else numbers).add(int(m.group(1)))
    for name in ("preset.json", "meta.json"):
        if not (folder / name).is_file():
            errors.append(err("layout", f"`{name}` is missing."))
    count = len(numbers)
    if count < 1:
        errors.append(err("images", "At least one image is required (images/1.webp is the cover)."))
    elif numbers != set(range(1, count + 1)):
        errors.append(err("images", "Images must be numbered 1, 2, 3, 4 without gaps."))
    if count > MAX_IMAGES:
        errors.append(err("images", f"At most {MAX_IMAGES} images are allowed."))
    for n in sorted(numbers):
        if n not in thumbs:
            errors.append(err("images", f"`images/{n}-thumb.webp` is missing."))
    for n in sorted(thumbs - numbers):
        errors.append(err("images", f"`images/{n}-thumb.webp` has no matching image."))
    return errors, sorted(numbers)


def check_images(folder: Path, numbers: list[int]) -> list[dict]:
    errors = []
    for n in numbers:
        for name, thumb in ((f"{n}.webp", False), (f"{n}-thumb.webp", True)):
            problem = images.check_published(folder / "images" / name, thumb=thumb)
            if problem:
                errors.append(err("images", f"`images/{name}` {problem}."))
    return errors


def _str_list(value, max_len: int) -> bool:
    return isinstance(value, list) and len(value) <= max_len and all(isinstance(v, str) for v in value) \
        and len(set(value)) == len(value)


def check_meta(meta, slug: str, tags: list[str], *, published: bool) -> list[dict]:
    """published=False: before the merge, `created` and `updated` are left out and `discussion` is null; CI sets
    them on main. (Filee's validator accepts a missing time but not a null one.)"""
    if not isinstance(meta, dict):
        return [err("meta", "meta.json must be a JSON object.")]
    errors = []
    keys = list(meta.keys())
    # `warnings` is left out when the validator that published the preset didn't report any field for it.
    optional = {"warnings"} if published else {"created", "updated", "warnings"}
    missing = [k for k in META_KEYS if k not in meta and k not in optional]
    extra = [k for k in keys if k not in META_KEYS]
    if missing:
        errors.append(err("meta", "meta.json is missing: " + ", ".join(missing) + "."))
    if extra:
        errors.append(err("meta", "meta.json has unknown fields: " + ", ".join(e[:40] for e in extra[:10]) + "."))
    if meta.get("id") != slug:
        errors.append(err("meta", "meta.json `id` must equal the folder name."))
    title = meta.get("title")
    if not isinstance(title, str) or not (TITLE_MIN <= len(title) <= TITLE_MAX) or "\n" in title or title != title.strip():
        errors.append(err("meta", f"`title` must be one line of {TITLE_MIN}-{TITLE_MAX} characters."))
    elif LINK_RE.search(title) or HTML_RE.search(title):
        errors.append(err("meta", "`title` must not contain links or HTML."))
    desc = meta.get("description")
    if not isinstance(desc, str) or not (DESC_MIN <= len(desc) <= DESC_MAX):
        errors.append(err("meta", f"`description` must be {DESC_MIN}-{DESC_MAX} characters."))
    elif LINK_RE.search(desc) or HTML_RE.search(desc):
        errors.append(err("meta", "`description` must be plain text without links or HTML."))
    author = meta.get("author")
    if not isinstance(author, str) or not LOGIN_RE.match(author):
        errors.append(err("meta", "`author` must be a GitHub login."))
    tag_list = meta.get("tags")
    if not _str_list(tag_list, MAX_TAGS):
        errors.append(err("meta", f"`tags` must be a list of at most {MAX_TAGS} different tags."))
    else:
        unknown = [t for t in tag_list if t not in tags]
        if unknown:
            errors.append(err("meta", "Unknown tags (add them to tags.json first): " + ", ".join(u[:30] for u in unknown) + "."))
    langs = meta.get("languages")
    if not _str_list(langs, MAX_LANGUAGES) or not langs or any(l not in LANGUAGES for l in langs):
        errors.append(err("meta", "`languages` must list 1-6 of the supported language codes."))
    kinds = meta.get("kinds")
    if not _str_list(kinds, 3) or not kinds or any(k not in KINDS for k in kinds):
        errors.append(err("meta", "`kinds` must list convert, tool and/or toolbar."))
    formats = meta.get("formats")
    if not isinstance(formats, dict) or set(formats.keys()) != {"from", "to"} or not all(
        _str_list(formats[k], 40) and all(FORMAT_ID_RE.match(f) for f in formats[k]) for k in ("from", "to")
    ):
        errors.append(err("meta", "`formats` must be {\"from\": [...], \"to\": [...]} with format ids."))
    mv = meta.get("minVersion")
    if not isinstance(mv, str) or not VERSION_RE.match(mv):
        errors.append(err("meta", "`minVersion` must look like 2.5.0."))
    for key in ("created", "updated"):
        if key not in meta and not published:
            continue
        value = meta.get(key)
        if not isinstance(value, str) or not ISO_RE.match(value):
            errors.append(err("meta", f"`{key}` must be a UTC time like 2026-11-15T00:00:00Z."))
    if "warnings" in meta:
        warns = meta["warnings"]
        if not isinstance(warns, list) or len(warns) > MAX_WARNINGS or not all(
            isinstance(w, dict) and set(w) == {"code", "severity"} and isinstance(w["code"], str)
            and WARNING_CODE_RE.match(w["code"]) and w["severity"] in SEVERITIES for w in warns
        ):
            errors.append(err("meta", "`warnings` must be a list of {\"code\", \"severity\"} from Filee's validator."))
    disc = meta.get("discussion")
    if not (disc is None or (isinstance(disc, int) and not isinstance(disc, bool) and disc > 0)):
        errors.append(err("meta", "`discussion` must be a discussion number or null."))
    if meta.get("license") != LICENSE:
        errors.append(err("meta", f"`license` must be {LICENSE}."))
    return errors


def check_folder(folder: Path, tags: list[str], *, published: bool) -> tuple[list[dict], dict | None]:
    """All repository rules for one folder. Returns (errors, meta or None)."""
    slug = folder.name
    errors: list[dict] = []
    if not SLUG_RE.match(slug):
        errors.append(err("slug", "The folder name must be 3-48 characters of a-z, 0-9 and -."))
    elif slug in RESERVED_SLUGS:
        errors.append(err("slug", f"`{slug}` is reserved for filee.sh's own pages; choose another folder name."))
    layout_errors, numbers = check_layout(folder)
    errors += layout_errors
    meta = None
    if (folder / "meta.json").is_file() and not (folder / "meta.json").is_symlink():
        try:
            meta = read_json(folder / "meta.json", MAX_META_BYTES)
        except Exception as e:
            errors.append(err("meta", f"meta.json does not parse: {str(e)[:120]}"))
        else:
            errors += check_meta(meta, slug, tags, published=published)
    preset = folder / "preset.json"
    if preset.is_file() and not preset.is_symlink():
        try:
            read_json(preset, MAX_PRESET_BYTES)
        except Exception as e:
            errors.append(err("preset", f"preset.json does not parse: {str(e)[:120]}"))
    errors += check_images(folder, numbers)
    return errors, meta if isinstance(meta, dict) else None
