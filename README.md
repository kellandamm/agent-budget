# Agent Budget

Portable skill + local MCP evidence broker + experimental Copilot CLI adapter.
Python 3.12+; standard library only. Nothing is installed globally. No model is
called until you explicitly launch Copilot and approve its normal permissions.

## Multi-agent setup

See [client compatibility and setup](COMPATIBILITY.md) for Claude Desktop,
Claude Code, Codex, Cursor, GitHub Copilot CLI/desktop/VS Code, and other local
MCP clients. Export a client-specific configuration without modifying its settings:

```powershell
python .\client_config.py --client cursor --root C:\path\to\project
```

Index the workspace first. Client configuration support is not the same as
live verification in every target application. Hosted/cloud agents are not yet
supported by this local stdio server.

## Try it on Windows

From this directory, choose a workspace you want the agent to read:

```powershell
python .\agent_budget.py --root C:\path\to\project index
python .\agent_budget.py --root C:\path\to\project search "authentication"
python .\agent_budget.py --root C:\path\to\project read "src\auth.py" --start 20 --lines 30
python .\agent_budget.py --root C:\path\to\project copilot "Locate the authentication entry point"
```

The last command prints the launch configuration, without invoking a model.
Add `--run` to open a fresh interactive Copilot CLI session. The adapter exposes
only the broker's two read-only tools via `--available-tools`; it does not grant
blanket tool/path permissions, suppress questions, or disable project instructions.
It discovers and disables other configured MCP servers for this run only.
The installed CLI must support the flags shown in the configuration.
MCP tool-name filtering should be reverified after CLI updates.
Empty tool lists must be treated as integration failure,
not permission to enable unrestricted tools. This is not a security sandbox.

## Load or install as a plugin

The package includes an Agent Plugins 1.0 manifest, the portable skill, and a
stdio MCP server registration. Python 3.12+ must be available as `python` on the
host's PATH. From this directory:

```powershell
$env:AGENT_BUDGET_ROOT = 'C:\path\to\project'
python .\agent_budget.py --root $env:AGENT_BUDGET_ROOT index
copilot -C $env:AGENT_BUDGET_ROOT --plugin-dir .
```

Use an absolute plugin path if launching from another directory. To install
persistently instead of loading for one session:

```powershell
copilot plugin install .
```

In subsequent sessions, set `AGENT_BUDGET_ROOT` before starting Copilot and ask it
to use the `agent-budget` skill. Installation does not set this variable, index
files, or enable the plugin in this already-running conversation. Restart the
host after changing environment variables. A GUI launched outside this terminal
will not inherit them. If you indexed with `--state`, also set the absolute
`AGENT_BUDGET_STATE` to that same directory.

The server refuses to start without an explicit absolute workspace root and an
existing matching index. It never falls back to the host's current directory or
automatically indexes a new workspace. Rebuild the index yourself after changes.
The broker budget lasts for one server process, not necessarily one chat turn
or task; reconnecting resets it.

**Plugin mode bounds only its own tools.** It does not hide built-in tools, reduce
host system instructions, force skill activation, or enforce silent replies.
For the stricter read-only tool allowlist, use the `copilot` launcher command
above instead. The launcher embeds the same policy in its initial prompt since
it intentionally does not expose a skill-loading tool.

Reinstall after modifying the package because Copilot caches installed plugins.
The packaged MCP server was exercised in a live `--plugin-dir` session: Copilot
loaded both tools and successfully searched through the broker. On the tested
CLI, `mcp list --json` did not show a session-mounted plugin even though the
live session loaded it; do not use that command alone to diagnose this mode.
To distribute it, place this directory's contents at the root of a Git repository;
compatible users can then install by repository URL. No repository is published
by this prototype. Other clients must support Agent Plugins 1.0 (including MCP
variable expansion); otherwise copy the skill and configure MCP manually.

### GitHub Copilot desktop app with Agency

The desktop app's Agency configuration is separate from standalone CLI plugin
installation. `package_desktop.py` creates a self-contained plugin copy with an
absolute Python executable, a fixed workspace root, and its matching index path.
It requires an existing index, never sets global environment variables, and
refuses to overwrite a package directory.

```powershell
python .\package_desktop.py --root C:\path\to\project --destination C:\path\to\desktop-plugin-v1
```

In the app session's effective Agency configuration, add this plugin alongside
existing entries (do not replace unrelated configuration):

```toml
[[plugins.default]]
plugin = 'local:C:\path\to\desktop-plugin-v1'
engines = ["copilot"]
```

Validate with `agency config check <config-file> --skip-remotes`, then restart
the app session with `/restart-session`. Confirm `agent-budget` appears under
Plugins, its skill under Skills, and `agentbudget` under MCP servers. Ask the agent
to use the skill and perform a broker search. Configuration validation alone
does not prove the running app has loaded the tools.

The generated package is a snapshot: after source changes, rebuild the index and
generate a new versioned package, update the local plugin path, and restart.
This pilot does not alter other sessions, remove tools, or enforce total model
input/output limits.

For another MCP client, configure a stdio server with `python` as command and
absolute arguments equivalent to:

```text
agent_budget.py --root <workspace> serve
```

Use the absolute Python executable and script path. Keep the same server process
for one task so cumulative retrieval/call budgets apply.

## What is actually enforced

The broker caps each JSON evidence payload at 6,000 UTF-8 bytes, cumulative
evidence/error payloads at approximately 24,000 bytes, and tool execution at 12
attempts per process. Once exhausted, it returns a small explicit stop error;
repeated stop errors and MCP envelopes are outside the evidence budget. These
are **byte budgets, not exact tokenizer counts or complete request limits**.
You can set `--response-bytes`, `--evidence-bytes`, and `--calls` after `serve`.
Standalone `search`/`read` commands each start a new budget.

Search returns at most eight ranked section hits. Reads return complete lines,
source hashes, and an explicit continuation pointer. Changed or missing sources
fail rather than serving cached text. A stale index can still omit newly added
files or newly relevant text: rebuild after workspace changes. Search is lexical,
not semantic; fuzzy historical questions may require better queries or richer
metadata. Large single lines fail explicitly instead of disappearing silently.

The Copilot adapter is initially read-only. It does not implement editing,
automatic session rotation, semantic checkpoints, a hard model-output token
limit, or silent-completion enforcement. It requests a fresh session and records
Copilot's final usage in `last-usage.json` when the host writes that file.
System prompts, installed catalogs, dynamically added servers, history, reasoning,
and user changes to session tools remain host-controlled. No measured savings
are claimed. Desktop-app enforcement is not implemented by the CLI launcher.

## Storage and source scope

Default state: `%LOCALAPPDATA%\AgentBudget\workspaces\<workspace-hash>` on Windows,
or `$XDG_DATA_HOME/agent-budget/workspaces/<workspace-hash>` on Unix
(fallback `~/.local/share`). `--state` selects another external directory.
The SQLite index contains source text; it is local and unencrypted. Use a private
user directory. Do not index confidential files that should not reach your agent.
Reading through MCP sends selected evidence to the host/model you configured.

Only the explicit root is scanned. Git workspaces honor untracked-file ignore
rules; tracked files are still considered. Non-Git workspaces use the documented
directory/extension filters, not `.gitignore` parsing. Symlinks and junctions are
excluded. Common credential filenames, dependency/build directories, binary or
non-UTF-8 files, files over 1 MB, and unsupported extensions are skipped. Filename
filters are **not secret detection**. Add `index --exclude "relative/pattern/*"`
for sensitive paths (use forward slashes inside these glob patterns).
Indexing reports skipped-file reasons, and fails above 10,000 eligible files.
Reindex atomically replaces the old indexed contents.

Retention is manual: after stopping the server, remove the specific workspace's
state directory to remove its index and usage record. This does not delete host
session histories. No background service or global configuration is installed.

## Verification and next gate

```powershell
python -m unittest discover -v
```

An initial live Copilot CLI smoke run successfully searched this project's index,
read README line ranges, and returned a sourced one-sentence answer using only
the broker tools. The host reported 120 output tokens across four model requests
and 17,748 cumulative input tokens, including 12,714 cache-read tokens. This is
one small integration example, not a savings benchmark or a guaranteed minimum.
It illustrates why a short answer is not the same as a small total request cost.

Before calling this a token-saving execution plugin, benchmark representative
tasks against the same agent/model without it. Measure total input, cache usage,
output/reasoning usage where reported, task correctness, retries, and wall time.
Include indexing overhead and insufficient-evidence outcomes. Tool descriptions
and protocol metadata also consume input tokens.

The next implementation gate is narrowly scoped editing and deterministic
validation tools. Do not substitute
unrestricted shell access: it bypasses the retrieval boundary.
