"""One notification when a server the row may warn about goes down, and once when it comes back (a port of the app's ``notices.py``).

Only a server going from ``connected`` to ``down`` or ``needs_auth`` is told, once, never repeated while it stays down; when a server that was told
about is connected again ONE "back" follows. The first time a (session, server) pair is seen is a baseline, never a transition, and nothing is sent
for ``QUIET_AFTER_START`` after the process starts. A server that leaves the row is forgotten. Memory only. The titles are fixed copy.
"""

from __future__ import annotations

import time
from typing import Callable, Dict, Iterable, List, Set, Tuple

from mh_vocab import ALERT_STATES, STATE_CONNECTED, Health, server_key

DOWN_TITLE, BACK_TITLE = "an mcp server went down", "an mcp server is back"
QUIET_AFTER_START = 60.0
_Pair = Tuple[str, str]


class TransitionNotices:
    """Per (session, server) state and the one-notification-per-transition rule. Inputs: clock (monotonic seconds)."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        """Start the quiet window. Output: None."""
        self._clock = clock
        self._started = clock()
        self._last: Dict[_Pair, str] = {}
        self._told: Set[_Pair] = set()

    def observe(self, rows: Iterable[Tuple[str, Iterable[Health]]], enabled: bool) -> List[Tuple[str, str]]:
        """Fold one pass and return what to send. Inputs: rows ((session id, filtered servers)); enabled (the ``notify`` setting). Output: [(session id, title)]."""
        quiet = self._clock() - self._started < QUIET_AFTER_START
        fresh: Dict[_Pair, str] = {}
        send: List[Tuple[str, str]] = []
        for session_id, servers in rows:
            for server in servers:
                pair = (session_id, server_key(server.name))
                before = self._last.get(pair)
                fresh[pair] = server.state
                if before is None or before == server.state:
                    continue
                if before == STATE_CONNECTED and server.state in ALERT_STATES and pair not in self._told:
                    if enabled and not quiet:
                        self._told.add(pair)
                        send.append((session_id, DOWN_TITLE))
                elif server.state == STATE_CONNECTED and pair in self._told:
                    self._told.discard(pair)
                    if enabled and not quiet:
                        send.append((session_id, BACK_TITLE))
        self._last = fresh
        self._told &= set(fresh)
        return send
