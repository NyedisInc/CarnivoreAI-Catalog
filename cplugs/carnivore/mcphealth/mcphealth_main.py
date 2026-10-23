"""The mcphealth entry: judges which MCP servers each live session has and which are down, from the host's three redacted methods.

The host wakes this process with an ``mcp.health`` event when a row would change; it answers ``ui.invalidate``, the host asks ``ui.render``
for the ``status_source`` surface and this module measures nothing itself: it reads ``mcp.config`` (with each stdio server's ``alive``) and ``mcp.links``,
judges each session (``mh_combine``), applies the owner's warn filter (``mh_warn_filter``), sends the transition notifications (``mh_notices``)
and returns ``data.sessions.<session id> = {servers, dropped}``, which the app's listing reads into each row's ``mcp_health``.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional

from cplug_sdk import HostError, Plugin

import mh_warn_filter
from mh_combine import Evidence, as_drops, judge_session
from mh_notices import TransitionNotices
from mh_vocab import Health, Link, ServerSpec, server_key

SURFACE = "status_source"
MAX_DOC_BYTES = 56 * 1024
MAX_SERVERS = 40


def _specs(row: Mapping[str, Any]) -> List[ServerSpec]:
    """A ``mcp.config`` session row's servers. Inputs: row. Output: list[ServerSpec]."""
    return [ServerSpec(str(s.get("name") or ""), str(s.get("transport") or "unknown"), str(s.get("scope") or ""), s.get("command"),
                       s.get("alive") if isinstance(s.get("alive"), bool) else None)
            for s in row.get("servers") or () if isinstance(s, Mapping)]


def _links(row: Optional[Mapping[str, Any]]) -> Optional[Dict[str, Link]]:
    """A ``mcp.links`` session row's links by key, or None when the session has no client log. Inputs: row. Output: dict | None."""
    links = [Link(str(l.get("name") or ""), str(l.get("transport") or "unknown"), str(l.get("state") or "unknown"), str(l.get("reason") or ""))
             for l in (row or {}).get("links") or () if isinstance(l, Mapping)]
    return {l.key: l for l in links if l.key} or None


def judge_all(config: Mapping[str, Any], links: Mapping[str, Any]) -> Dict[str, Iterable[Health]]:
    """Every measured session's judged servers, from the three method answers. PURE.

    Inputs: config / links (the answers of ``mcp.config``, ``mcp.links``). Output: {session id: servers}.
    """
    linked = {r.get("session"): r for r in links.get("sessions") or () if isinstance(r, Mapping)}
    out: Dict[str, Iterable[Health]] = {}
    for row in config.get("sessions") or ():
        sid = row.get("session") if isinstance(row, Mapping) else None
        if not isinstance(sid, str):
            continue
        specs = _specs(row)
        excluded = frozenset(k for k in row.get("excluded") or () if isinstance(k, str))
        ev = Evidence(tuple(specs), excluded, _links(linked.get(sid)), {s.key: s.alive for s in specs if s.alive is not None})
        out[sid] = judge_session(ev)
    return out


def build_document(settings: Mapping[str, Any], judged: Mapping[str, Iterable[Health]]) -> Dict[str, Any]:
    """The ``status_source`` document: one entry per session under ``data.sessions``, hidden servers left out, capped to the host's size limit.

    Inputs: settings (the owner's); judged (:func:`judge_all`). Output: the document. Session ids and server names go in as they are (``adopted:<tmux>``, ``plugin:x:y``): the host lets ``data`` carry them.
    """
    sessions: Dict[str, Any] = {}
    size = 0
    for sid, servers in sorted(judged.items()):
        shown = list(mh_warn_filter.apply(settings, servers))[:MAX_SERVERS]
        entry = {"servers": [{"name": s.name, "transport": s.transport, "state": s.state, "reason": s.reason, "source": s.source, "exact": s.exact}
                             for s in shown], "dropped": as_drops(shown)}
        size += len(sid) + len(repr(entry))
        if size > MAX_DOC_BYTES:
            break
        sessions[sid] = entry
    return {"schema": 1, "root": {"type": "section", "title": "mcp health", "children": [
        {"type": "text", "value": f"{len(sessions)} sessions measured", "tone": "muted"}]}, "data": {"sessions": sessions}}


class HealthPlugin(Plugin):
    """The SDK plugin plus the one request it does not route: ``ui.render``."""

    def __init__(self) -> None:
        """Start the notification state. Output: None."""
        super().__init__()
        self.notices = TransitionNotices()

    async def render(self, surface: Any) -> Dict[str, Any]:
        """Answer ``ui.render``: measure through the host, judge, notify, document. Inputs: surface. Output: {document}. Raises KeyError for another surface."""
        if surface != SURFACE:
            raise KeyError(surface)
        config, links = await self.host.mcp_config(), await self.host.mcp_links()
        judged = judge_all(config, links)
        shown = {sid: mh_warn_filter.apply(self.settings, servers) for sid, servers in judged.items()}
        for sid, title in self.notices.observe(shown.items(), self.settings.get("notify") is True):
            try:
                await self.host.notify.toast(ref=sid, title=title)
            except HostError as exc:  # the owner withheld notify, or the hourly limit: the state is still tracked
                await self.host.log("info", "mcphealth_toast_refused", reason=exc.reason)
        return {"document": build_document(self.settings, judged)}

    async def handle(self, method: str, params: Dict[str, Any]) -> Any:
        """Host requests: ``ui.render`` here, everything else the SDK's. Inputs: method; params. Output: the result."""
        if method == "ui.render":
            return await self.render(params.get("surface"))
        return await super().handle(method, params)


plugin = HealthPlugin()


@plugin.on_event("mcp.health")
async def on_health(seq: Any, payload: Any) -> None:
    """Something an MCP row would show moved: ask the host to re-render the status document. Inputs: seq; payload (a session count)."""
    await plugin.host.call("ui.invalidate", {"surface": SURFACE})
