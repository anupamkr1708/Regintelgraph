"""Only `yaml.safe_load` may construct objects from YAML. Static scan + behavioural proof."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from packages.ingestion.errors import ManifestError
from packages.ingestion.manifest import parse_manifest

REPO = Path(__file__).resolve().parents[2]
ALLOWED_YAML_ATTRS = {
    "safe_load",
    "YAMLError",
    "parse",
    "compose",
    "SafeLoader",
    "AliasEvent",
    "MappingNode",
    "SequenceNode",
    "ScalarNode",
    "Node",
}
SELF = Path(__file__).resolve()


def _python_files() -> list[Path]:
    roots = [REPO / "packages", REPO / "scripts", REPO / "apps", REPO / "workers", REPO / "tests"]
    return sorted(p for r in roots if r.is_dir() for p in r.rglob("*.py") if p.resolve() != SELF)


def test_no_unsafe_yaml_api_anywhere_in_the_repository() -> None:
    offenders: list[str] = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "yaml"
                and node.attr not in ALLOWED_YAML_ATTRS | {"safe_dump"}
            ):
                offenders.append(f"{path.relative_to(REPO)}:{node.lineno}: yaml.{node.attr}")
            if isinstance(node, ast.ImportFrom) and node.module == "yaml" and any(a.name not in ALLOWED_YAML_ATTRS for a in node.names):
                offenders.append(f"{path.relative_to(REPO)}:{node.lineno}: from yaml import ...")
    assert offenders == []


@pytest.mark.parametrize(
    "payload",
    [
        "x: !!python/object/apply:os.system ['echo pwned']\n",
        "x: !!python/object/apply:pathlib.Path.touch [/tmp/rig-yaml-pwned]\n",
        "x: !!python/name:os.system\n",
    ],
)
def test_python_object_tags_are_rejected_without_side_effects(payload: str) -> None:
    marker = Path("/tmp/rig-yaml-pwned")
    marker.unlink(missing_ok=True)
    with pytest.raises(ManifestError):
        parse_manifest(payload.encode())
    assert not marker.exists()
