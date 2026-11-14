"""Second half of the submission workflow: opens or updates the pull request for a submission issue.

Usage: python tools/open_pr.py --result <dir from submission.py> --repo <main checkout> --issue N

Runs with the GitHub App token (GH_TOKEN). Before anything is committed, the built folder is checked again against
the repository rules, so only preset.json, meta.json and re-encoded WebP images can reach the branch.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gh  # noqa: E402
import rules  # noqa: E402
from common import LOGIN_RE, SLUG_RE, SUBMISSION_MARKER, clean_text, defang, load_tags  # noqa: E402

REPO = os.environ.get("GITHUB_REPOSITORY", "")
FORM_HINT = "Edit the issue (the **...** menu > **Edit**) to fix it; the check runs again on every edit."


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], check=check, capture_output=True, text=True)


def error_list(errors) -> str:
    return "\n".join(f"- {defang(e.get('message', ''), 400)}" for e in errors[:30])


def report_failure(issue: int, errors) -> None:
    body = (
        f"{SUBMISSION_MARKER}\n"
        "Thanks for the submission! It can't become a pull request yet:\n\n"
        f"{error_list(errors)}\n\n{FORM_HINT}"
    )
    gh.upsert_comment(REPO, issue, SUBMISSION_MARKER, body)
    gh.set_labels(REPO, issue, ["needs-changes"], [])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--result", required=True, type=Path)
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--issue", required=True, type=int)
    args = ap.parse_args()

    result = json.loads((args.result / "result.json").read_text(encoding="utf-8"))
    if not result.get("ok"):
        report_failure(args.issue, result.get("errors") or [{"message": "The submission could not be processed."}])
        return 0

    slug, author = result.get("slug"), result.get("author")
    if not (isinstance(slug, str) and SLUG_RE.match(slug) and isinstance(author, str) and LOGIN_RE.match(author)):
        raise SystemExit("result.json has an invalid slug or author")
    if result.get("issue") != args.issue:
        raise SystemExit("result.json belongs to another issue")

    built = args.result / "presets" / slug
    errors, meta = rules.check_folder(built, load_tags(args.repo), published=False)
    if meta and meta.get("author") != author:
        errors.append(rules.err("meta", "author mismatch"))
    if errors:
        report_failure(args.issue, errors)
        return 0

    branch = f"submission/issue-{args.issue}"
    repo = args.repo
    git(repo, "checkout", "-B", branch, "origin/main")
    target = repo / "presets" / slug
    if target.exists():
        report_failure(args.issue, [{"message": "A preset with this folder name was just added. Edit the title and try again."}])
        return 0
    shutil.copytree(built, target)
    git(repo, "add", "--", f"presets/{slug}")
    # Titles show as plain text in commits and PR titles; only mentions need neutralising there.
    title = clean_text(result.get("title") or slug, single_line=True)[:60].replace("@", "@\u200b")
    git(repo, "-c", f"user.name={os.environ['BOT_NAME']}", "-c", f"user.email={os.environ['BOT_EMAIL']}",
        "commit", "-q", "-m", f"Add preset: {title} (#{args.issue})", "-m", f"Submitted by {author} in #{args.issue}.")

    # Push only when the content changed, so an issue edit that changes nothing doesn't re-run the checks.
    remote_tree = ""
    if git(repo, "fetch", "-q", "origin", branch, check=False).returncode == 0:
        remote_tree = git(repo, "rev-parse", "FETCH_HEAD^{tree}", check=False).stdout.strip()
    local_tree = git(repo, "rev-parse", "HEAD^{tree}").stdout.strip()
    if remote_tree != local_tree:
        git(repo, "push", "-q", "--force", "origin", f"HEAD:refs/heads/{branch}")

    owner = REPO.split("/")[0]
    pr_title = f"Add preset: {title}"
    pr_body = (
        f"{SUBMISSION_MARKER}\n"
        f"Preset submitted by @{author} in #{args.issue}.\n\n"
        f"- Folder: `presets/{slug}/`\n"
        f"- Made by the submission workflow from the issue's attachments (images re-encoded, metadata removed).\n\n"
        "Checks: the rules check and the AI review run on this pull request. A maintainer merges; nothing is "
        "published before that.\n\n"
        f"Closes #{args.issue}\n"
    )
    open_prs = gh.request("GET", f"repos/{REPO}/pulls?state=open&head={owner}:{branch}")
    if open_prs:
        pr = gh.request("PATCH", f"repos/{REPO}/pulls/{open_prs[0]['number']}", {"title": pr_title, "body": pr_body})
        verb = "updated"
    else:
        pr = gh.request("POST", f"repos/{REPO}/pulls", {
            "title": pr_title, "head": branch, "base": "main", "body": pr_body, "maintainer_can_modify": True,
        })
        verb = "opened"
    gh.set_labels(REPO, pr["number"], ["submission"], [])

    gh.upsert_comment(REPO, args.issue, SUBMISSION_MARKER, (
        f"{SUBMISSION_MARKER}\n"
        f"Thanks! Your preset is in pull request #{pr['number']} ({verb}). The checks run there and a maintainer "
        f"reviews it. To change something, edit this issue; the pull request follows.\n"
    ))
    gh.set_labels(REPO, args.issue, [], ["needs-changes"])
    print(f"PR #{pr['number']} {verb}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
