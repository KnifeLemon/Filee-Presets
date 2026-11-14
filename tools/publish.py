"""Publishes main: validates every preset, fills the CI-owned meta.json fields, creates missing Discussions and
regenerates index.json.

Usage: python tools/publish.py --root . --cli "<filee cli>" [--no-discussions]

CI-owned meta.json fields: kinds, formats, minVersion (from Filee's validator), created and updated (from git
history, ignoring this script's own commits) and discussion (created in the category named by DISCUSSION_CATEGORY,
default "Presets"). A preset that fails any check is left out of index.json and listed in the --failed file.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import filee_cli  # noqa: E402
import rules  # noqa: E402
from common import (  # noqa: E402
    MAX_META_BYTES, META_KEYS, PUBLISH_MARKER, clean_text, env, load_tags, read_json, write_json,
)

STATS = {"likes": 0, "comments": 0, "downloads": 0, "hot": 0.0,
         "best": {"day": 0, "week": 0, "month": 0, "year": 0, "all": 0}}
ZWSP = chr(0x200B)


def iso(ts: int) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def history(root: Path, slug: str) -> list[int]:
    """Commit times touching the folder, newest first, without the publish bot's own commits."""
    proc = subprocess.run(
        ["git", "-C", str(root), "log", "--format=%ct%x09%s", "--", f"presets/{slug}"],
        capture_output=True, text=True,
    )
    times = []
    for line in proc.stdout.splitlines():
        ts, _, subject = line.partition("\t")
        if PUBLISH_MARKER not in subject and ts.isdigit():
            times.append(int(ts))
    return times


class Discussions:
    def __init__(self, repo: str, category_name: str):
        import gh

        self.gh = gh
        owner, name = repo.split("/")
        self.owner, self.name, self.repo = owner, name, repo
        data = gh.graphql(
            "query($o:String!,$n:String!){repository(owner:$o,name:$n){id "
            "discussionCategories(first:50){nodes{id name slug}}}}", {"o": owner, "n": name})
        self.repo_id = data["repository"]["id"]
        self.category = next((c["id"] for c in data["repository"]["discussionCategories"]["nodes"]
                              if category_name.lower() in (c["name"].lower(), c["slug"].lower())), None)
        self.existing: dict[str, int] = {}
        if self.category:
            self._load_existing()

    def _load_existing(self) -> None:
        after = None
        while True:
            data = self.gh.graphql(
                "query($o:String!,$n:String!,$c:ID!,$a:String){repository(owner:$o,name:$n){"
                "discussions(first:100,after:$a,categoryId:$c){pageInfo{hasNextPage endCursor} nodes{number body}}}}",
                {"o": self.owner, "n": self.name, "c": self.category, "a": after})
            page = data["repository"]["discussions"]
            for node in page["nodes"]:
                body = node.get("body") or ""
                if body.startswith("<!-- filee-preset:"):
                    slug = body[len("<!-- filee-preset:"):].split(" ", 1)[0].split("-->", 1)[0].strip()
                    self.existing.setdefault(slug, node["number"])
            if not page["pageInfo"]["hasNextPage"]:
                return
            after = page["pageInfo"]["endCursor"]

    def ensure(self, meta: dict) -> int | None:
        slug = meta["id"]
        if slug in self.existing:
            return self.existing[slug]
        if not self.category:
            return None
        title = clean_text(meta["title"], single_line=True).replace("@", "@" + ZWSP)
        desc = clean_text(meta["description"], single_line=False).replace("@", "@" + ZWSP)
        body = (
            f"<!-- filee-preset:{slug} -->\n"
            f"**{title}**, shared by @{meta['author']}.\n\n"
            f"{desc}\n\n"
            "---\n"
            "Give this post a thumbs-up to like the preset. Comments here are the preset's discussion; use "
            "**Report content** for anything that breaks the rules.\n\n"
            f"Files: https://github.com/{self.repo}/tree/main/presets/{slug}\n"
            "Install it from the Preset gallery in Filee or on filee.sh. Published under CC0 1.0.\n"
        )
        data = self.gh.graphql(
            "mutation($r:ID!,$c:ID!,$t:String!,$b:String!){createDiscussion(input:"
            "{repositoryId:$r,categoryId:$c,title:$t,body:$b}){discussion{number}}}",
            {"r": self.repo_id, "c": self.category, "t": title, "b": body})
        number = data["createDiscussion"]["discussion"]["number"]
        self.existing[slug] = number
        return number


def publish_one(root: Path, folder: Path, tags: list[str], cli: str, discussions: Discussions | None):
    slug = folder.name
    errors, meta = rules.check_folder(folder, tags, published=False)
    if errors or meta is None:
        return None, errors or [rules.err("meta", "meta.json is missing")]
    result = filee_cli.check(folder, cli)
    if not result["ok"]:
        return None, result["errors"]
    derived = result["derived"]
    times = history(root, slug)
    updated = dict(meta)
    updated["kinds"] = derived["kinds"]
    updated["formats"] = derived["formats"]
    updated["minVersion"] = derived["minVersion"]
    if derived.get("warnings") is not None:
        updated["warnings"] = [{"code": w["code"], "severity": w["severity"]} for w in derived["warnings"]]
    updated["created"] = meta.get("created") or (iso(times[-1]) if times else now())
    updated["updated"] = iso(times[0]) if times else updated["created"]
    if updated["updated"] < updated["created"]:
        updated["updated"] = updated["created"]
    if updated.get("discussion") is None and discussions is not None:
        updated["discussion"] = discussions.ensure(updated)
    updated = {k: updated[k] for k in META_KEYS if k in updated}
    if updated != meta:
        write_json(folder / "meta.json", updated)
    errors = rules.check_meta(read_json(folder / "meta.json", MAX_META_BYTES), slug, tags, published=True)
    if errors:
        return None, errors
    images = sorted((folder / "images").glob("[1-4].webp"))
    entry = dict(updated)
    entry.setdefault("warnings", [])
    entry["images"] = [p.relative_to(root).as_posix() for p in images]
    entry["thumb"] = f"presets/{slug}/images/1-thumb.webp"
    entry["preset"] = f"presets/{slug}/preset.json"
    entry["sha256"] = rules.sha256(folder / "preset.json")
    entry["stats"] = json.loads(json.dumps(STATS))
    return entry, []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, type=Path)
    ap.add_argument("--cli", default="")
    ap.add_argument("--no-discussions", action="store_true")
    ap.add_argument("--failed", type=Path, help="where to write the failures (outside the repository)")
    args = ap.parse_args()
    root = args.root.resolve()
    tags = load_tags(root)

    discussions = None
    if not args.no_discussions:
        discussions = Discussions(os.environ["GITHUB_REPOSITORY"], env("DISCUSSION_CATEGORY") or "Presets")
        if not discussions.category:
            print("::warning::No Discussions category named "
                  f"'{env('DISCUSSION_CATEGORY') or 'Presets'}'. Create it (Settings > Discussions); "
                  "presets are published without a discussion until then.")

    entries, failed = [], {}
    presets_dir = root / "presets"
    for folder in sorted(presets_dir.iterdir()) if presets_dir.is_dir() else []:
        if not folder.is_dir() or folder.is_symlink():
            failed[folder.name] = [rules.err("layout", "not a folder")]
            continue
        entry, errors = publish_one(root, folder, tags, args.cli, discussions)
        if entry:
            entries.append(entry)
        else:
            failed[folder.name] = errors

    index_path = root / "index.json"
    old = None
    if index_path.is_file():
        try:
            old = json.loads(index_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            old = None
    generated = old.get("generated") if isinstance(old, dict) and old.get("presets") == entries else now()
    write_json(index_path, {"version": 1, "generated": generated, "presets": entries})
    if args.failed:
        write_json(args.failed, failed)
    print(json.dumps({"published": len(entries), "failed": sorted(failed)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
