"""Decide each MCP server's state in one session from the evidence the host methods hand over. PURE.

A port of the app's ``combine.py`` onto what a sidecar can see. THE LADDER, per server, first rung that answers wins:

1. The session's OWN LINK (``mcp.links``, the client log): a logged give-up, failure, close or needs-auth is the answer even
   while the server is reachable; a logged connected link is connected.
2. The process (stdio, ``alive`` from ``mcp.config``, which the host measures and holds back as null inside the agent's start grace): gone is
   down, even over a connected link, and over an unknown one; running is connected when no link answered.
3. Anything else is UNKNOWN, never connected.

A LOG-ONLY server (built-in, claude.ai connector) whose newest attempt got an HTTP 404 or 501 reads UNKNOWN with reason ``not_found`` (#2627): quiet, outside ALERT_STATES, and made of values every field host already accepts (#2507).

WHAT THIS CANNOT DO, AND THE APP'S VERSION CAN. No carnivore first-hand rung, no reachability probe, no transcript fold: the host
methods carry none of that.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, FrozenSet, Iterable, List, Mapping, Optional, Tuple

from mh_vocab import (
    ALERT_STATES, DROP_DROPPED, DROP_NEEDS_AUTH, REASON_CONNECTED, REASON_NOT_FOUND, REASON_NOT_IMPLEMENTED, REASON_NOT_SEEN, REASON_PROCESS_GONE, REASON_PROCESS_RUNNING, SOURCE_CLIENT_LOG,
    SOURCE_PROBE, SOURCE_PROCESS, STATE_CONNECTED, STATE_DOWN, STATE_NEEDS_AUTH, STATE_UNKNOWN, TRANSPORT_STDIO, TRANSPORT_UNKNOWN, Health, Link,
    ServerSpec)


@dataclass(frozen=True)
class Evidence:
    """Everything known about one session. Fields: specs; excluded (keys never reported); links (key -> Link, or None); alive (spec key -> True | None)."""

    specs: Tuple[ServerSpec, ...]
    excluded: FrozenSet[str] = frozenset()
    links: Optional[Mapping[str, Link]] = None
    alive: Mapping[str, Optional[bool]] = field(default_factory=dict)


def judge(spec: ServerSpec, ev: Evidence) -> Health:
    """One configured server's state, by the ladder in the module docstring. PURE.

    Inputs: spec; ev (Evidence). Output: Health.
    Example: judge(ServerSpec("memory", "stdio", command="node"), Evidence((), alive={"memory": True})).state == "connected"
    """
    link = (ev.links or {}).get(spec.key)
    transport = spec.transport if spec.transport != TRANSPORT_UNKNOWN or link is None else link.transport
    gone = spec.transport == TRANSPORT_STDIO and ev.alive.get(spec.key) is False
    if link is not None and link.state != STATE_CONNECTED and not (gone and link.state == STATE_UNKNOWN):
        return Health(spec.name, transport, link.state, link.reason, SOURCE_CLIENT_LOG)
    if gone:
        return Health(spec.name, transport, STATE_DOWN, REASON_PROCESS_GONE, SOURCE_PROCESS)
    if link is not None:
        return Health(spec.name, transport, link.state, link.reason, SOURCE_CLIENT_LOG)
    if spec.transport == TRANSPORT_STDIO and ev.alive.get(spec.key) is True:
        return Health(spec.name, transport, STATE_CONNECTED, REASON_PROCESS_RUNNING, SOURCE_PROCESS)
    return Health(spec.name, transport, STATE_UNKNOWN, REASON_NOT_SEEN, SOURCE_PROCESS if spec.transport == TRANSPORT_STDIO else SOURCE_PROBE)


def _is_not_implemented(health: Health) -> bool:
    """True when a log-only server's newest attempt got an HTTP 404 or 501: Claude Code's built-in ``claude-artifacts`` never connects. PURE.

    WHY ONLY LOG-ONLY. A server in the user's own config that answers 404 is a real fault and keeps warning (:func:`judge`); a server the user
    never configured is not theirs to fix, and any other failure of it (timeout, 500, refused) still warns. Inputs: health. Output: bool.
    """
    return health.state == STATE_DOWN and health.reason == REASON_NOT_IMPLEMENTED


def judge_session(ev: Evidence) -> Tuple[Health, ...]:
    """Every server of one session: the configured ones and the log-only ones (claude.ai connectors, built-ins), excluded keys left out. PURE.

    Output: tuple[Health, ...] sorted by name. Example: [h.name for h in judge_session(ev) if h.state == "down"] -> ["memory"]
    """
    out: Dict[str, Health] = {}
    for spec in ev.specs:
        if spec.key and spec.key not in ev.excluded and spec.key not in out:
            out[spec.key] = judge(spec, ev)
    for key, link in (ev.links or {}).items():
        if key in out or key in ev.excluded:
            continue
        health = judge(ServerSpec(name=link.name, transport=link.transport, scope="client_log"), ev)
        if _is_not_implemented(health):
            health = Health(health.name, health.transport, STATE_UNKNOWN, REASON_NOT_FOUND, health.source)
        out[key] = Health(health.name, health.transport, health.state, health.reason, health.source, exact=False)
    return tuple(sorted(out.values(), key=lambda h: h.name.lower()))


def as_drops(servers: Iterable[Health]) -> List[Dict[str, object]]:
    """The down and needs-auth servers in the legacy ``dropped`` shape (the reconnect route and older clients read it). PURE.

    Output: list of {name, state, exact, source}.
    """
    return [{"name": s.name, "state": DROP_NEEDS_AUTH if s.state == STATE_NEEDS_AUTH else DROP_DROPPED, "exact": s.exact, "source": s.source}
            for s in servers if s.state in ALERT_STATES]
