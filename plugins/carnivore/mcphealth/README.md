# mcp health

A Carnivore plugin (kind cplug, runtime sidecar). It shows which MCP servers each live session has and which are down, puts the yellow "!" on a
session row, and can notify once when a server drops and once when it is back.

It measures nothing itself. The app measures and hands it three redacted answers (`mcp.config` with each stdio server's `alive`, `mcp.links`, permission
`mcp.health.read`); this plugin judges them and answers the app's `status_source` surface. Settings: `warnMode` (`all`, `allow`, `block`), `servers`
(the names the list applies to) and `notify`.

What it cannot see that the compiled-in version could: carnivore's own first-hand record, the reachability probe, the transcript fold and the agent
start time. A stdio server whose process is not found reads as unknown, never down, because the host method carries command basenames only.

Package source: `packages/cplugs/mcphealth/` in the app repository. Unsigned here; the catalog copy and the signature are a separate step.
Files: `cplug.json` and `carnivore.json` (manifests), `mcphealth_main.py` (entry), `mh_*.py` (vocabulary, judge, warn filter, notices).
