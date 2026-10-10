"""First-party theme rules: code is allowed for the first-party handle only.

A theme of the first-party publisher (``FIRST_PARTY_HANDLE``) may carry an
effects script, the effects base it imports and a mascot svg. Everyone else
stays data only, and a retired handle is refused for any item.
"""

from __future__ import annotations

import json
from typing import Dict

import pytest

from index_builder.kind_errors import KindRefused
from index_builder.kind_rules import (
    BUNDLED_THEME_IDS,
    FIRST_PARTY_HANDLE,
    FIRST_PARTY_THEME_IDS,
    RESERVED_NAMES,
    RETIRED_THEME_IDS,
    validate_folder,
)
from tests.test_kind_vectors import VECTORS, members_of

#: A retired handle, spelled in pieces so this repository never repeats it.
RETIRED_HANDLE = "".join(["ado", "om", "666"])


def _theme(name: str, **extra: bytes) -> Dict[str, bytes]:
    """A valid theme folder: the shared ``theme-ok`` vector renamed, plus extras."""
    vector = next(v for v in VECTORS if v["id"] == "theme-ok")
    members = members_of(vector)
    manifest = json.loads(members["theme.json"])
    manifest["id"] = name
    members["theme.json"] = json.dumps(manifest).encode()
    members.update(extra)
    return members


def _with_manifest(members: Dict[str, bytes], **keys: object) -> Dict[str, bytes]:
    """The same folder with extra theme.json keys."""
    manifest = {**json.loads(members["theme.json"]), **keys}
    return {**members, "theme.json": json.dumps(manifest).encode()}


def _code(name: str) -> Dict[str, bytes]:
    """A theme with effects.js, effects-base.js and mascot.svg, wired in theme.json."""
    members = _theme(
        name,
        **{
            "effects.js": b"import { base } from './effects-base.js';\nbase();\n",
            "effects-base.js": b"export function base() {}\n",
            "mascot.svg": b"<svg xmlns='http://www.w3.org/2000/svg'/>",
        },
    )
    return _with_manifest(members, effects="effects.js", mascot="mascot.svg")


def _refused(name: str, members: Dict[str, bytes], handle: str) -> KindRefused:
    with pytest.raises(KindRefused) as caught:
        validate_folder(name, members, handle=handle)
    return caught.value


def test_the_name_lists_are_the_apps() -> None:
    assert (len(BUNDLED_THEME_IDS), len(FIRST_PARTY_THEME_IDS), len(RETIRED_THEME_IDS)) == (12, 16, 11)
    assert len(RESERVED_NAMES["theme"]) == 39
    assert not {"cannabis", "black_market"} & RESERVED_NAMES["theme"]


def test_first_party_theme_with_code_passes() -> None:
    kind, _ = validate_folder("snes", _code("snes"), handle=FIRST_PARTY_HANDLE)
    assert kind == "theme"


def test_a_first_party_theme_may_carry_characters() -> None:
    members = _with_manifest(_theme("gameboy"), characters={"pack": "pixel"})
    assert validate_folder("gameboy", members, handle=FIRST_PARTY_HANDLE)[0] == "theme"


def test_a_community_handle_cannot_take_a_first_party_name() -> None:
    assert _refused("snes", _theme("snes"), "someone").code == "name_reserved"


def test_a_community_theme_with_code_is_refused() -> None:
    assert _refused("x", _code("x"), "someone").code == "scripts_not_supported"
    assert _refused("x", _theme("x", **{"effects.js": b"1"}), "someone").code == "scripts_not_supported"


def test_a_first_party_import_outside_the_folder_is_refused() -> None:
    members = _code("x")
    members["effects.js"] = b"import { base } from '../_shared/effects-base.js';\n"
    assert _refused("x", members, FIRST_PARTY_HANDLE).code == "theme_import_unresolved"


def test_a_first_party_dynamic_import_is_checked_too() -> None:
    members = _code("x")
    members["effects.js"] = b"const m = import('./missing.js');\n"
    assert _refused("x", members, FIRST_PARTY_HANDLE).code == "theme_import_unresolved"


@pytest.mark.parametrize("name", ["matrix", "ascii-raven"])
def test_bundled_and_retired_names_are_refused_even_for_first_party(name: str) -> None:
    assert _refused(name, _theme(name), FIRST_PARTY_HANDLE).code == "name_reserved"


@pytest.mark.parametrize("path", ["page.html", "style.css", "mod.mjs", "dir/effects.js"])
def test_first_party_still_refuses_markup_styles_and_subfolders(path: str) -> None:
    refused = _refused("x", _theme("x", **{path: b"x"}), FIRST_PARTY_HANDLE)
    assert refused.code in {"scripts_not_supported", "theme_file_not_allowed"}


def test_a_retired_handle_is_refused_for_any_item() -> None:
    assert _refused("x", _theme("x"), RETIRED_HANDLE).code == "handle_retired"
    assert _refused("x", _theme("x"), RETIRED_HANDLE.upper()).code == "handle_retired"
    skill = {"SKILL.md": b"---\nname: x\ndescription: a skill\n---\nbody\n"}
    assert _refused("x", skill, RETIRED_HANDLE).code == "handle_retired"
