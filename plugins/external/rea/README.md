# rea (Claude Code plugin)
Wraps the `rea-agents` reverse-engineering MCP server (npm, MIT; registry id io.github.morluto/rea).
Enabling this plugin registers one MCP server, `rea`, launched as `npx -y rea-agents@6.5.0 mcp`.
First call fetches the package over the network. Full binary RE uses an existing Hopper/Ghidra/IDA; JavaScript and static binary inspection work with none.
