"""Turns a "Submit a preset" issue into a presets/<slug>/ folder.

Usage: python tools/submission.py --event "$GITHUB_EVENT_PATH" --repo . --out <dir> [--cli "<filee cli>"]

Reads the issue straight from the event payload file (never from the shell), downloads only the attachments in the
"Preset file" and "Images" fields, re-encodes the images, and writes:
  <out>/result.json                 {ok, errors[], slug, issue, author, title}
  <out>/presets/<slug>/...          when ok
The author comes from the issue's GitHub account, never from the form.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import filee_cli  # noqa: E402
import fetch  # noqa: E402
import images  # noqa: E402
from common import (  # noqa: E402
    DESC_MAX, DESC_MIN, HTML_RE, LANGUAGES, LICENSE, LINK_RE, LOGIN_RE, MAX_IMAGES, MAX_LANGUAGES,
    MAX_PRESET_BYTES, MAX_TAGS, MIN_IMAGES, SLUG_RE, TITLE_MAX, TITLE_MIN, clean_text, load_tags, slugify,
    write_json,
)

# The issue form's field labels (.github/ISSUE_TEMPLATE/submit-preset.yml). Keep both in sync.
FIELDS = ["Title", "Description", "Languages", "Tags", "Preset file", "Images", "Agreement"]
NO_RESPONSE = "_No response_"
FENCED_JSON_RE = re.compile(r"```(?:json|jsonc)?\s*\n(.*?)\n```", re.DOTALL)
LANG_OPTION_RE = re.compile(r"\(([A-Za-z]{2,3}(?:-[A-Za-z]{2,4})?)\)\s*$")
CHECKED_RE = re.compile(r"^\s*-\s*\[[xX]\]\s+", re.MULTILINE)
REQUIRED_CHECKS = 2


class Problems(list):
    def add(self, code: str, message: str) -> None:
        self.append({"code": code, "message": message})


def parse_sections(body: str) -> dict[str, str]:
    """Splits an issue-form body into {label: value}. Only the known labels start a section."""
    sections: dict[str, list[str]] = {}
    current = None
    for line in body.replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        if stripped.startswith("### ") and stripped[4:].strip() in FIELDS and stripped[4:].strip() not in sections:
            current = stripped[4:].strip()
            sections[current] = []
            continue
        if current:
            sections[current].append(line)
    out = {}
    for key, lines in sections.items():
        value = "\n".join(lines).strip()
        out[key] = "" if value == NO_RESPONSE else value
    return out


def unique(items):
    seen = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return seen


def parse_form(body: str, tags: list[str], problems: Problems) -> dict:
    s = parse_sections(body)
    title = clean_text(s.get("Title", ""), single_line=True)
    if not (TITLE_MIN <= len(title) <= TITLE_MAX):
        problems.add("title", f"The title must be {TITLE_MIN}-{TITLE_MAX} characters.")
    elif LINK_RE.search(title) or HTML_RE.search(title):
        problems.add("title", "The title must not contain links or HTML.")

    desc = clean_text(s.get("Description", ""), single_line=False)
    if not (DESC_MIN <= len(desc) <= DESC_MAX):
        problems.add("description", f"The description must be {DESC_MIN}-{DESC_MAX} characters.")
    elif LINK_RE.search(desc) or HTML_RE.search(desc):
        problems.add("description", "The description must be plain text without links or HTML.")

    langs = []
    for part in s.get("Languages", "").split(","):
        m = LANG_OPTION_RE.search(part.strip())
        if m and m.group(1) in LANGUAGES:
            langs.append(m.group(1))
    langs = unique(langs)
    if not langs:
        problems.add("languages", "Choose the language the title and description are written in.")
    elif len(langs) > MAX_LANGUAGES:
        problems.add("languages", f"Choose at most {MAX_LANGUAGES} languages.")

    chosen_tags = unique(t.strip() for t in s.get("Tags", "").split(",") if t.strip() in tags)
    if len(chosen_tags) > MAX_TAGS:
        problems.add("tags", f"Choose at most {MAX_TAGS} tags.")

    if len(CHECKED_RE.findall(s.get("Agreement", ""))) < REQUIRED_CHECKS:
        problems.add("agreement", "Both boxes under Agreement must be ticked (CC0, and images made by you without personal information).")

    preset_field = s.get("Preset file", "")
    preset_urls = unique(fetch.FILE_URL_RE.findall(preset_field))
    pasted = FENCED_JSON_RE.findall(preset_field)
    image_urls = unique(fetch.ASSET_URL_RE.findall(s.get("Images", "")))
    if not (MIN_IMAGES <= len(image_urls) <= MAX_IMAGES):
        problems.add("images", f"Attach {MIN_IMAGES} to {MAX_IMAGES} images in the Images field (found {len(image_urls)}).")
    if len(preset_urls) + len(pasted) != 1:
        problems.add("preset", "Attach exactly one exported preset file (filee-presets.json) in the Preset file field.")
    return {
        "title": title, "description": desc, "languages": langs, "tags": chosen_tags,
        "preset_urls": preset_urls, "preset_pasted": pasted, "image_urls": image_urls,
    }


def load_preset(form: dict, problems: Problems):
    if form["preset_urls"]:
        try:
            raw = fetch.download(form["preset_urls"][0], MAX_PRESET_BYTES, fetch.JSON_TYPES)
        except fetch.FetchError as e:
            problems.add("preset", f"The preset file could not be downloaded: {e}.")
            return None
    elif form["preset_pasted"]:
        raw = form["preset_pasted"][0].encode("utf-8")
        if len(raw) > MAX_PRESET_BYTES:
            problems.add("preset", "The preset file is larger than 256 KB.")
            return None
    else:
        return None
    try:
        data = json.loads(raw.decode("utf-8-sig"), parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))
    except Exception:
        problems.add("preset", "The preset file is not valid JSON. Export it again from Settings > General > Export.")
        return None
    if not isinstance(data, dict) or not isinstance(data.get("presets"), list):
        problems.add("preset", "The preset file is not a Filee preset export (no `presets` list).")
        return None
    return data


def pick_slug(title: str, issue: int, existing: set[str]) -> str:
    base = slugify(title)
    if len(base) < 3:
        base = f"preset-{issue}"
    slug = base
    n = 2
    while slug in existing:
        suffix = f"-{n}"
        slug = base[: 48 - len(suffix)].rstrip("-") + suffix
        n += 1
    return slug


def run(event_path: Path, repo: Path, out: Path, cli: str) -> dict:
    event = json.loads(event_path.read_text(encoding="utf-8"))
    issue = event["issue"]
    number = int(issue["number"])
    author = str(issue["user"]["login"])
    body = issue.get("body") or ""
    problems = Problems()
    result = {"ok": False, "errors": problems, "slug": None, "issue": number, "author": author, "title": None}

    if not LOGIN_RE.match(author) or issue["user"].get("type") != "User":
        problems.add("author", "Submissions must come from a personal GitHub account.")
        return result

    tags = load_tags(repo)
    form = parse_form(body, tags, problems)
    preset = load_preset(form, problems)

    encoded = []
    for i, url in enumerate(form["image_urls"][:MAX_IMAGES], start=1):
        try:
            raw = fetch.download(url, images.MAX_IMAGE_BYTES, fetch.IMAGE_TYPES)
            encoded.append(images.reencode(raw))
        except (fetch.FetchError, images.ImageError) as e:
            problems.add("images", f"Image {i}: {e}.")

    if problems:
        return result

    presets_dir = repo / "presets"
    existing = {p.name for p in presets_dir.iterdir()} if presets_dir.is_dir() else set()
    slug = pick_slug(form["title"], number, existing)
    if not SLUG_RE.match(slug):
        problems.add("slug", "Could not make a folder name from the title.")
        return result

    folder = out / "presets" / slug
    (folder / "images").mkdir(parents=True, exist_ok=True)
    write_json(folder / "preset.json", preset)
    for i, (full, thumb) in enumerate(encoded, start=1):
        (folder / "images" / f"{i}.webp").write_bytes(full)
        (folder / "images" / f"{i}-thumb.webp").write_bytes(thumb)

    derived = {"kinds": [], "formats": {"from": [], "to": []}, "minVersion": None}
    if cli:
        check = filee_cli.check(folder, cli)
        if not check["ok"]:
            for e in check["errors"]:
                problems.add(e["code"], e["message"])
            return result
        derived = check["derived"]

    write_json(folder / "meta.json", {
        "id": slug,
        "title": form["title"],
        "description": form["description"],
        "author": author,
        "tags": form["tags"],
        "languages": form["languages"],
        "kinds": derived["kinds"],
        "formats": derived["formats"],
        "minVersion": derived["minVersion"],
        "discussion": None,
        "license": LICENSE,
    })
    result.update(ok=True, slug=slug, title=form["title"])
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--event", required=True, type=Path)
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--cli", default="")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    try:
        result = run(args.event, args.repo, args.out, args.cli)
    except Exception as e:  # the log gets the traceback; the issue comment only a fixed message
        traceback.print_exc()
        print(f"::error::submission failed: {type(e).__name__}", file=sys.stderr)
        result = {"ok": False, "errors": [{"code": "internal", "message": "The submission could not be processed. A maintainer will look at it."}],
                  "slug": None, "issue": None, "author": None, "title": None}
    write_json(args.out / "result.json", result)
    print(json.dumps({"ok": result["ok"], "slug": result["slug"], "errors": len(result["errors"])}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
