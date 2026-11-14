"""Posts the check result on a preset pull request and sets its label.

Usage: python tools/report.py --report <report.json> [--verdict <verdict.json>] --pr N [--dry-run]

Labels: `needs-changes` when the rules fail, `ready` only when the rules pass with no warnings AND the AI review ran
and found nothing, `needs-review` for everything else. The AI review can never do more than choose between `ready`
and `needs-review`; merging is always a maintainer's click.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import CHECK_MARKER, defang  # noqa: E402

LABELS = ["ready", "needs-review", "needs-changes"]
FLAG_TEXT = {
    "spam": "Spam or placeholder",
    "offensive": "Offensive content",
    "misleading": "Misleading or steering the review",
    "personal_info_in_images": "Personal information in images",
}


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


def render(report: dict, verdict: dict | None, label: str) -> str:
    lines = [CHECK_MARKER, "## Preset check", ""]
    slug = report.get("slug")
    if slug:
        lines.append(f"Folder: `presets/{defang(slug, 48)}/` ({defang(report.get('mode') or '?', 10)})")
        lines.append("")
    errors = report.get("errors") or []
    if report.get("incomplete"):
        lines.append("**Rules:** did not finish. " + defang(errors[0].get("message", ""), 300))
    elif report.get("ok"):
        lines.append("**Rules:** passed.")
    else:
        lines.append(f"**Rules:** {len(errors)} problem(s) to fix:")
        lines += [f"- {defang(e.get('message', ''), 400)}" for e in errors[:30]]
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
    body = render(report, verdict, label)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(body)
    if args.dry_run:
        print(body)
        print(f"label: {label}")
        return 0
    import gh

    repo = os.environ["GITHUB_REPOSITORY"]
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
