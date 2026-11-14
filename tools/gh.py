"""A small GitHub REST/GraphQL client (urllib only). The token comes from GH_TOKEN and is never printed."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

API = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")


class GitHubError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"GitHub API {status}: {message}")
        self.status = status


def _token() -> str:
    token = os.environ.get("GH_TOKEN", "")
    if not token:
        raise GitHubError(0, "GH_TOKEN is not set")
    return token


def request(method: str, path: str, body=None, *, accept: str = "application/vnd.github+json"):
    url = path if path.startswith("https://") else f"{API}/{path.lstrip('/')}"
    data = None if body is None else json.dumps(body).encode("utf-8")
    for attempt in range(3):
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {_token()}",
            "Accept": accept,
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "filee-presets-ci",
            **({"Content-Type": "application/json"} if data is not None else {}),
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", "replace")[:300]
            if e.code in (502, 503, 504) and attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            raise GitHubError(e.code, text) from None
    raise GitHubError(0, "unreachable")


def paginate(path: str):
    page = 1
    sep = "&" if "?" in path else "?"
    while True:
        items = request("GET", f"{path}{sep}per_page=100&page={page}")
        if not items:
            return
        yield from items
        if len(items) < 100:
            return
        page += 1


def graphql(query: str, variables: dict):
    result = request("POST", "graphql", {"query": query, "variables": variables})
    if result.get("errors"):
        raise GitHubError(200, json.dumps(result["errors"])[:300])
    return result["data"]


def upsert_comment(repo: str, number: int, marker: str, body: str) -> str:
    """Edits this tool's earlier comment (found by its marker) or adds a new one. Returns the comment URL."""
    for c in paginate(f"repos/{repo}/issues/{number}/comments"):
        if (c.get("body") or "").startswith(marker) and (c.get("user") or {}).get("type") == "Bot":
            return request("PATCH", f"repos/{repo}/issues/comments/{c['id']}", {"body": body})["html_url"]
    return request("POST", f"repos/{repo}/issues/{number}/comments", {"body": body})["html_url"]


def set_labels(repo: str, number: int, add: list[str], remove: list[str]) -> None:
    for name in remove:
        try:
            request("DELETE", f"repos/{repo}/issues/{number}/labels/{name}")
        except GitHubError as e:
            if e.status != 404:
                raise
    if add:
        request("POST", f"repos/{repo}/issues/{number}/labels", {"labels": add})
