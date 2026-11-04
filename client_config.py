"""Export scoped MCP configuration without modifying an agent's settings."""

import argparse
import json
from pathlib import Path
import sqlite3
import sys

import agent_budget


CLIENTS = ("claude-desktop", "claude-code", "cursor", "codex", "copilot", "vscode", "generic")


def configuration(client, root, state=None, response_bytes=6000, evidence_bytes=24000, calls=12):
    if client not in CLIENTS:
        raise ValueError("Unsupported client")
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Workspace root must be a directory")
    state = state.resolve() if state else agent_budget.state_directory(root)
    if state.is_relative_to(root):
        raise ValueError("Index state must be outside workspace")
    broker = agent_budget.Broker(root, state, response_bytes, evidence_bytes, calls)
    broker.close()
    server = {
        "command": str(Path(sys.executable).resolve()),
        "args": ["-u", str(Path(agent_budget.__file__).resolve()), "--root", str(root),
                 "--state", str(state), "serve", "--response-bytes", str(response_bytes),
                 "--evidence-bytes", str(evidence_bytes), "--calls", str(calls)],
        "env": {"PYTHONUTF8": "1"},
    }
    if client == "codex":
        # JSON basic strings/arrays are also valid TOML for these generated values.
        return (
            "[mcp_servers.agentbudget]\n"
            f"command = {json.dumps(server['command'], ensure_ascii=False)}\n"
            f"args = {json.dumps(server['args'], ensure_ascii=False)}\n"
            "\n[mcp_servers.agentbudget.env]\n"
            'PYTHONUTF8 = "1"\n'
        )
    if client in ("cursor", "copilot", "vscode", "claude-code"):
        server["type"] = "stdio"
    if client == "copilot":
        server["tools"] = ["*"]
    key = "servers" if client == "vscode" else "mcpServers"
    return json.dumps({key: {"agentbudget": server}}, indent=2, ensure_ascii=False) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", choices=CLIENTS, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--response-bytes", type=int, default=6000)
    parser.add_argument("--evidence-bytes", type=int, default=24000)
    parser.add_argument("--calls", type=int, default=12)
    parser.add_argument("--output", type=Path, help="Create a new file; never overwrite existing settings")
    args = parser.parse_args()
    try:
        content = configuration(args.client, args.root, args.state, args.response_bytes,
                                args.evidence_bytes, args.calls)
        if args.output:
            with args.output.open("x", encoding="utf-8") as stream:
                stream.write(content)
        else:
            print(content, end="")
        return 0
    except (OSError, ValueError, sqlite3.Error) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
