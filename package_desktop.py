"""Build a self-contained desktop plugin pinned to one explicitly indexed root."""

import argparse
import json
from pathlib import Path
import shutil
import sys

import agent_budget


def package(root, destination, python=sys.executable):
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Workspace root must be a directory")
    state = agent_budget.state_directory(root)
    if state.resolve().is_relative_to(root):
        raise ValueError("Index state must be outside workspace")
    broker = agent_budget.Broker(root, state)
    broker.close()
    if destination.exists():
        raise ValueError("Destination already exists; choose a new package directory")
    source = Path(__file__).parent.resolve()
    if destination.resolve().is_relative_to(source):
        raise ValueError("Package destination must be outside the source package")
    destination.mkdir(parents=True, mode=0o700)
    for name in ("plugin.json", "agent_budget.py", "README.md"):
        shutil.copyfile(source / name, destination / name)
    shutil.copytree(source / "skills", destination / "skills")
    config = {
        "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
        "mcpServers": {"agentbudget": {
            "type": "stdio",
            "command": str(Path(python).resolve()),
            "args": ["-u", "${PLUGIN_ROOT}/agent_budget.py", "--root", str(root),
                     "--state", str(state), "serve"],
            "env": {"PYTHONUTF8": "1"},
        }},
    }
    (destination / "mcp.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return {"package": str(destination.resolve()), "root": str(root), "state": str(state)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(package(args.root, args.destination)))
        return 0
    except (OSError, ValueError, agent_budget.sqlite3.Error) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
