"""The vocabulary mcphealth judges with: states, reason and source codes, and the three shapes the host methods carry.

Copied from the app's ``src/core/mcp_health`` on purpose: a package imports nothing from the app. The strings are the
wire values the host reads back out of the ``status_source`` document, so they must not drift.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

TRANSPORT_STDIO, TRANSPORT_HTTP, TRANSPORT_SSE, TRANSPORT_PROXY, TRANSPORT_UNKNOWN = "stdio", "http", "sse", "proxy", "unknown"
STATE_CONNECTED, STATE_DOWN, STATE_NEEDS_AUTH, STATE_UNKNOWN = "connected", "down", "needs_auth", "unknown"
#: States the row's yellow "!" reports.
ALERT_STATES = frozenset({STATE_DOWN, STATE_NEEDS_AUTH})
SOURCE_CLIENT_LOG, SOURCE_PROCESS, SOURCE_PROBE = "client_log", "process", "probe"
REASON_CONNECTED, REASON_PROCESS_RUNNING, REASON_NOT_SEEN, REASON_PROCESS_GONE = "connected", "process_running", "not_seen", "process_gone"
#: The legacy ``dropped`` list states.
DROP_DROPPED, DROP_NEEDS_AUTH = "dropped", "needs_auth"

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def server_key(name: str) -> str:
    """The comparison key of a server name: lowercase, every run of other characters one ``_``.

    Inputs: name (str). Output: str. Example: server_key("claude.ai Claude Docs") == "claude_ai_claude_docs"
    """
    return _NON_ALNUM.sub("_", name.lower()).strip("_")


@dataclass(frozen=True)
class ServerSpec:
    """One configured server as ``mcp.config`` lists it. Fields: name; transport; scope; command (executable basename or None); alive (True, False or None: the host holds False back inside the agent's start grace)."""

    name: str
    transport: str
    scope: str = ""
    command: Optional[str] = None
    alive: Optional[bool] = None

    @property
    def key(self) -> str:
        """The comparison key of the name. Output: str."""
        return server_key(self.name)


@dataclass(frozen=True)
class Link:
    """One server's link in the session's own client log, as ``mcp.links`` lists it. Fields: name (log directory name); transport; state; reason."""

    name: str
    transport: str
    state: str
    reason: str

    @property
    def key(self) -> str:
        """The comparison key of the name. Output: str."""
        return server_key(self.name)


@dataclass(frozen=True)
class Health:
    """One server's judged state in one session. Fields: name; transport; state; reason; source; exact (name is the exact config name)."""

    name: str
    transport: str
    state: str
    reason: str
    source: str
    exact: bool = True
