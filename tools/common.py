"""Shared limits and helpers for the Filee-Presets tools.

Every tool here treats submission content (issue text, preset JSON, images) as data. Nothing from a submission is
ever executed, imported, or passed to a shell.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,46})[a-z0-9]$")
LOGIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
FORMAT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9+._-]{0,23}$")
VERSION_RE = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}$")
ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
LINK_RE = re.compile(r"(https?://|www\.|\]\(|<\s*a\s)", re.IGNORECASE)
HTML_RE = re.compile(r"<\s*/?\s*[A-Za-z!]")

TITLE_MIN, TITLE_MAX = 3, 60
DESC_MIN, DESC_MAX = 10, 600
MAX_TAGS = 6
MAX_LANGUAGES = 6
MIN_IMAGES, MAX_IMAGES = 1, 4
MAX_IMAGE_BYTES = 2 * 1024 * 1024
MIN_LONG_EDGE, MAX_LONG_EDGE = 640, 2560
THUMB_EDGE = 480
MAX_PRESET_BYTES = 256 * 1024
MAX_META_BYTES = 8 * 1024
LICENSE = "CC0-1.0"
KINDS = {"convert", "tool", "toolbar"}

# Languages a title/description can be written in: the Filee app's UI languages.
LANGUAGES = [
    "ar", "bn-BD", "de", "en", "es", "fa", "fr", "hi", "id", "it", "ja", "ko", "ne", "pl", "pt-BR", "pt-PT",
    "ru", "th", "tr", "vi", "zh-CN", "zh-TW",
]

META_KEYS = [
    "id", "title", "description", "author", "tags", "languages", "kinds", "formats", "minVersion",
    "created", "updated", "discussion", "license",
]

CHECK_MARKER = "<!-- filee-presets-check -->"
SUBMISSION_MARKER = "<!-- filee-presets-submission -->"
PUBLISH_MARKER = "[gallery-publish]"


def load_tags(root: Path = REPO_ROOT) -> list[str]:
    data = json.loads((root / "tags.json").read_text(encoding="utf-8"))
    return [t["id"] for t in data["tags"]]


def read_json(path: Path, max_bytes: int):
    """Reads a JSON file that may come from a submission: size-capped, strict (no NaN/Infinity), UTF-8."""
    if path.is_symlink() or not path.is_file():
        raise ValueError("not a regular file")
    if path.stat().st_size > max_bytes:
        raise ValueError(f"larger than {max_bytes // 1024} KB")
    text = path.read_bytes().decode("utf-8-sig")
    return json.loads(text, parse_constant=_reject_constant)


def _reject_constant(name: str):
    raise ValueError(f"{name} is not valid JSON")


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


def slugify(title: str) -> str:
    text = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:48].strip("-")


def clean_text(value: str, *, single_line: bool) -> str:
    """Normalises newlines, drops control and format characters (bidi overrides, zero-width), trims."""
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    out = []
    for ch in value:
        cat = unicodedata.category(ch)
        if ch == "\n":
            out.append(" " if single_line else "\n")
        elif ch == "\t":
            out.append(" ")
        elif cat in ("Cc", "Cf", "Cs", "Co"):
            continue
        else:
            out.append(ch)
    text = "".join(out)
    if single_line:
        text = re.sub(r" {2,}", " ", text)
    else:
        text = re.sub(r"[ ]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def defang(text: str, limit: int = 300) -> str:
    """Makes untrusted text safe to quote in a GitHub comment: no mentions, no markup, no links, one line."""
    text = clean_text(str(text), single_line=True)[:limit]
    text = text.replace("@", "@\u200b")
    text = re.sub(r"https?://", "hxxp://", text, flags=re.IGNORECASE)
    for ch in "\\`*_[]<>|#!~":
        text = text.replace(ch, "\\" + ch)
    return text


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()
