# Client compatibility

Agent Budget is a local, read-only MCP stdio server with optional agent
instructions. It is not a universal model-request interceptor. Any host that
supports local MCP tools may be able to use it; not all agent products support
local processes, plugins, or enforceable context controls.

## Status

| Client/surface | Configuration | Verification status |
|---|---|---|
| GitHub Copilot CLI | `mcpServers` JSON / Agent Plugins 1.0 | Live broker search/read verified |
| GitHub Copilot desktop app with Agency | Scoped local plugin and `agency.toml` | Live broker search verified in this pilot |
| Claude Desktop | `mcpServers` JSON | Export and local transport tested; target app not tested |
| Claude Code | Project `.mcp.json` | Export and local transport tested; target app not tested |
| Codex CLI / compatible local Codex hosts | `[mcp_servers.agentbudget]` TOML | Installed CLI accepted config via overrides; local transport tested; model tool use not tested |
| Cursor desktop | `.cursor/mcp.json` | Export and local transport tested; target app not tested |
| Copilot in VS Code, Local harness | `.vscode/mcp.json`, top-level `servers` | Export and local transport tested; target app not tested |
| Copilot in VS Code, Copilot harness | Copilot global `mcpServers` config, or supported workspace-root `.mcp.json` | Same exporter; target harness not tested |
| Other local MCP clients | Generic `mcpServers` JSON starting point | Host-specific configuration required |
| Cloud/web-only agents | Not covered by local stdio | Requires a separately designed remote deployment |

Transport tests start the exact commands exported by each adapter, negotiate MCP,
list tools, search a fixture, and verify call-budget exhaustion. They do not prove
that a proprietary client accepts or uses that configuration.

## Generate configuration

Run from the cloned repository. Index only a folder you want the selected model
to be able to read:

```powershell
python .\agent_budget.py --root C:\work\my-project index
python .\client_config.py --client claude-desktop --root C:\work\my-project
python .\client_config.py --client codex --root C:\work\my-project --output .\codex-snippet.toml
```

Clients: `claude-desktop`, `claude-code`, `cursor`, `codex`, `copilot`, `vscode`,
`generic`. The exporter prints a snippet by default. `--output` creates a new file
and refuses to overwrite an existing one. It does not install plugins or mutate
client settings. If using a custom index directory, pass the same `--state`.
Optional `--response-bytes`, `--evidence-bytes`, and `--calls` change broker limits.

Generated files contain machine-specific absolute paths. Generate them on the
machine that will run the server; do not commit them or copy Windows paths to a
Linux/WSL/remote host. Keep the cloned source at a stable path. All clients share
the same index for a given root, but each server process has a separate budget.

## Client setup

**Claude Desktop:** open Settings > Developer > Edit Config. Merge the exported
`mcpServers.agentbudget` entry into the existing `claude_desktop_config.json`,
preserving other servers. The usual Windows location is
`%APPDATA%\Claude\claude_desktop_config.json`. Fully quit and reopen the app.
Review its normal trust/tool prompts. The portable skill is not automatically
installed or activated by this MCP registration.

**Claude Code:** merge the exported entry into the project's `.mcp.json`. Review
project MCP approval in Claude Code. Optionally install
`skills\agent-budget\SKILL.md` using that client's supported skill mechanism.

**Codex:** merge the exported TOML tables into `~/.codex/config.toml` (or
`$CODEX_HOME/config.toml` where configured), preserving existing tables. Trusted
project `.codex/config.toml` is another supported scope in current documentation.
Avoid duplicate `[mcp_servers.agentbudget]` tables. Use `codex mcp get agentbudget`
to check configuration, then verify an actual search in a session. No approval
bypass or additional filesystem permissions are configured by the exporter.

**Cursor:** merge the exported entry into `.cursor/mcp.json` for project scope
or `~/.cursor/mcp.json` for global scope. Verify the server in Cursor's MCP
settings. MCP configuration does not automatically install the portable skill;
add project instructions using Cursor's supported rules UI if desired.

**GitHub Copilot CLI:** merge into its `mcp-config.json`, or use the existing
plugin/strict-launcher instructions in README. The `copilot` export is also for
VS Code's Copilot global MCP destination where supported.

**Copilot in VS Code:** select the intended harness first. The `vscode` export
uses the `servers` format for the Local harness; merge it into `.vscode/mcp.json`.
Current VS Code documentation calls this a compatibility destination. For a
Copilot harness, use the `copilot` export and the supported Copilot Global or
workspace-root configuration flow instead. Review MCP trust prompts.

**This GitHub Copilot desktop app:** use `package_desktop.py` and a workspace-local
Agency registration as described in README. Standalone CLI installation is not
proof that this app loaded the plugin.

## Verify every client

After configuration/restart, request:

> Use agentbudget_search to find "a term present in my indexed project". Then
> use agentbudget_read on one matching line range. Cite the source path and lines.
> If either tool is unavailable, report the failure rather than substituting
> built-in filesystem tools. Do not edit files.

Inspect the actual calls and result, not just the answer. Changed sources require
reindexing. A process restart resets budgets. After exhaustion, stop the task;
do not reconnect repeatedly to bypass the limit.

Optional behavioral instruction:

> Prefer Agent Budget for local evidence. Search first, read only necessary
> sections, and stop on budget exhaustion. Avoid routine narration. Keep answers
> concise, but expose blockers, uncertainty, and required approvals.

The full portable skill is in `skills/agent-budget/SKILL.md`; use each client's
supported instruction/skill discovery. Instructions are advisory, not a sandbox.

## Enforcement limits

Across all clients the broker enforces only its own response bytes, cumulative
evidence bytes, and tool-attempt limits per process. Other tools, system prompts,
history, reasoning tokens, generated artifacts, and the final reply are controlled
by the host. The Copilot strict launcher additionally narrows tool availability
for a fresh CLI session; this is not inherited by other clients.

No client-wide input/output cap, guaranteed silent mode, editing, hosted endpoint,
automatic session rotation, or quantified token savings is claimed.

## References

Configuration formats were checked against these sources when adding adapters:

- [Claude Desktop local MCP setup](https://modelcontextprotocol.io/docs/develop/connect-local-servers)
- [Codex MCP configuration](https://developers.openai.com/codex/mcp/)
- [Cursor MCP configuration](https://cursor.com/docs/mcp)
- [VS Code MCP setup and harness destinations](https://code.visualstudio.com/docs/agent-customization/mcp-servers)
- [Copilot CLI plugin creation](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/plugins-creating)

Client behavior changes; recheck formats and perform a live tool call after updates.
