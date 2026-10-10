"""Validation of a mod folder: a Claude Code mod, which carries TypeScript hook modules.

A MOD IS THE ONE KIND THAT SHIPS CODE, so it is an ALLOWLIST and the review
step reads every line of it. The app runs it only after the user ticks the
consent sheet; nothing here makes that code safe, it only keeps the folder
to a shape the reviewer and the app can reason about:

* ``.claude-plugin/plugin.json`` (required): keys in ``MANIFEST_KEYS`` only,
  ``author`` required, ``name`` equal to the folder name.
* ``hooks/hooks.json`` (required): exactly ``{"modules": [...]}``, each
  entry a ``./`` path relative to ``hooks/``, an existing file with a
  source extension, never leaving the folder.
* Files: source (``.ts .tsx .mts .cts .js .mjs .cjs .jsx``), ``.json`` and
  ``.md`` only. No ``node_modules/``, no ``package.json``. Symlinks and
  executable files are refused earlier, by ``kind_rules.load_folder``.

It does not join ``kind_rules.KIND_LIMITS`` on purpose: a mod and a plugin
share one manifest path, and that table is inverted to detect a kind from a
manifest, so a second row with the same manifest would turn every plugin into
a mod. Every offence is collected and raised once as 409 ``mod_not_safe``.
"""

from __future__ import annotations

import json
import posixpath
import re
from typing import Dict, List

from .content_rules import content_problem
from .kind_errors import KindRefused

MANIFEST = ".claude-plugin/plugin.json"
HOOKS = "hooks/hooks.json"
MAX_MANIFEST_BYTES = 64 * 1024
MAX_LISTED = 10

MANIFEST_KEYS = frozenset({
    "name", "version", "description", "author", "homepage", "repository",
    "license", "keywords", "types",
})
SOURCE_EXTENSIONS = (".ts", ".tsx", ".mts", ".cts", ".js", ".mjs", ".cjs", ".jsx")
FILE_EXTENSIONS = SOURCE_EXTENSIONS + (".json", ".md")
_URL_RE = re.compile(r"^https?://")


def _refuse(detail: str) -> None:
    """Raise a 400 for a structural mistake.

    Inputs: detail (str).
    Raises: KindRefused 400 mod_invalid.
    Example: _refuse("plugin.json is not utf-8 json")
    """
    raise KindRefused(400, "mod_invalid", detail)


def _json_object(members: Dict[str, bytes], path: str, limit: int) -> dict:
    """Parse one required member as a json object.

    Inputs: members (dict) path to bytes; path (str); limit (int) max bytes.
    Output: the parsed dict.
    Raises: KindRefused 400 mod_invalid when absent, too large or not an object.
    Example: _json_object({"a.json": b"{}"}, "a.json", 10) -> {}
    """
    raw = members.get(path)
    if raw is None:
        _refuse(f"the folder carries no {path}")
    if len(raw) > limit:
        _refuse(f"{path} is larger than {limit} bytes")
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        _refuse(f"{path} is not utf-8 json")
    if not isinstance(parsed, dict):
        _refuse(f"{path} must be a json object")
    return parsed


def _resolve(base: str, ref: object) -> str:
    """Resolve a ``./`` reference against a folder inside the mod.

    Inputs: base (str) posix folder ("" for the root); ref (object) the entry.
    Output: the normalized member path, or "" when it is not a plain ``./``
      path or climbs out of the folder.
    Example: _resolve("hooks", "./register.ts") -> "hooks/register.ts"
    """
    if not isinstance(ref, str) or not ref.startswith("./"):
        return ""
    path = posixpath.normpath(posixpath.join(base, ref))
    return "" if path.startswith("..") or path.startswith("/") else path


def validate_mod(name: str, members: Dict[str, bytes]) -> str:
    """Validate a decoded mod folder and return its plugin.json text.

    Inputs: name (str) the folder name; members (dict) path to content.
    Output: str, the manifest text.
    Raises: KindRefused 400 mod_invalid for a structural mistake, 409
      mod_not_safe naming up to ten offences.
    Example: validate_mod("m", {".claude-plugin/plugin.json": b"{...}", ...})
    """
    manifest = _json_object(members, MANIFEST, MAX_MANIFEST_BYTES)
    hooks = _json_object(members, HOOKS, MAX_MANIFEST_BYTES)
    if manifest.get("name") != name:
        _refuse("plugin.json name does not match the folder name")
    author = manifest.get("author")
    if not (isinstance(author, str) and author.strip()) and not (
        isinstance(author, dict) and isinstance(author.get("name"), str) and author["name"].strip()
    ):
        _refuse("plugin.json needs an author")
    problems: List[str] = [f"{MANIFEST} (key {str(k)[:40]})" for k in manifest if k not in MANIFEST_KEYS]
    for key in ("homepage", "repository"):
        value = manifest.get(key)
        if value is not None and not (isinstance(value, str) and _URL_RE.match(value)):
            problems.append(f"{MANIFEST} ({key} must be an http or https url)")
    if "types" in manifest and _resolve("", manifest["types"]) not in members:
        problems.append(f"{MANIFEST} (types must name a file in the folder)")

    modules = hooks.get("modules")
    if set(hooks) != {"modules"} or not isinstance(modules, list) or not modules:
        problems.append(f'{HOOKS} (must be exactly {{"modules": [...]}} with at least one entry)')
        modules = []
    for entry in modules:
        target = _resolve("hooks", entry)
        if not target or target not in members:
            problems.append(f"{HOOKS} (module {str(entry)[:60]} is not a ./ file inside the folder)")
        elif not target.lower().endswith(SOURCE_EXTENSIONS):
            problems.append(f"{HOOKS} (module {target} is not a source file)")

    for path, data in members.items():
        parts = path.split("/")
        if "node_modules" in parts or parts[-1].lower() == "package.json":
            problems.append(f"{path} (path not allowed)")
        elif not path.lower().endswith(FILE_EXTENSIONS):
            problems.append(f"{path} (extension not allowed)")
        else:
            reason = content_problem(path, data)
            if reason:
                problems.append(f"{path} ({reason})")
    if problems:
        more = f"; and {len(problems) - MAX_LISTED} more" if len(problems) > MAX_LISTED else ""
        raise KindRefused(409, "mod_not_safe", f"a mod may not carry: {'; '.join(problems[:MAX_LISTED])}{more}")
    return members[MANIFEST].decode("utf-8")
