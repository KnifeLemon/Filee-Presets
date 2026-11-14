"""Rules check for a pull request that adds or changes a preset.

Usage: python tools/check.py --base <main checkout> --pr <PR checkout> --changed <files.json> --out <dir> [--cli "..."]

<files.json> is the GitHub "list pull request files" answer. The PR checkout is only read as data: nothing in it is
executed, and these tools always come from main. Writes <out>/report.json and, when the rules pass, a copy of the
preset folder in <out>/preset/ for the AI review.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import filee_cli  # noqa: E402
import rules  # noqa: E402
from common import MAX_META_BYTES, SLUG_RE, load_tags, read_json, write_json  # noqa: E402

err = rules.err


def changed_slug(changed: list[dict], errors: list[dict]) -> tuple[str | None, bool]:
    """The one presets/<slug>/ folder this PR touches, and whether it only deletes files."""
    slugs = set()
    only_deletes = True
    for f in changed:
        names = [f.get("filename", "")]
        if f.get("previous_filename"):
            names.append(f["previous_filename"])
        for name in names:
            parts = name.split("/")
            if len(parts) < 3 or parts[0] != "presets":
                errors.append(err("scope", f"`{name[:100]}` is outside presets/<name>/. A preset PR may only change its own folder."))
                continue
            slugs.add(parts[1])
        if f.get("status") != "removed":
            only_deletes = False
    if len(slugs) > 1:
        errors.append(err("scope", "One pull request may add or change one preset only."))
    if not slugs:
        errors.append(err("scope", "The pull request changes no preset folder."))
        return None, False
    return sorted(slugs)[0], only_deletes


def preset_shas(presets: Path, skip: str) -> dict[str, str]:
    out = {}
    if presets.is_dir():
        for folder in presets.iterdir():
            f = folder / "preset.json"
            if folder.name != skip and f.is_file() and not f.is_symlink():
                out[rules.sha256(f)] = folder.name
    return out


def run(base: Path, pr: Path, changed: list[dict], cli: str) -> tuple[dict, Path | None]:
    errors: list[dict] = []
    warnings: list[dict] = []
    notes: list[str] = []
    report = {"ok": False, "slug": None, "mode": None, "errors": errors, "warnings": warnings, "notes": notes,
              "derived": None}

    slug, only_deletes = changed_slug(changed, errors)
    if errors or slug is None:
        return report, None
    report["slug"] = slug
    if not SLUG_RE.match(slug):
        errors.append(err("slug", "The folder name must be 3-48 characters of a-z, 0-9 and -."))
        return report, None

    base_folder = base / "presets" / slug
    folder = pr / "presets" / slug
    exists_on_main = base_folder.is_dir()

    if only_deletes and not folder.exists():
        report["mode"] = "removal"
        warnings.append(err("removal", "This pull request removes a preset. A maintainer must confirm the removal."))
        report["ok"] = True
        return report, None

    report["mode"] = "update" if exists_on_main else "new"
    tags = load_tags(base)
    folder_errors, meta = rules.check_folder(folder, tags, published=False)
    errors += folder_errors

    base_meta = None
    if exists_on_main:
        try:
            base_meta = read_json(base_folder / "meta.json", MAX_META_BYTES)
        except Exception:
            base_meta = None
        warnings.append(err("update", "This pull request changes a published preset. A maintainer must review the change."))
    if meta is not None:
        for key in ("author", "created", "updated", "discussion"):
            expected = base_meta.get(key) if isinstance(base_meta, dict) else None
            if key == "author" and not exists_on_main:
                continue
            if meta.get(key) != expected:
                errors.append(err("meta", f"`{key}` is set by CI and can't be changed in a pull request."))

    if (folder / "preset.json").is_file() and not (folder / "preset.json").is_symlink():
        dupes = preset_shas(base / "presets", skip=slug)
        twin = dupes.get(rules.sha256(folder / "preset.json"))
        if twin:
            errors.append(err("duplicate", f"preset.json is identical to the published preset `{twin}`."))
    if meta is not None and isinstance(meta.get("title"), str):
        for other in (base / "presets").glob("*/meta.json") if (base / "presets").is_dir() else []:
            if other.parent.name == slug:
                continue
            try:
                other_title = read_json(other, MAX_META_BYTES).get("title", "")
            except Exception:
                continue
            if isinstance(other_title, str) and other_title.casefold() == meta["title"].casefold():
                warnings.append(err("duplicate-title", f"The published preset `{other.parent.name}` has the same title."))

    if not errors:
        cli_result = filee_cli.check(folder, cli)
        errors += cli_result["errors"]
        warnings += cli_result["warnings"]
        report["derived"] = cli_result["derived"]
        derived = cli_result["derived"]
        if cli_result["ok"] and meta is not None and derived:
            for key in ("kinds", "formats", "minVersion"):
                if meta.get(key) != derived.get(key):
                    notes.append(f"`{key}` in meta.json differs from what Filee derives; it is updated on publish.")

    report["ok"] = not errors
    return report, folder if report["ok"] else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, type=Path)
    ap.add_argument("--pr", required=True, type=Path)
    ap.add_argument("--changed", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--cli", default="")
    args = ap.parse_args()
    changed = json.loads(args.changed.read_text(encoding="utf-8"))
    if not isinstance(changed, list):
        raise SystemExit("--changed must be a JSON list")
    report, folder = run(args.base, args.pr, changed, args.cli)
    args.out.mkdir(parents=True, exist_ok=True)
    if folder is not None:
        shutil.copytree(folder, args.out / "preset", symlinks=True)
    write_json(args.out / "report.json", report)
    print(json.dumps({"ok": report["ok"], "slug": report["slug"], "errors": len(report["errors"]),
                      "warnings": len(report["warnings"])}))
    # Messages can quote file names from the PR, so the log gets only the codes (no workflow-command injection).
    codes = sorted({str(e.get("code", ""))[:40].replace("\n", " ").replace(":", "") for e in report["errors"]})
    if codes:
        print("::error::rules failed: " + ", ".join(codes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
