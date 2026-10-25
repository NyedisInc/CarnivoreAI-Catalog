"""The warezsmoke entry: one read tool that answers pong. It exists to prove the store path, not to do work."""

from __future__ import annotations

from typing import Any, Dict

from cplug_sdk import Plugin

plugin = Plugin()


@plugin.tool("warezsmoke_ping", "Answers pong. Reads nothing and changes nothing.", {"type": "object", "properties": {}, "additionalProperties": False}, gate="read")
async def ping(args: Dict[str, Any], caller: Any) -> str:
    """The ping tool. Inputs: args; caller. Output: the text pong."""
    return "pong"
