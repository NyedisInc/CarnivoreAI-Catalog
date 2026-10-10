"""The mod kind: one passing folder, one refusal per rule, and a signed mod in the index."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

import pytest

from index_builder.kind_errors import KindRefused
from index_builder.mods import validate_mod
from index_builder.releases import ReleaseRefused

from tests.conftest import requires_minisign
from tests.test_builder import HANDLE
from tests.test_builder import catalog  # noqa: F401  (fixture)
from tests.test_kind_release_flow import _build, _publish

NAME = "working-on"


def good() -> Dict[str, bytes]:
    """A minimal valid mod folder."""
    return {
        ".claude-plugin/plugin.json": json.dumps({
            "name": NAME, "version": "0.1.0", "description": "d",
            "author": {"name": "Nyedis"}, "types": "./types/index.d.ts",
        }).encode(),
        "hooks/hooks.json": b'{ "modules": ["./register.ts"] }',
        "hooks/register.ts": b"export default function register() {}\n",
        "types/index.d.ts": b"export {};\n",
        "README.md": b"# working-on\n",
    }


def manifest(**changes) -> Dict[str, bytes]:
    """good() with plugin.json keys replaced (None removes one)."""
    members = good()
    data = json.loads(members[".claude-plugin/plugin.json"])
    for key, value in changes.items():
        data.pop(key, None) if value is None else data.__setitem__(key, value)
    members[".claude-plugin/plugin.json"] = json.dumps(data).encode()
    return members


def with_file(path: str, data: bytes = b"x") -> Dict[str, bytes]:
    """good() plus one more file."""
    return {**good(), path: data}


def test_a_good_mod_passes() -> None:
    """The pass fixture returns its manifest text."""
    assert json.loads(validate_mod(NAME, good()))["name"] == NAME


@pytest.mark.parametrize("members,code,fragment", [
    (manifest(name="other"), 400, "name does not match"),
    (manifest(author=None), 400, "needs an author"),
    (manifest(hooks={}), 409, "key hooks"),
    (manifest(homepage="ftp://x"), 409, "homepage"),
    (manifest(types="./nope.d.ts"), 409, "types must name"),
    ({**good(), "hooks/hooks.json": b'{"modules": ["./register.ts"], "x": 1}'}, 409, "exactly"),
    ({**good(), "hooks/hooks.json": b'{"modules": ["../x.ts"]}'}, 409, "not a ./ file"),
    ({**good(), "hooks/hooks.json": b'{"modules": ["./missing.ts"]}'}, 409, "not a ./ file"),
    ({**good(), "hooks/hooks.json": b'{"modules": ["./../README.md"]}'}, 409, "README.md"),
    ({**good(), "hooks/hooks.json": b'{"modules": ["./types/x"]}'}, 409, "not a ./ file"),
    (with_file("node_modules/a.js"), 409, "node_modules"),
    (with_file("package.json", b"{}"), 409, "package.json"),
    (with_file("bin/run.sh"), 409, "extension not allowed"),
    (with_file("a.png"), 409, "extension not allowed"),
    (with_file("hooks/b.ts", b"#!/usr/bin/env node\n"), 409, "shebang"),
])
def test_each_rule_refuses(members, code, fragment) -> None:
    """One refusal per rule, with the hosted status code."""
    with pytest.raises(KindRefused, match=fragment) as caught:
        validate_mod(NAME, members)
    assert caught.value.status == code


def test_a_module_that_is_not_source_is_refused() -> None:
    """A hook module must carry a source extension."""
    members = {**good(), "hooks/hooks.json": b'{"modules": ["./data.json"]}', "hooks/data.json": b"{}"}
    with pytest.raises(KindRefused, match="not a source file"):
        validate_mod(NAME, members)


@requires_minisign
def test_a_signed_mod_is_indexed_and_a_hostile_one_refused(catalog: dict, tmp_path: Path) -> None:  # noqa: F811
    """A signed good mod appears with kind mod; the same name carrying a package.json is refused."""
    _publish(catalog, tmp_path, "mod", NAME, good())
    item = next(i for i in _build(catalog["root"]).document["items"] if i["name"] == NAME)
    assert item["kind"] == "mod"
    assert item["versions"][0]["src"]["path"] == f"mods/{HANDLE}/{NAME}"
    assert "grade" not in item["versions"][0]


@requires_minisign
def test_a_hostile_signed_mod_is_refused(catalog: dict, tmp_path: Path) -> None:  # noqa: F811
    """A valid signature does not launder a folder the mod rules refuse."""
    _publish(catalog, tmp_path, "mod", NAME, with_file("package.json", b"{}"))
    with pytest.raises(ReleaseRefused, match="mod rules"):
        _build(catalog["root"])
