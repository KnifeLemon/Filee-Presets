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
    MAX_FOLDER_BYTES, MAX_LANGUAGES, MAX_META_BYTES, MAX_PRESET_BYTES, MAX_TAGS, META_KEYS, RESERVED_SLUGS, SLUG_RE,
    MAX_WARNINGS, SEVERITIES, TITLE_MAX, TITLE_MIN, VERSION_RE, WARNING_CODE_RE, derive_languages, read_json,
)

IMAGE_NAME_RE = re.compile(r"^([1-4])(?:(-thumb)\.webp|\.(webp|png|jpg|jpeg))$")
SOURCE_EXTS = ("png", "jpg", "jpeg")


def err(code: str, message: str) -> dict:
    return {"code": code, "message": message}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def image_sets(folder: Path) -> dict[str, dict[int, str]]:
    """{"" (default set) or a language code: {number: file name of the full image}} as found on disk."""
    sets: dict[str, dict[int, str]] = {}
    root = folder / "images"
    for d in [root] + sorted(p for p in root.iterdir() if p.is_dir() and not p.is_symlink()) if root.is_dir() else []:
        key = "" if d == root else d.name
        for f in d.iterdir():
            m = IMAGE_NAME_RE.match(f.name)
            if m and not m.group(2) and f.is_file() and not f.is_symlink():
                sets.setdefault(key, {})[int(m.group(1))] = f.name
    return sets


def check_layout(folder: Path, *, published: bool) -> tuple[list[dict], dict[str, dict[int, str]]]:
    """preset.json, meta.json, images/N.ext (+ N-thumb.webp) and optional images/<lang>/ sets. No links.

    Published (main): WebP only, every image with its thumbnail. Before the merge PNG/JPEG sources are fine and
    thumbnails may be missing: publish re-encodes them (tools/images.py normalize)."""
    errors: list[dict] = []
    if folder.is_symlink() or not folder.is_dir():
        return [err("layout", "The preset folder is missing.")], {}
    total = 0
    for entry in sorted(folder.rglob("*")):
        rel = entry.relative_to(folder).as_posix()
        if entry.is_symlink():
            errors.append(err("layout", f"`{rel}` is a link; only regular files are allowed."))
            continue
        parts = rel.split("/")
        if entry.is_dir():
            if rel != "images" and not (len(parts) == 2 and parts[0] == "images" and parts[1] in LANGUAGES):
                errors.append(err("layout", f"Unexpected folder `{rel[:80]}` (language sets go in images/<language code>/)."))
            continue
        total += entry.stat().st_size
        if rel in ("preset.json", "meta.json"):
            continue
        m = IMAGE_NAME_RE.match(entry.name) if parts[0] == "images" and len(parts) in (2, 3) else None
        if not m or (len(parts) == 3 and parts[1] not in LANGUAGES):
            errors.append(err("layout", f"Unexpected file `{rel[:80]}`."))
        elif published and m.group(3) in SOURCE_EXTS:
            errors.append(err("images", f"`{rel[:80]}` must be WebP on main."))
    if total > MAX_FOLDER_BYTES:
        errors.append(err("size", f"The folder is {total / 1048576:.1f} MB; at most {MAX_FOLDER_BYTES // 1048576} MB."))
    for name in ("preset.json", "meta.json"):
        if not (folder / name).is_file():
            errors.append(err("layout", f"`{name}` is missing."))

    sets = image_sets(folder)
    default = sets.get("", {})
    for key in sorted(sets, key=lambda k: (k != "", k)):
        prefix = "images/" + (f"{key}/" if key else "")
        d = folder / prefix
        numbers = set(sets[key])
        for n in numbers:
            sources = [e for e in ("webp",) + SOURCE_EXTS if (d / f"{n}.{e}").is_file()]
            if len(sources) > 1:
                errors.append(err("images", f"`{prefix}{n}` exists as more than one file ({', '.join(sources)})."))
        if key == "" and numbers != set(range(1, len(numbers) + 1)):
            errors.append(err("images", "Images must be numbered 1, 2, 3, 4 without gaps."))
        if key and not numbers <= set(default):
            errors.append(err("images", f"`{prefix}` has numbers the default set doesn't have; a language set replaces default images, it can't add new ones."))
        for n in sorted(numbers):
            if published and not (d / f"{n}-thumb.webp").is_file():
                errors.append(err("images", f"`{prefix}{n}-thumb.webp` is missing."))
        for t in sorted(d.glob("[1-4]-thumb.webp")):
            if int(t.name[0]) not in numbers:
                errors.append(err("images", f"`{prefix}{t.name}` has no matching image."))
    if not default:
        errors.append(err("images", "At least one image is required (images/1.webp is the cover)."))
    return errors, sets


def check_images(folder: Path, sets: dict[str, dict[int, str]], *, published: bool) -> list[dict]:
    errors = []
    for key, numbers in sets.items():
        prefix = "images/" + (f"{key}/" if key else "")
        for n, name in sorted(numbers.items()):
            path = folder / prefix / name
            # Before the merge any PNG/JPEG/WebP that decodes is fine (publish re-encodes it without metadata).
            problem = images.check_published(path, thumb=False) if published else images.check_source(path)
            if problem:
                errors.append(err("images", f"`{prefix}{name}` {problem}."))
            thumb = folder / prefix / f"{n}-thumb.webp"
            if thumb.is_file():
                problem = images.check_published(thumb, thumb=True)
                if problem:
                    errors.append(err("images", f"`{prefix}{thumb.name}` {problem}."))
    return errors


def _str_list(value, max_len: int) -> bool:
    return isinstance(value, list) and len(value) <= max_len and all(isinstance(v, str) for v in value) \
        and len(set(value)) == len(value)


def check_title(value, name: str) -> list[dict]:
    if not isinstance(value, str) or not (TITLE_MIN <= len(value) <= TITLE_MAX) or "\n" in value or value != value.strip():
        return [err("meta", f"{name} must be one line of {TITLE_MIN}-{TITLE_MAX} characters.")]
    if LINK_RE.search(value) or HTML_RE.search(value):
        return [err("meta", f"{name} must not contain links or HTML.")]
    return []


def check_description(value, name: str) -> list[dict]:
    if not isinstance(value, str) or not (DESC_MIN <= len(value) <= DESC_MAX):
        return [err("meta", f"{name} must be {DESC_MIN}-{DESC_MAX} characters.")]
    if LINK_RE.search(value) or HTML_RE.search(value):
        return [err("meta", f"{name} must be plain text without links or HTML.")]
    return []


def check_meta(meta, slug: str, tags: list[str], *, published: bool) -> list[dict]:
    """published=False: before the merge, `created` and `updated` are left out and `discussion` is null; CI sets
    them on main. (Filee's validator accepts a missing time but not a null one.)"""
    if not isinstance(meta, dict):
        return [err("meta", "meta.json must be a JSON object.")]
    errors = []
    keys = list(meta.keys())
    # `warnings` is left out when the validator that published the preset didn't report any field for it.
    optional = {"warnings", "titleByLang", "descriptionByLang"}
    if not published:  # set by CI on main; `language` is filled in from `languages` for older submissions
        optional |= {"created", "updated", "language"}
    missing = [k for k in META_KEYS if k not in meta and k not in optional]
    extra = [k for k in keys if k not in META_KEYS]
    if missing:
        errors.append(err("meta", "meta.json is missing: " + ", ".join(missing) + "."))
    if extra:
        errors.append(err("meta", "meta.json has unknown fields: " + ", ".join(e[:40] for e in extra[:10]) + "."))
    if meta.get("id") != slug:
        errors.append(err("meta", "meta.json `id` must equal the folder name."))
    errors += check_title(meta.get("title"), "`title`")
    errors += check_description(meta.get("description"), "`description`")
    if "language" in meta and meta["language"] not in LANGUAGES:
        errors.append(err("meta", "`language` must be one of the app's language codes (en, ko, ja, zh-CN, ...)."))
    main = (derive_languages(meta) or [None])[0]
    for key, check in (("titleByLang", check_title), ("descriptionByLang", check_description)):
        if key not in meta:
            continue
        texts = meta[key]
        if not isinstance(texts, dict) or len(texts) > len(LANGUAGES):
            errors.append(err("meta", f"`{key}` must be an object of language code: text."))
            continue
        for code, text in texts.items():
            if code not in LANGUAGES:
                errors.append(err("meta", f"`{key}` has `{str(code)[:16]}`, which is not one of the app's language codes."))
            elif code == main:
                errors.append(err("meta", f"`{key}.{code}` repeats the main language; that text goes in title/description."))
            else:
                errors += check(text, f"`{key}.{code}`")
    if isinstance(meta.get("descriptionByLang"), dict):
        orphans = sorted(set(meta["descriptionByLang"]) - set(meta.get("titleByLang") or {}))
        if orphans:
            errors.append(err("meta", "`descriptionByLang` has languages without a title in `titleByLang`: "
                              + ", ".join(str(o)[:16] for o in orphans) + "."))
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
        errors.append(err("meta", "`languages` must list the supported language codes."))
    elif published and langs != derive_languages(meta):
        errors.append(err("meta", "`languages` must be `language` plus the codes in `titleByLang` (CI sets it)."))
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
    layout_errors, sets = check_layout(folder, published=published)
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
    errors += check_images(folder, sets, published=published)
    return errors, meta if isinstance(meta, dict) else None
