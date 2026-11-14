"""Runs Filee's own validator, `filee presets check <folder> --json`, and reads its answer defensively.

FILEE_CLI holds the command prefix, for example `dotnet /tmp/filee-cli/filee-cli.dll`. The CLI is built by the
workflow from KnifeLemon/Filee (ref in the FILEE_REF repository variable); it is our own code, never the submitter's.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

from common import FORMAT_ID_RE, KINDS, SEVERITIES, VERSION_RE, WARNING_CODE_RE

TIMEOUT = 300


def _error(code: str, message: str) -> dict:
    return {"ok": False, "errors": [{"code": code, "message": message}], "warnings": [], "derived": None}


def _parse(stdout: str):
    stdout = stdout.strip()
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        pass
    start = stdout.find("{")
    end = stdout.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(stdout[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None


def _messages(items) -> list[dict]:
    out = []
    if not isinstance(items, list):
        return out
    for item in items[:50]:
        if isinstance(item, dict):
            code = str(item.get("code") or "error")[:60]
            where = [str(item[k])[:120] for k in ("file", "path") if isinstance(item.get(k), str) and item[k]]
            message = ": ".join(where + [str(item.get("message") or item.get("text") or code)])[:400]
        else:
            code, message = "error", str(item)[:400]
        out.append({"code": code, "message": message})
    return out


def _derived(data) -> dict | None:
    if not isinstance(data, dict):
        return None
    kinds = [k for k in data.get("kinds") or [] if isinstance(k, str) and k in KINDS]
    formats = data.get("formats") if isinstance(data.get("formats"), dict) else {}

    def ids(values):
        seen = []
        for v in values if isinstance(values, list) else []:
            if isinstance(v, str) and FORMAT_ID_RE.match(v.lower()) and v.lower() not in seen:
                seen.append(v.lower())
        return seen[:40]

    min_version = data.get("minVersion")
    if not (isinstance(min_version, str) and VERSION_RE.match(min_version)):
        min_version = None
    return {
        "kinds": sorted(set(kinds)),
        "formats": {"from": ids(formats.get("from")), "to": ids(formats.get("to"))},
        "minVersion": min_version,
        "warnings": _warnings(data["warnings"]) if "warnings" in data else None,
    }


def _warnings(items) -> list[dict]:
    """What the preset can do that people should know before installing it (`derived.warnings`). Older validators
    don't send the field; then `warnings` is None and meta.json leaves it out."""
    out = []
    for item in items[:20] if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        code, severity = item.get("code"), item.get("severity")
        if isinstance(code, str) and WARNING_CODE_RE.match(code) and severity in SEVERITIES:
            if not any(w["code"] == code for w in out):
                out.append({"code": code, "severity": severity, "message": str(item.get("message") or code)[:300]})
    return out


def check(folder: Path, cli: str) -> dict:
    """Returns {ok, errors[], warnings[], derived{kinds, formats, minVersion} | None}."""
    if not cli:
        return _error("validator-missing", "The Filee validator is not configured (FILEE_CLI).")
    cmd = shlex.split(cli) + ["presets", "check", str(folder), "--json"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT, encoding="utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired) as e:
        return _error("validator-failed", f"The Filee validator could not run ({type(e).__name__}).")
    data = _parse(proc.stdout)
    if not isinstance(data, dict) or not isinstance(data.get("ok"), bool):
        return _error("validator-output", f"The Filee validator gave no readable result (exit code {proc.returncode}).")
    result = {
        "ok": data["ok"] and proc.returncode == 0,
        "errors": _messages(data.get("errors")),
        "warnings": _messages(data.get("warnings")),
        "derived": _derived(data.get("derived")),
    }
    if result["ok"] and result["derived"] is None:
        result["ok"] = False
        result["errors"].append({"code": "validator-output", "message": "The Filee validator returned no derived fields."})
    if result["ok"] and result["derived"]["minVersion"] is None:
        result["ok"] = False
        result["errors"].append({"code": "validator-output", "message": "The Filee validator returned no minimum version."})
    if not result["ok"] and not result["errors"]:
        result["errors"].append({"code": "validator-failed", "message": f"The Filee validator failed (exit code {proc.returncode})."})
    return result
