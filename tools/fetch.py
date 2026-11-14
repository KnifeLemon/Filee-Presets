"""Downloads issue attachments, and nothing else.

Only https://github.com/user-attachments/... URLs are accepted. Redirects are followed by hand, each hop must stay on
GitHub's attachment storage hosts, no credentials are ever sent, and every body is size-capped while it streams.
The bytes are only written to disk; callers parse them as JSON or decode them as images.
"""

from __future__ import annotations

import re
import urllib.error
import urllib.parse
import urllib.request

ASSET_URL_RE = re.compile(
    r"https://github\.com/user-attachments/assets/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)
FILE_URL_RE = re.compile(r"https://github\.com/user-attachments/files/\d{1,12}/[A-Za-z0-9._-]{1,120}")

REDIRECT_HOSTS = re.compile(
    r"^(objects\.githubusercontent\.com"
    r"|private-user-images\.githubusercontent\.com"
    r"|user-images\.githubusercontent\.com"
    r"|github-production-user-asset-[0-9a-f]{4,12}\.s3\.amazonaws\.com"
    r"|github-production-repository-file-[0-9a-f]{4,12}\.s3\.amazonaws\.com)$"
)

IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp", "application/octet-stream", "binary/octet-stream"}
JSON_TYPES = {"application/json", "text/plain", "application/octet-stream", "binary/octet-stream"}

MAX_HOPS = 4
TIMEOUT = 30
USER_AGENT = "filee-presets-ci"


class FetchError(Exception):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def is_attachment_url(url: str) -> bool:
    return bool(ASSET_URL_RE.fullmatch(url) or FILE_URL_RE.fullmatch(url))


def _check_hop(url: str) -> None:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.port:
        raise FetchError("the attachment redirected to an unexpected address")
    if not REDIRECT_HOSTS.match(parts.hostname.lower()):
        raise FetchError("the attachment redirected to an unexpected host")


def download(url: str, max_bytes: int, allowed_types: set[str]) -> bytes:
    if not is_attachment_url(url):
        raise FetchError("only files uploaded to this form (github.com/user-attachments) are accepted")
    current = url
    for hop in range(MAX_HOPS + 1):
        req = urllib.request.Request(current, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
        try:
            resp = _opener.open(req, timeout=TIMEOUT)
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308):
                location = e.headers.get("Location", "")
                nxt = urllib.parse.urljoin(current, location)
                _check_hop(nxt)
                current = nxt
                continue
            raise FetchError(f"download failed (HTTP {e.code})") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise FetchError("download failed (network error)") from None
        with resp:
            ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype not in allowed_types:
                raise FetchError(f"unexpected file type ({ctype or 'none'})")
            length = resp.headers.get("Content-Length")
            if length and length.isdigit() and int(length) > max_bytes:
                raise FetchError(f"file is larger than {max_bytes // 1024} KB")
            data = resp.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise FetchError(f"file is larger than {max_bytes // 1024} KB")
            return data
    raise FetchError("too many redirects")
