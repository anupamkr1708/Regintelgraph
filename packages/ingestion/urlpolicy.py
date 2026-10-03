"""Static URL validation (docs/phase1/source-safety-contract.md §2, §3). Pure: no DNS, no sockets, no I/O.

Applied to every candidate URL, every redirect hop and every URL extracted as data. Host comparison is EXACT after
normalisation (lower-case, IDNA/punycode, trailing dot removed); suffix matching is never used.

Mapping of rejections to quarantine reason codes (the contract's code list has no finer codes, none are invented):
  scheme not https .......................... SCHEME_NOT_HTTPS
  host/port/userinfo/IP-literal violations .. HOST_NOT_ALLOWED
  viewer-wrapper form ....................... VIEWER_WRAPPER_URL
  path/query/length/encoding violations ..... MANIFEST_SCOPE_MISMATCH  (URL is outside the reviewed manifest scope)
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Collection
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

from packages.domain.ingest import FetchPurpose, QuarantineReason
from packages.domain.manifest import PathPrefixes
from packages.ingestion.errors import UrlRejected

HTTPS_PORT = 443
_BAD_CHARS = re.compile(r"[\x00-\x20\x7f-\x9f\\]")  # control chars, whitespace, backslash (anywhere in the URL)
_ENCODED_SEP = re.compile(r"%(?:2f|5c|00|25)", re.IGNORECASE)  # encoded '/', '\', NUL and '%' (double-encoding)
_VIEWER_PATH = re.compile(r"^/web/?$")


@dataclass(frozen=True, slots=True)
class ValidatedUrl:
    """A URL that passed static validation. `target` is what is sent on the wire (path + reviewed query only)."""

    host: str
    port: int
    path: str
    query: str
    purpose: FetchPurpose

    @property
    def target(self) -> str:
        return self.path + (f"?{self.query}" if self.query else "")

    @property
    def url(self) -> str:
        return f"https://{self.host}{self.target}"


def normalise_host(raw_host: str) -> str:
    """Lower-case, strip one trailing dot, IDNA-encode; non-ASCII that cannot be encoded is rejected."""
    host = raw_host.lower().rstrip(".") if raw_host.endswith(".") else raw_host.lower()
    try:
        host.encode("ascii")
        return host
    except UnicodeEncodeError:
        pass
    try:
        return host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise UrlRejected(QuarantineReason.HOST_NOT_ALLOWED, "host is not valid IDNA") from exc


def _is_ip_literal_form(host: str) -> bool:
    """True for IPv4/IPv6 literals and numeric forms (decimal 2130706433, hex 0x7f.1, octal 0177.0.0.1, short 127.1)."""
    if ":" in host or host.startswith("["):
        return True
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    labels = host.split(".")
    # Every label numeric-looking (decimal / 0x hex / octal) => a legacy inet_aton form; never a legitimate allowlisted name.
    return all(re.fullmatch(r"(?:0[xX][0-9a-fA-F]+|[0-9]+)", label) for label in labels if label) and bool(host)


def _path_prefix_matches(path: str, prefix: str) -> bool:
    if prefix.endswith("/"):
        return path.startswith(prefix)
    return path == prefix or path.startswith(prefix + "/")


def validate_url(
    url: object,
    purpose: FetchPurpose,
    *,
    allowed_hosts: Collection[str],
    path_prefixes: PathPrefixes,
    max_url_length: int,
    listing_urls: Collection[str] = (),
) -> ValidatedUrl:
    """Validate `url` for `purpose` or raise `UrlRejected`. Order: length -> characters -> scheme -> authority -> path -> query."""
    if not isinstance(url, str) or not url:
        raise UrlRejected(QuarantineReason.MANIFEST_SCOPE_MISMATCH, "URL must be a non-empty string")
    if len(url) > max_url_length:  # before any parsing: bounded work on hostile input
        raise UrlRejected(QuarantineReason.MANIFEST_SCOPE_MISMATCH, "URL exceeds the manifest length limit")
    if _BAD_CHARS.search(url):
        raise UrlRejected(QuarantineReason.MANIFEST_SCOPE_MISMATCH, "URL contains control, whitespace or backslash characters")
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise UrlRejected(QuarantineReason.HOST_NOT_ALLOWED, "URL is malformed") from exc

    if parts.scheme.lower() != "https" or not url.lower().startswith("https://"):
        raise UrlRejected(QuarantineReason.SCHEME_NOT_HTTPS, "scheme must be https (no protocol-relative, http, file, data, javascript)")

    netloc = parts.netloc
    if "@" in netloc:
        raise UrlRejected(QuarantineReason.HOST_NOT_ALLOWED, "userinfo is not allowed")
    try:
        port = parts.port
        hostname = parts.hostname
    except ValueError as exc:
        raise UrlRejected(QuarantineReason.HOST_NOT_ALLOWED, "invalid port or authority") from exc
    if not hostname:
        raise UrlRejected(QuarantineReason.HOST_NOT_ALLOWED, "empty host")
    if port not in (None, HTTPS_PORT):
        raise UrlRejected(QuarantineReason.HOST_NOT_ALLOWED, "only port 443 is allowed")
    if _is_ip_literal_form(hostname):
        raise UrlRejected(QuarantineReason.HOST_NOT_ALLOWED, "IP-literal hosts are not allowed")
    host = normalise_host(hostname)
    if host not in set(allowed_hosts):  # EXACT match; never endswith()
        raise UrlRejected(QuarantineReason.HOST_NOT_ALLOWED, "host is not on the manifest allowlist")

    path = parts.path or "/"
    if _VIEWER_PATH.match(path) and "file=" in parts.query:
        raise UrlRejected(QuarantineReason.VIEWER_WRAPPER_URL, "viewer-wrapper URLs are never fetched")
    if not path.startswith("/") or not path.isascii():
        raise UrlRejected(QuarantineReason.MANIFEST_SCOPE_MISMATCH, "path must be absolute ASCII")
    if _ENCODED_SEP.search(path):
        raise UrlRejected(QuarantineReason.MANIFEST_SCOPE_MISMATCH, "encoded separators / double-encoding in path")
    decoded = unquote(path)
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in decoded) or "\\" in decoded:
        raise UrlRejected(QuarantineReason.MANIFEST_SCOPE_MISMATCH, "control characters in decoded path")
    if any(seg in ("..", ".") for seg in decoded.split("/")):
        raise UrlRejected(QuarantineReason.MANIFEST_SCOPE_MISMATCH, "dot segments in path")
    if "//" in path:
        raise UrlRejected(QuarantineReason.MANIFEST_SCOPE_MISMATCH, "empty path segments")
    if not any(_path_prefix_matches(path, p) for p in path_prefixes.for_purpose(purpose)):
        raise UrlRejected(QuarantineReason.MANIFEST_SCOPE_MISMATCH, "path is outside the reviewed prefixes for this purpose")

    query = parts.query  # the fragment (parts.fragment) is dropped here: stripped before use, never sent
    if purpose is FetchPurpose.LISTING:
        candidate = f"https://{host}{path}" + (f"?{query}" if query else "")
        if candidate not in set(listing_urls):
            raise UrlRejected(QuarantineReason.MANIFEST_SCOPE_MISMATCH, "listing URL is not a reviewed manifest entry point")
    elif query:
        raise UrlRejected(QuarantineReason.MANIFEST_SCOPE_MISMATCH, "query strings are only allowed for reviewed listing URLs")
    return ValidatedUrl(host=host, port=HTTPS_PORT, path=path, query=query, purpose=purpose)
