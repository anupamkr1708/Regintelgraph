"""The secret scanner detects the shapes it claims to, ignores the repo's synthetic placeholders, and never echoes values.
Sample secrets are assembled at runtime so this file itself is clean under the scanner."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.check_secrets import ALLOW_MARKER, scan_text, scan_tree

REPO = Path(__file__).resolve().parents[2]
A = "A" * 36

SAMPLES = {
    "private-key-block": "-----BEGIN " + "RSA PRIVATE KEY-----",
    "aws-access-key-id": "AK" + "IA" + "IOSFODNN7EXAMPLE",
    "github-token": "gh" + "p_" + A,
    "slack-token": "xo" + "xb-" + "1234567890-abcdef",
    "google-api-key": "AI" + "za" + "SyA-" + "1234567890abcdefghijklmnopqrstu",
    "provider-api-key": "s" + "k-" + "ant-" + "abcdefghijklmnopqrstuvwxyz0123",
    "jwt": "ey" + "JhbGciOiJIUzI1NiJ9." + "ey" + "JzdWIiOiIxMjM0NTY3ODkwIn0." + "abcdefghijklmnop",
    "secret-literal-assignment": "api_" + 'key = "' + "abcdefghijklmnopqrstuvwxyz012345" + '"',
    "url-with-credentials": "postgresql://" + "svc:" + "hunter2hunter2" + "@db.internal.example/app",
}


@pytest.mark.parametrize(("rule", "sample"), SAMPLES.items())
def test_each_rule_fires(rule: str, sample: str) -> None:
    assert (1, rule) in scan_text(f"x = 1\n{sample}\n")[:3] or any(r == rule for _, r in scan_text(sample))


@pytest.mark.parametrize(
    "benign",
    [
        "postgresql://postgres:postgres@localhost:5432/postgres",  # throwaway CI service container
        "postgresql://u@/postgres?host=/home/dev/repo/local_data/pgsock",
        "RIG_CRAWLER_CONTACT_EMAIL=",
        "OPENAI_API_KEY=",
        'token = "short"',
        "sha256:" + "a" * 64,
        "s3cr3t-token-123",
        "ops-secret-contact@testreg.example",
        "api_key = os.environ['X']",
        "Authorization: Bearer <token>",
    ],
)
def test_synthetic_placeholders_and_non_secrets_are_not_flagged(benign: str) -> None:
    assert scan_text(benign) == []


def test_documented_opt_out_marker_is_honoured() -> None:
    assert scan_text(SAMPLES["aws-access-key-id"] + f"  # {ALLOW_MARKER}: documented synthetic placeholder") == []


def test_findings_never_contain_the_secret_value(tmp_path: Path) -> None:
    secret = SAMPLES["github-token"]
    (tmp_path / "leak.txt").write_text(f"token: {secret}\n")
    findings = scan_tree(tmp_path)
    assert findings == ["leak.txt:1: github-token"] and secret not in "".join(findings)


def test_binary_and_oversized_files_are_skipped(tmp_path: Path) -> None:
    (tmp_path / "blob.bin").write_bytes(b"\xff\xfe\x00" + SAMPLES["github-token"].encode())
    assert scan_tree(tmp_path) == []


def test_the_repository_itself_is_clean() -> None:
    assert scan_tree(REPO) == []
