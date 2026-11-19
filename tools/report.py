"""Posts the check result on a preset pull request and sets its label.

Usage: python tools/report.py --report <report.json> [--verdict <verdict.json>] --pr N [--sha SHA] [--dry-run]

Labels: `needs-changes` when the rules fail, `ready` only when the rules pass with no warnings AND the AI review ran
and found nothing, `needs-review` for everything else. The AI review can never do more than choose between `ready`
and `needs-review`; merging is always a maintainer's click.

Text fixes the AI review proposed for meta.json (already checked by tools/review.py) are posted as one pull request
review of suggested changes on the head commit, so a maintainer can apply them with "Commit suggestion". A fix whose
line isn't part of the pull request's diff is shown in the check comment instead.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gh  # noqa: E402
from common import CHECK_MARKER, MAX_TAGS, SLUG_RE, defang  # noqa: E402

LABELS = ["ready", "needs-review", "needs-changes"]
SEVERITY_TEXT = {"high": "**High**", "medium": "Medium", "info": "Info"}
FLAG_TEXT = {
    "spam": "Spam or placeholder",
    "offensive": "Offensive content",
    "misleading": "Misleading or steering the review",
    "personal_info_in_images": "Personal information in images",
}
SUGGEST_MARKER = "<!-- filee-presets-suggestions"
FIX_FIELDS = ("title", "description", "tags")
ISSUE_NOTE = ("If the submission issue is edited later, make this change there too; the pull request is rebuilt "
              "from the issue.")
HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
SHA_RE = re.compile(r"[0-9a-f]{40}")


def load(path: Path | None):
    if path and path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
    return None


def decide(report: dict, verdict: dict | None) -> str:
    if report.get("incomplete"):
        return "needs-review"
    if not report.get("ok"):
        return "needs-changes"
    if report.get("warnings") or report.get("mode") == "removal":
        return "needs-review"
    if verdict and verdict.get("status") == "done" and (verdict.get("verdict") or {}).get("clean") is True:
        return "ready"
    return "needs-review"


def status_of(report: dict) -> tuple[str, str]:
    """The `rules` commit status: the rules result only (the AI review is shown by the label)."""
    if report.get("incomplete"):
        return "error", "The check could not finish; a maintainer will run it again."
    if not report.get("ok"):
        return "failure", f"{len(report.get('errors') or [])} problem(s) to fix; see the comment."
    return "success", "Rules and Filee's validator passed."


def verdict_fixes(verdict: dict | None) -> list[dict]:
    """The checked fixes from a finished review (review.py validated them; only their shape is checked here)."""
    if not verdict or verdict.get("status") != "done":
        return []
    out = []
    for f in (verdict.get("verdict") or {}).get("fixes") or []:
        if not isinstance(f, dict) or f.get("field") not in FIX_FIELDS or not isinstance(f.get("reason"), str):
            continue
        value = f.get("replacement")
        if f["field"] == "tags":
            if not (isinstance(value, list) and len(value) <= MAX_TAGS and all(isinstance(t, str) for t in value)):
                continue
        elif not isinstance(value, str):
            continue
        out.append({"field": f["field"], "replacement": value, "reason": f["reason"]})
    return out[:3]


def find_field(text: str, field: str, value) -> tuple[int, int] | None:
    """(first, last) 0-based lines holding the top-level `field` of meta.json, or None.

    The lines must hold the whole key and nothing else: replacing them with `value` has to give exactly the parsed
    meta.json with only that field changed, so a key inside a nested object or a line shared with another key
    never matches."""
    lines = text.split("\n")
    try:
        meta = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(meta, dict) or field not in meta:
        return None
    expected = dict(meta, **{field: value})
    key = re.compile(r"^\s*" + re.escape(json.dumps(field)) + r"\s*:")
    for first, line in enumerate(lines):
        if not key.match(line):
            continue
        for last in range(first, min(first + MAX_TAGS + 3, len(lines))):
            new = build_lines(lines[first : last + 1], field, value)
            try:
                if json.loads("\n".join(lines[:first] + new + lines[last + 1 :])) == expected:
                    return first, last
            except json.JSONDecodeError:
                continue
    return None


def build_lines(old: list[str], field: str, value) -> list[str]:
    """The replacement for `old` (the field's lines): same indentation and trailing comma, the formatter's layout
    (tools/common.py write_json: indent 2, one list item per line)."""
    indent = re.match(r"\s*", old[0]).group(0)
    comma = "," if old[-1].rstrip().endswith(",") else ""
    step = "  "
    if len(old) > 1:
        inner = re.match(r"\s*", old[1]).group(0)
        if len(inner) > len(indent):
            step = inner[len(indent) :]
    if isinstance(value, list) and value:
        items = [f"{indent}{step}{json.dumps(v, ensure_ascii=False)}" for v in value]
        items = [line + "," for line in items[:-1]] + items[-1:]
        return [f"{indent}{json.dumps(field)}: ["] + items + [f"{indent}]{comma}"]
    return [f"{indent}{json.dumps(field)}: {json.dumps(value, ensure_ascii=False)}{comma}"]


def diff_hunks(patch: str | None) -> list[set[int]]:
    """Right-side (new file) line numbers per hunk of a unified diff: the lines a review comment can sit on."""
    hunks: list[set[int]] = []
    line = 0
    for row in (patch or "").split("\n"):
        m = HUNK_RE.match(row)
        if m:
            line = int(m.group(1))
            hunks.append(set())
        elif hunks and not row.startswith(("-", "\\")):
            hunks[-1].add(line)
            line += 1
    return hunks


def suggestion_body(reason: str, lines: list[str]) -> str:
    block = "\n".join(lines)
    longest = max((len(run) for run in re.findall(r"`+", block)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{defang(reason, 200)}\n\n{fence}suggestion\n{block}\n{fence}\n\n<sub>{ISSUE_NOTE}</sub>"


def plan_suggestions(text: str, hunks: list[set[int]], path: str, fixes: list[dict]) -> tuple[list[dict], list[dict]]:
    """(review comments, fixes that can't be placed) for meta.json's `text` at the head commit."""
    comments, fallback = [], []
    lines = text.split("\n")
    for fix in fixes:
        span = find_field(text, fix["field"], fix["replacement"])
        if span is None:
            fallback.append(fix)
            continue
        first, last = span[0] + 1, span[1] + 1  # GitHub counts from 1
        if not any(all(n in h for n in range(first, last + 1)) for h in hunks):
            fallback.append(fix)
            continue
        new = build_lines(lines[span[0] : span[1] + 1], fix["field"], fix["replacement"])
        comment = {"path": path, "line": last, "side": "RIGHT", "body": suggestion_body(fix["reason"], new)}
        if last > first:
            comment.update(start_line=first, start_side="RIGHT")
        comments.append(comment)
    return comments, fallback


def post_suggestions(repo: str, pr: int, sha: str, slug: str, fixes: list[dict]) -> tuple[int, list[dict]]:
    """Posts the fixes as one review (event COMMENT) on `sha`. Returns (fixes posted, fixes left for the comment).

    A review of this tool for the same head commit already exists after a re-run: then nothing is posted again.
    A new commit (the submitter edited the issue) gets a new review; GitHub marks the old one outdated."""
    if not fixes:
        return 0, []
    if not (SHA_RE.fullmatch(sha) and isinstance(slug, str) and SLUG_RE.match(slug)):
        return 0, fixes
    marker = f"{SUGGEST_MARKER} {sha} -->"
    try:
        for r in gh.paginate(f"repos/{repo}/pulls/{pr}/reviews"):
            if (r.get("body") or "").startswith(marker) and (r.get("user") or {}).get("type") == "Bot":
                return len(fixes), []
        path = f"presets/{slug}/meta.json"
        content = gh.request("GET", f"repos/{repo}/contents/{quote(path)}?ref={sha}")
        text = base64.b64decode(content.get("content") or "").decode("utf-8-sig")
        patch = next((f.get("patch") for f in gh.paginate(f"repos/{repo}/pulls/{pr}/files")
                      if f.get("filename") == path), None)
        comments, fallback = plan_suggestions(text, diff_hunks(patch), path, fixes)
        if comments:
            gh.request("POST", f"repos/{repo}/pulls/{pr}/reviews", {
                "commit_id": sha, "event": "COMMENT", "comments": comments,
                "body": f"{marker}\nThe AI review suggests {len(comments)} change(s) to `meta.json`. Check each one "
                        "and use **Commit suggestion** to apply it.",
            })
        return len(comments), fallback
    except (gh.GitHubError, ValueError, KeyError) as e:  # the comment and the label still go out
        print(f"::warning::Suggested changes not posted ({type(e).__name__} {getattr(e, 'status', '')}).")
        return 0, fixes


def render_fixes(posted: int, fallback: list[dict]) -> list[str]:
    lines = []
    if posted:
        lines += ["", f"**Suggested fixes:** {posted} posted as suggested changes on `meta.json` (see the review). "
                      "A maintainer can apply them with **Commit suggestion**."]
    if fallback:
        lines += ["", "**Suggested fixes** (not placed on the diff; change `meta.json` by hand if they are right):"]
        for f in fallback:
            value = ", ".join(f["replacement"]) if isinstance(f["replacement"], list) else f["replacement"]
            lines.append(f"- `{f['field']}`: {defang(f['reason'], 200)} Proposed: {defang(value, 650)}")
    return lines


def render(report: dict, verdict: dict | None, label: str, fixes: tuple[int, list[dict]] = (0, [])) -> str:
    lines = [CHECK_MARKER, "## Preset check", ""]
    slug = report.get("slug")
    presets = [p for p in report.get("presets") or [] if isinstance(p, dict)]
    if slug:
        lines.append(f"Folder: `presets/{defang(slug, 48)}/` ({defang(report.get('mode') or '?', 10)})")
        lines.append("")
    elif len(presets) > 1:
        lines.append(f"{len(presets)} preset folders, each checked on its own (allowed for repository owners and members).")
        lines.append("")
    errors = report.get("errors") or []
    if report.get("incomplete"):
        lines.append("**Rules:** did not finish. " + defang(errors[0].get("message", ""), 300))
    elif report.get("ok"):
        lines.append("**Rules:** passed.")
    else:
        lines.append(f"**Rules:** {len(errors)} problem(s) to fix:")
        lines += [f"- {defang(e.get('message', ''), 400)}" for e in errors[:30]]
    for p in presets:
        can = ((p.get("derived") or {}).get("warnings") or [])[:20]
        if can:
            name = f" (`{defang(p.get('slug', ''), 48)}`)" if len(presets) > 1 else ""
            lines += ["", f"**This preset can{name}:** (shown to people before they install it)"]
            lines += [f"- {SEVERITY_TEXT.get(w.get('severity'), 'Note')}: {defang(w.get('message', ''), 300)} "
                      f"(`{re.sub(r'[^a-z0-9_]', '', str(w.get('code', '')))[:48]}`)" for w in can]
    warnings = report.get("warnings") or []
    if warnings:
        lines += ["", "**Needs a maintainer:**"] + [f"- {defang(w.get('message', ''), 300)}" for w in warnings[:10]]
    notes = report.get("notes") or []
    if notes:
        lines += ["", "**Notes:**"] + [f"- {defang(n, 200)}" for n in notes[:10]]

    lines.append("")
    if verdict is None:
        lines.append("**AI review:** not run for this check.")
    elif verdict.get("status") != "done":
        lines.append(f"**AI review:** skipped. {defang(verdict.get('reason', ''), 200)}")
    else:
        v = verdict.get("verdict") or {}
        lines.append(f"**AI review** (`{defang(verdict.get('model', ''), 60)}`, advisory):")
        lines.append("")
        lines.append("| Check | Result |")
        lines.append("| --- | --- |")
        for key, text in FLAG_TEXT.items():
            lines.append(f"| {text} | {'**flagged**' if v.get(key) else 'no'} |")
        lines.append(f"| Description matches the preset | {'yes' if v.get('matches_description') else '**no**'} |")
        if v.get("suggested_tags"):
            lines.append("")
            lines.append("Suggested tags: " + ", ".join(f"`{defang(t, 24)}`" for t in v["suggested_tags"]))
            if v.get("new_tags"):
                lines.append("(not in tags.json yet: " + ", ".join(f"`{defang(t, 24)}`" for t in v["new_tags"]) + ")")
        if v.get("notes"):
            lines += ["", f"> {defang(v['notes'], 500)}"]
        lines += render_fixes(*fixes)

    lines.append("")
    lines.append({
        "ready": "**Result:** `ready`. A maintainer can merge after a last look at the images.",
        "needs-review": "**Result:** `needs-review`. A maintainer will look at it.",
        "needs-changes": "**Result:** `needs-changes`. If this came from the submission form, edit the issue and "
                         "the pull request updates itself.",
    }[label])
    lines.append("")
    lines.append("<sub>Checked by the Filee-Presets workflows. The AI review only sorts pull requests into the queue; "
                 "nothing is merged without a maintainer.</sub>")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", required=True, type=Path)
    ap.add_argument("--verdict", type=Path)
    ap.add_argument("--pr", required=True, type=int)
    ap.add_argument("--sha", default="", help="pull request head; gets a `rules` commit status")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    report = load(args.report) or {
        "ok": False, "incomplete": True,
        "errors": [{"code": "internal", "message": "The check could not finish (a problem on our side, not with the preset). A maintainer will run it again."}],
    }
    verdict = load(args.verdict)
    label = decide(report, verdict)
    fixes = verdict_fixes(verdict)
    if args.dry_run:
        body = render(report, verdict, label, (0, fixes))
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as f:
                f.write(body)
        print(body)
        print(f"label: {label}")
        return 0

    repo = os.environ["GITHUB_REPOSITORY"]
    body = render(report, verdict, label, post_suggestions(repo, args.pr, args.sha, report.get("slug"), fixes))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(body)
    gh.upsert_comment(repo, args.pr, CHECK_MARKER, body)
    gh.set_labels(repo, args.pr, [label], [name for name in LABELS if name != label])
    if re.fullmatch(r"[0-9a-f]{40}", args.sha):
        state, text = status_of(report)
        run_url = f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{repo}/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}"
        gh.request("POST", f"repos/{repo}/statuses/{args.sha}",
                   {"state": state, "context": "rules", "description": text, "target_url": run_url})
    print(f"label: {label}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
