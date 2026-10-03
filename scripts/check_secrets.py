#!/usr/bin/env python3
"""Repository-wide secret scan (T-16). Stdlib only, offline, deterministic. CI job: `secret-scan`.

Scans every file that is tracked or would be committed (honours .gitignore) for HIGH-CONFIDENCE credential shapes: private
key blocks, cloud/VCS/chat tokens, provider-style API keys, JWTs, URLs with embedded passwords and long literal values
assigned to secret-looking names. It never prints a matched value (only file, line and rule name).
This is a floor, not a ceiling: a dedicated scanner (e.g. gitleaks) with history scanning is a recommended, separately
reviewed addition (tooling-bootstrap-plan §4). A line may opt out with the marker `rig:allow-secret` plus a reason; use it
only for documented synthetic placeholders.
"""

from __future__ import annotations

import fnmatch
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ALLOW_MARKER = "rig:allow-secret"
MAX_FILE_BYTES = 2 * 1024 * 1024
_LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "[::1]")

RULES: dict[str, re.Pattern[str]] = {
    "private-key-block": re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY"),
    "aws-access-key-id": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "github-token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})\b"),
    "slack-token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    "google-api-key": re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    "provider-api-key": re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_\-]{24,}"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    "secret-literal-assignment": re.compile(
        r"""(?i)\b(?:api[_-]?key|secret|token|passwd|password|private[_-]?key)\b\s*[:=]\s*["'][A-Za-z0-9/+_\-]{24,}["']"""
    ),
}
_URL_CREDENTIALS = re.compile(r"[a-zA-Z][a-zA-Z0-9+.\-]*://([^/\s:@]+):([^/\s@]+)@([^/\s:?#]+)")


def _ignored_dir_patterns(root: Path) -> list[str]:
    """Simple directory-name patterns from .gitignore (no negations, no path separators beyond a leading/trailing slash).

    Used ONLY by the no-git fallback so that a local virtualenv, caches or a project-local database cluster are not mistaken
    for repository content. Real git decides when it is available.
    """
    gitignore = root / ".gitignore"
    if not gitignore.is_file():
        return []
    patterns: list[str] = []
    for raw in gitignore.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "!")):
            continue
        name = line.strip("/")
        if name and "/" not in name:
            patterns.append(name)
    return patterns


def _walk_without_git(root: Path) -> list[Path]:
    ignored = _ignored_dir_patterns(root)
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != ".git" and not any(fnmatch.fnmatch(d, p) for p in ignored))
        found.extend(Path(dirpath) / f for f in sorted(filenames))
    return sorted(found)


def candidate_files(root: Path) -> list[Path]:
    """Files that are (or would be) committed: tracked + untracked-not-ignored. Outside git, every file except .git."""
    if shutil.which("git") is not None:
        res = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard", "-z"], capture_output=True, check=False
        )
        if res.returncode == 0:
            names = sorted({n for n in res.stdout.decode("utf-8", "surrogateescape").split("\0") if n})
            return [root / n for n in names if (root / n).is_file()]
    return _walk_without_git(root)


def scan_text(text: str) -> list[tuple[int, str]]:
    """(line number, rule name) for every finding in `text`. Values are never returned."""
    findings: list[tuple[int, str]] = []
    for number, line in enumerate(text.splitlines(), 1):
        if ALLOW_MARKER in line:
            continue
        for name, pattern in RULES.items():
            if pattern.search(line):
                findings.append((number, name))
        for match in _URL_CREDENTIALS.finditer(line):
            if match.group(3).lower() not in _LOOPBACK_HOSTS:
                findings.append((number, "url-with-credentials"))
    return findings


def scan_tree(root: Path) -> list[str]:
    out: list[str] = []
    for path in candidate_files(root):
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binary / unreadable: nothing to pattern-match
        out.extend(f"{path.relative_to(root).as_posix()}:{n}: {rule}" for n, rule in scan_text(text))
    return out


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    findings = scan_tree(root)
    if findings:
        print("\n".join(findings))
        print(f"RESULT: FAILED ({len(findings)} potential secret(s); values intentionally not printed)")
        return 1
    print("RESULT: no high-confidence secrets found")
    return 0


if __name__ == "__main__":
    sys.exit(main())
