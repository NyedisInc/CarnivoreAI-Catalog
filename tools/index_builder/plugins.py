"""Validation of a submitted plugin folder: an allowlist of inert files.

NOTHING A PLUGIN CARRIES MAY RUN WITHOUT A PER-USE DECISION BY THE USER OR
THE MODEL. That is the whole rule. A plugin is therefore an ALLOWLIST of
paths and manifest keys, never a denylist, so a new way of running code
that Claude Code adds later is refused by default:

* ``.claude-plugin/plugin.json`` (required) with the keys in
  ``MANIFEST_KEYS`` only. ``hooks``, ``lspServers``, ``monitors`` and every
  path override key are refused by that allowlist. ``mcpServers`` and a root
  ``.mcp.json`` ARE allowed (2026-10-15, Adam (Adoom666), ``docs/DECISIONS.md``)
  provided each server is structurally sound (``_mcp_problems``); the app's
  install sheet shows the full command and the install is consent-gated.
* ``commands/**.md``, ``agents/**.md``, ``skills/<n>/SKILL.md`` plus text
  references beside it, and a root README and LICENSE.
* Any ``hooks``, ``bin``, ``monitors`` or ``scripts`` segment at any depth,
  ``.lsp.json``, settings files, every other path or
  extension, non UTF-8 bytes, a shebang, front matter keys that grant
  tools or permissions, and the inline shell injection marker are refused.

Every offence is collected first and raised once as 409 ``plugin_not_safe``
listing up to ten paths, so an author fixes everything in one round.
Structural mistakes (no manifest, bad json, wrong name) are 400.

Pure over plain values. THIS IS A PORT of ``hosted/catalog/api/validate_plugin.py`` in the
product repository, held to it by ``tests/fixtures/kind_vectors.json``, which
both repositories run and pin by sha256.
"""

from __future__ import annotations

import json
import re
from typing import Dict, List

from .content_rules import FORBIDDEN_NAMES, FORBIDDEN_SEGMENTS, content_problem
from .kind_errors import KindRefused

MANIFEST = ".claude-plugin/plugin.json"
MAX_MANIFEST_BYTES = 64 * 1024
MAX_LISTED = 10
MAX_MCP_SERVERS = 16
MAX_MCP_BYTES = 16 * 1024
#: The only keys a server entry may carry (mirrors plugin_safe_subset.MCP_ENTRY_KEYS); anything else can pick a transport the consent sheet hides.
MCP_ENTRY_KEYS = frozenset({"command", "args", "env"})

MANIFEST_KEYS = frozenset(
    {"name", "version", "description", "author", "homepage", "repository", "license", "keywords", "mcpServers"}
)
TEXT_REFERENCE_EXTENSIONS = (".md", ".txt", ".json", ".yaml", ".yml")
_ROOT_DOC_RE = re.compile(r"^(?:readme|license)(?:\.md|\.txt)?$", re.IGNORECASE)


def _refuse(code: str, detail: str) -> None:
    """Raise a 400 for a structural mistake.

    Inputs: code (str); detail (str).
    Output: never returns.
    Raises: KindRefused 400.
    Example: _refuse("plugin_invalid", "bad json")
    """
    raise KindRefused(400, code, detail)


def _path_allowed(path: str) -> bool:
    """Whether a path is on the plugin allowlist.

    Inputs: path (str) an already path-checked member path.
    Output: bool.
    Example: _path_allowed("commands/a/b.md") -> True
    """
    parts = path.split("/")
    if path == MANIFEST or path == ".mcp.json":
        return True
    if any(p.lower() in FORBIDDEN_SEGMENTS for p in parts) or FORBIDDEN_NAMES.match(parts[-1]):
        return False
    if len(parts) == 1:
        return bool(_ROOT_DOC_RE.match(path))
    lower = path.lower()
    if parts[0] in ("commands", "agents"):
        return lower.endswith(".md")
    if parts[0] == "skills" and len(parts) >= 3:
        return lower.endswith(TEXT_REFERENCE_EXTENSIONS)
    return False


def _mcp_problems(label: str, servers: object) -> List[str]:
    """Structural problems in one MCP server map (mirror of the app's ``mcp_problems``).

    Not a judgement of the command: it guarantees every server parses and
    can be shown in full on the consent sheet. At most 16 servers and
    16 KiB; each server an object with a non-empty string ``command``,
    ``args`` a list of strings, ``env`` a string to string map.

    Inputs: label (str) where the map came from; servers (object) the value.
    Output: list of "label (reason)" strings.
    Example: _mcp_problems("m", {"a": {"command": "npx"}}) -> []
    """
    if not isinstance(servers, dict):
        return [f"{label} (must be an object of servers)"]
    problems: List[str] = []
    if len(servers) > MAX_MCP_SERVERS or len(json.dumps(servers)) > MAX_MCP_BYTES:
        problems.append(f"{label} (more than {MAX_MCP_SERVERS} servers or 16 KiB)")
    for name, entry in servers.items():
        if not isinstance(entry, dict) or not isinstance(entry.get("command"), str) or not entry["command"].strip():
            problems.append(f"{label} (server {str(name)[:40]} needs a command string)")
            continue
        extra = sorted(str(k)[:20] for k in entry if k not in MCP_ENTRY_KEYS)
        if extra:
            problems.append(f"{label} (server {str(name)[:40]} has unsupported keys {extra})")
        args, env = entry.get("args", []), entry.get("env", {})
        if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
            problems.append(f"{label} (server {str(name)[:40]} args must be a list of strings)")
        if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
            problems.append(f"{label} (server {str(name)[:40]} env must map strings to strings)")
    return problems


def _manifest_problems(name: str, raw: bytes) -> List[str]:
    """Check plugin.json and return the unsafe-key offences it carries.

    Inputs: name (str) the submission name; raw (bytes) plugin.json.
    Output: list of "path (reason)" strings, empty when the keys are safe.
    Raises: KindRefused 400 plugin_invalid for a structural mistake.
    Example: _manifest_problems("p", b'{"name":"p","hooks":{}}')
    """
    if len(raw) > MAX_MANIFEST_BYTES:
        _refuse("plugin_invalid", "plugin.json is larger than 64 KiB")
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        _refuse("plugin_invalid", "plugin.json is not utf-8 json")
    if not isinstance(manifest, dict):
        _refuse("plugin_invalid", "plugin.json must be a json object")
    if manifest.get("name") != name:
        _refuse("plugin_invalid", "plugin.json name does not match the submission name")
    problems = [
        f"{MANIFEST} (key {str(key)[:40]})" for key in manifest if key not in MANIFEST_KEYS
    ]
    for key in ("homepage", "repository"):
        value = manifest.get(key)
        if value is not None and not (
            isinstance(value, str) and re.match(r"^https?://", value)
        ):
            problems.append(f"{MANIFEST} ({key} must be an http or https url)")
    if "mcpServers" in manifest:
        problems.extend(_mcp_problems(f"{MANIFEST} mcpServers", manifest["mcpServers"]))
    return problems


def _mcp_file_problems(data: bytes) -> List[str]:
    """Problems in a root ``.mcp.json`` (its ``mcpServers`` object, or the object itself).

    Inputs: data (bytes). Output: list of problem strings.
    Example: _mcp_file_problems(b'{"a": {"command": "npx"}}') -> []
    """
    if len(data) > MAX_MCP_BYTES:
        return [".mcp.json (larger than 16 KiB)"]
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return [".mcp.json (not utf-8 json)"]
    servers = value["mcpServers"] if isinstance(value, dict) and "mcpServers" in value else value
    return _mcp_problems(".mcp.json", servers)


def validate_plugin(name: str, members: Dict[str, bytes]) -> str:
    """Validate a decoded plugin folder and return its plugin.json text.

    Inputs: name (str) the validated submission name; members (dict) path
      to content.
    Output: str, the manifest text.
    Raises: KindRefused 400 plugin_invalid for a structural mistake,
      409 plugin_not_safe naming up to ten offending paths.
    Example: validate_plugin("p", {".claude-plugin/plugin.json": b'{"name":"p"}'})
    """
    raw = members.get(MANIFEST)
    if raw is None:
        _refuse("submission_invalid", "the folder carries no .claude-plugin/plugin.json")
    problems: List[str] = _manifest_problems(name, raw)
    for path, data in members.items():
        if not _path_allowed(path):
            problems.append(f"{path} (path not allowed)")
        elif path == ".mcp.json":
            problems.extend(_mcp_file_problems(data))
        elif path != MANIFEST:
            reason = content_problem(path, data)
            if reason:
                problems.append(f"{path} ({reason})")
    if problems:
        listed = "; ".join(problems[:MAX_LISTED])
        more = f"; and {len(problems) - MAX_LISTED} more" if len(problems) > MAX_LISTED else ""
        raise KindRefused(
            409, "plugin_not_safe", f"a plugin may not carry: {listed}{more}"
        )
    return raw.decode("utf-8")
