"""Which servers may raise the row's yellow "!": the owner's allow or block list (a port of the app's ``warn_filter.py``).

Settings: ``warnMode`` (``all`` the default, ``allow`` only the listed servers warn, ``block`` the listed servers never warn) and ``servers``
(names separated by commas, semicolons or new lines, matched by :func:`mh_vocab.server_key`). A hidden server is left out of the row entirely.
"""

from __future__ import annotations

import re
from typing import Any, FrozenSet, Iterable, Mapping, Tuple

from mh_vocab import Health, server_key

MODE_ALL, MODE_ALLOW, MODE_BLOCK = "all", "allow", "block"
_SPLIT = re.compile(r"[,;\n]+")


def parse_names(value: Any) -> FrozenSet[str]:
    """The server keys a ``servers`` setting names (a string or a list of strings). Example: parse_names("Memory, figma") == {"memory", "figma"}"""
    parts = value if isinstance(value, list) else _SPLIT.split(value) if isinstance(value, str) else []
    return frozenset(k for k in (server_key(p) for p in parts if isinstance(p, str)) if k)


def warns(settings: Mapping[str, Any], name: str) -> bool:
    """Whether the server called ``name`` may warn under the owner's settings (an unknown mode reads as ``all``). Output: bool."""
    mode, keys = settings.get("warnMode"), parse_names(settings.get("servers", ""))
    if mode == MODE_ALLOW:
        return server_key(name) in keys
    if mode == MODE_BLOCK:
        return server_key(name) not in keys
    return True


def apply(settings: Mapping[str, Any], servers: Iterable[Health]) -> Tuple[Health, ...]:
    """The servers with every hidden one left out. PURE. Inputs: settings; servers. Output: tuple[Health, ...]."""
    return tuple(s for s in servers if warns(settings, s.name))
