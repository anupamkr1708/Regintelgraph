"""Log hygiene (docs/phase1/source-safety-contract.md §11).

Retrieved content, headers and URLs are untrusted data. Anything dynamic that reaches a log line passes through here:
control characters are escaped (no log forging), length is bounded, known secret values are redacted, only allowlisted
headers are ever logged, and bodies / cookies / credentials / the crawler contact value are never logged.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from urllib.parse import urlsplit

LOGGER = logging.getLogger("regintelgraph.ingestion")
MAX_LOG_VALUE = 200
LOGGED_HEADERS = frozenset({"content-type", "content-length", "content-encoding", "etag", "last-modified", "retry-after", "location"})
_REDACTED = "[REDACTED]"


def neutralize(value: object, *, max_len: int = MAX_LOG_VALUE, secrets: Iterable[str] = ()) -> str:
    """Make `value` safe for one log line: redact secrets, escape control chars (incl. newlines), bound the length."""
    text = str(value)
    for secret in secrets:
        if secret:
            text = text.replace(secret, _REDACTED)
    out: list[str] = []
    for ch in text:
        code = ord(ch)
        if code < 0x20 or 0x7F <= code <= 0x9F or ch in "\u2028\u2029":
            out.append(f"\\x{code:02x}" if code < 0x100 else f"\\u{code:04x}")
        else:
            out.append(ch)
    cleaned = "".join(out)
    return cleaned if len(cleaned) <= max_len else cleaned[: max_len - 1] + "…"


def safe_url(url: str, *, secrets: Iterable[str] = ()) -> str:
    """Log form of a URL: scheme://host/path only (no userinfo, query string or fragment)."""
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        shown = f"{parts.scheme}://{host}{parts.path}"
    except ValueError:
        shown = "[unparseable-url]"
    return neutralize(shown, secrets=secrets)


def safe_headers(headers: Mapping[str, str], *, secrets: Iterable[str] = ()) -> dict[str, str]:
    """Allowlisted response headers only, each value neutralised. Set-Cookie, Authorization etc. are never included."""
    return {
        k.lower(): neutralize(v, secrets=secrets)
        for k, v in sorted(headers.items(), key=lambda kv: kv[0].lower())
        if k.lower() in LOGGED_HEADERS
    }
