"""Local, dependency-free evidence broker and Copilot launcher."""

import argparse
from contextlib import closing
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import time


EXTENSIONS = {
    ".md", ".txt", ".rst", ".py", ".js", ".jsx", ".ts", ".tsx", ".json",
    ".toml", ".yaml", ".yml", ".go", ".rs", ".cs", ".java", ".html",
    ".css", ".sql", ".sh", ".ps1", ".bicep", ".tf",
}
EXCLUDED = {
    ".git", ".hg", ".svn", ".venv", "venv", "node_modules", "__pycache__",
    ".copilot", ".ssh", ".aws", ".azure", "dist", "build",
}
SENSITIVE = ("*.env*", "*secret*", "*credential*", "*private*key*", "*.pem", "*.key")
MAX_FILE_BYTES = 1_000_000
MAX_FILES = 10_000
DEFAULT_CALLS = int(os.environ.get("AGENT_BUDGET_CALLS", "40"))
# shortcut: MCP has no task boundary; reset after this idle gap or a new initialize, upgrade if MCP adds one.
IDLE_SECONDS = float(os.environ.get("AGENT_BUDGET_IDLE_SECONDS", "600"))


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def state_directory(root, base=None):
    if base is None:
        if os.name == "nt":
            base = Path(os.environ["LOCALAPPDATA"]) / "AgentBudget"
        else:
            base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "agent-budget"
    key = digest(os.fsencode(str(root.resolve())))[:24]
    return Path(base) / "workspaces" / key


def eligible(relative, patterns):
    return (
        not any(part.lower() in EXCLUDED for part in relative.parts)
        and not any(fnmatch.fnmatch(relative.name.lower(), p) for p in SENSITIVE)
        and not any(fnmatch.fnmatch(relative.as_posix(), p) for p in patterns)
        and relative.suffix.lower() in EXTENSIONS
    )


def source_bytes(root, relative):
    path = root / relative
    # Refuse symlinks/junctions at every level, including paths still inside root.
    for candidate in (path, *path.parents):
        if candidate == root:
            break
        if candidate.is_symlink() or candidate.is_junction():
            raise ValueError("Linked paths are excluded")
    if not path.resolve().is_relative_to(root):
        raise ValueError("Path escapes workspace")
    with path.open("rb") as stream:
        data = stream.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("File exceeds size limit")
    if b"\x00" in data:
        raise ValueError("Binary file excluded")
    data.decode("utf-8")
    return data


def candidates(root):
    git = shutil.which("git")
    if git:
        probe = subprocess.run(
            [git, "-C", str(root), "rev-parse", "--show-toplevel"],
            capture_output=True, check=False,
        )
        if probe.returncode == 0:
            result = subprocess.run(
                [git, "-C", str(root), "ls-files", "-z", "--cached", "--others",
                 "--exclude-standard", "--", "."],
                capture_output=True, check=True,
            )
            yield from sorted({Path(os.fsdecode(p)) for p in result.stdout.split(b"\0") if p})
            return
    for directory, folders, files in os.walk(root, followlinks=False):
        folders[:] = sorted(
            name for name in folders
            if name.lower() not in EXCLUDED
            and not (Path(directory) / name).is_symlink()
            and not (Path(directory) / name).is_junction()
        )
        for name in sorted(files):
            yield (Path(directory) / name).relative_to(root)


def index_workspace(root, state, patterns):
    if state.resolve().is_relative_to(root):
        raise ValueError("State must be outside the indexed workspace")
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    skipped = {}
    with closing(sqlite3.connect(state / "index.sqlite3")) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, sha TEXT, text TEXT)")
        db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS sections USING fts5(path UNINDEXED, line UNINDEXED, text)")
        db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        # A failed rebuild rolls back to the previous complete index.
        db.execute("DELETE FROM files")
        db.execute("DELETE FROM sections")
        count = 0
        for relative in candidates(root):
            if not eligible(relative, patterns):
                continue
            try:
                data = source_bytes(root, relative)
            except (OSError, ValueError, UnicodeError) as error:
                reason = type(error).__name__ + ": " + str(error)
                skipped[reason] = skipped.get(reason, 0) + 1
                continue
            count += 1
            if count > MAX_FILES:
                raise ValueError("Workspace exceeds 10,000 eligible files; select a smaller root")
            text = data.decode("utf-8")
            db.execute("INSERT INTO files VALUES (?,?,?)", (str(relative), digest(data), text))
            lines = text.splitlines()
            for start in range(0, len(lines), 40):
                db.execute("INSERT INTO sections VALUES (?,?,?)",
                           (str(relative), start + 1, "\n".join(lines[start:start + 40])))
        db.execute("INSERT OR REPLACE INTO settings VALUES ('root',?)", (str(root),))
    return {"indexed": count, "skipped": skipped, "state": str(state),
            "warning": "Local index contains source text. Filename exclusions are not secret detection."}


class Broker:
    def __init__(self, root, state, response_bytes=6000, evidence_bytes=24000, calls=DEFAULT_CALLS, log=None):
        if not 512 <= response_bytes <= 64000 or not 512 <= evidence_bytes <= 1_000_000:
            raise ValueError("Byte budgets out of range")
        if not 1 <= calls <= 100:
            raise ValueError("Call budget must be 1..100")
        self.root = root
        self.db = sqlite3.connect(f"{(state / 'index.sqlite3').as_uri()}?mode=ro", uri=True)
        saved = self.db.execute("SELECT value FROM settings WHERE key='root'").fetchone()
        if saved is None or saved[0] != str(root):
            self.db.close()
            raise ValueError("Index root mismatch")
        self.response_bytes = response_bytes
        self.budget = (evidence_bytes, calls)
        self.reset()
        self.log = log
        if log:
            log.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(log.parent, 0o700)

    def reset(self):
        self.remaining, self.calls = self.budget
        self.last_call = time.monotonic()

    def record(self, name, result):
        if not self.log:
            return
        paths = sorted({m["path"] for m in result.get("matches", [])} | (
            {result["path"]} if "path" in result else set()))
        full = 0
        for path in paths:
            try:
                full += (self.root / path).stat().st_size
            except OSError:
                pass
        line = encode({"ts": time.time(), "tool": name.removeprefix("agentbudget_"), "paths": paths,
                       "bytes_returned": len(encode(result).encode("utf-8")), "full_bytes": full,
                       "budget_hit": "budget" in result.get("error", "").lower()})
        fd = os.open(self.log, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as stream:
            stream.write(line + "\n")

    def close(self):
        self.db.close()

    def fits(self, result):
        return len(encode(result).encode("utf-8")) <= min(self.response_bytes, self.remaining)

    def fresh(self, path, sha):
        if digest(source_bytes(self.root, Path(path))) != sha:
            raise ValueError("Stale source; reindex workspace")

    def search(self, query):
        if not isinstance(query, str) or len(query) > 500:
            raise ValueError("Query must be a string of at most 500 characters")
        terms = re.findall(r"\w+", query, re.UNICODE)[:12]
        if not terms:
            raise ValueError("Query needs words")
        match = " OR ".join('"' + term + '"' for term in terms)
        rows = self.db.execute(
            "SELECT sections.path, sections.line, snippet(sections,2,'','', ' ... ',16), files.sha "
            "FROM sections JOIN files ON files.path=sections.path "
            "WHERE sections MATCH ? ORDER BY rank LIMIT 9", (match,),
        ).fetchall()
        result = {"matches": [], "truncated": False}
        for path, line, excerpt, sha in rows[:8]:
            self.fresh(path, sha)
            item = {"path": path, "line": line, "excerpt": excerpt, "sha256": sha}
            result["matches"].append(item)
            if not self.fits(result):
                result["matches"].pop()
                result["truncated"] = True
                break
        result["truncated"] = result["truncated"] or len(rows) > 8
        if rows and not result["matches"]:
            raise ValueError("No complete search hit fits remaining budget")
        return result

    def read(self, path, start=1, lines=40):
        if not isinstance(path, str) or len(path) > 4096:
            raise ValueError("Path must be an indexed path string")
        if type(start) is not int or type(lines) is not int or start < 1 or not 1 <= lines <= 200:
            raise ValueError("Use start >= 1 and lines between 1 and 200")
        row = self.db.execute("SELECT sha,text FROM files WHERE path=?", (path,)).fetchone()
        if row is None:
            raise ValueError("Path not indexed; use exact path returned by search")
        sha, text = row
        self.fresh(path, sha)
        all_lines = text.splitlines()
        if start > len(all_lines):
            raise ValueError("Start beyond end of file")
        result = {"path": path, "sha256": sha, "start": start, "lines": [],
                  "truncated": False, "next_line": None}
        end = min(start - 1 + lines, len(all_lines))
        for offset in range(start - 1, end):
            result["lines"].append(all_lines[offset])
            result["next_line"] = offset + 2 if offset + 1 < len(all_lines) else None
            result["truncated"] = result["next_line"] is not None
            if not self.fits(result):
                result["lines"].pop()
                next_line = start + len(result["lines"])
                result["next_line"] = next_line
                result["truncated"] = True
                break
        if not result["lines"]:
            raise ValueError("No complete line fits budget; source needs smaller sections")
        return result

    def call(self, name, arguments):
        result = self._call(name, arguments)
        self.record(str(name), result)
        return result

    def _call(self, name, arguments):
        if time.monotonic() - self.last_call > IDLE_SECONDS:
            self.reset()
        self.last_call = time.monotonic()
        if self.calls <= 0 or self.remaining < 256:
            return {"error": "Budget exhausted; stop. Work may be incomplete."}
        self.calls -= 1
        try:
            if name == "agentbudget_search":
                result = self.search(**arguments)
            elif name == "agentbudget_read":
                result = self.read(**arguments)
            else:
                raise ValueError("Unknown tool")
            if not self.fits(result):
                raise ValueError("Result exceeds remaining budget")
        except (ValueError, TypeError, OSError, UnicodeError) as error:
            # Do not echo arbitrary arguments or unbounded filesystem errors.
            result = {"error": str(error)[:160]}
            while not self.fits(result):
                result["error"] = result["error"][:-1]
        size = len(encode(result).encode("utf-8"))
        self.remaining -= size
        return result


TOOLS = [
    {"name": "agentbudget_search", "description": "Find bounded local evidence. Search before reading.",
     "inputSchema": {"type": "object", "properties": {"query": {"type": "string", "maxLength": 500}},
                     "required": ["query"], "additionalProperties": False}},
    {"name": "agentbudget_read", "description": "Read exact indexed path and line range; rejects stale files.",
     "inputSchema": {"type": "object", "properties": {
         "path": {"type": "string"}, "start": {"type": "integer", "minimum": 1},
         "lines": {"type": "integer", "minimum": 1, "maximum": 200}},
         "required": ["path"], "additionalProperties": False}},
]


def serve(broker, incoming=sys.stdin, outgoing=sys.stdout):
    for line in incoming:
        try:
            message = json.loads(line)
            if not isinstance(message, dict):
                raise ValueError("Request must be an object")
        except (json.JSONDecodeError, ValueError):
            outgoing.write(encode({"jsonrpc": "2.0", "id": None,
                                   "error": {"code": -32700, "message": "Invalid JSON request"}}) + "\n")
            outgoing.flush()
            continue
        if "id" not in message:
            continue
        method = message.get("method")
        response = {"jsonrpc": "2.0", "id": message["id"]}
        if method == "initialize":
            broker.reset()  # a new MCP session is the closest task boundary stdio gives us
            response["result"] = {
                "protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                "serverInfo": {"name": "agent-budget", "version": "0.1.0"},
            }
        elif method == "ping":
            response["result"] = {}
        elif method == "tools/list":
            response["result"] = {"tools": TOOLS}
        elif method == "tools/call":
            params = message.get("params", {})
            if not isinstance(params, dict) or not isinstance(params.get("arguments", {}), dict):
                response["error"] = {"code": -32602, "message": "Invalid tool parameters"}
            else:
                result = broker.call(params.get("name"), params.get("arguments", {}))
                response["result"] = {"content": [{"type": "text", "text": encode(result)}],
                                      "isError": "error" in result}
        else:
            response["error"] = {"code": -32601, "message": "Unknown method"}
        outgoing.write(encode(response) + "\n")
        outgoing.flush()


def stats(log):
    returned = full = calls = 0
    if log.is_file():
        for line in log.read_text(encoding="utf-8").splitlines():
            entry = json.loads(line)
            calls += 1
            returned += entry["bytes_returned"]
            full += entry["full_bytes"]
    ratio = returned / full if full else 0.0
    return (f"calls={calls} returned={returned} full={full} "
            f"ratio={ratio:.4f} saved={(1 - ratio) * 100 if full else 0.0:.1f}%")


def launch_command(root, state, prompt):
    cli = shutil.which("copilot")
    if cli is None:
        raise ValueError("Copilot CLI not found")
    discovery = subprocess.run(
        [cli, "-C", str(root), "mcp", "list", "--json"],
        capture_output=True, text=True, check=True,
    )
    servers = json.loads(discovery.stdout).get("mcpServers")
    if not isinstance(servers, dict):
        raise ValueError("Unsupported Copilot MCP discovery format")
    disabled = []
    for name in sorted(servers):
        if name != "agentbudget":
            disabled.extend(["--disable-mcp-server", name])
    config = {"mcpServers": {"agentbudget": {
        "type": "stdio", "command": sys.executable,
        "args": [str(Path(__file__).resolve()), "--root", str(root),
                 "--state", str(state), "serve"],
        "tools": ["*"],
    }}}
    config_path = state / "copilot-mcp.json"
    config_path.write_text(encode(config), encoding="utf-8")
    skill = Path(__file__).parent / "skills" / "agent-budget" / "SKILL.md"
    instructions = skill.read_text(encoding="utf-8").split("---", 2)[2].strip()
    return [
        cli, "-C", str(root), "--disable-builtin-mcps",
        *disabled,
        "--additional-mcp-config", "@" + str(config_path),
        "--available-tools=agentbudget-agentbudget_search,agentbudget-agentbudget_read",
        "--usage-output-file", str(state / "last-usage.json"),
        "-i", instructions + "\nRead-only task (do not edit files):\n" + prompt,
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="Explicit workspace to index/read")
    parser.add_argument("--state", type=Path, help="External state directory; default is per-user app data")
    commands = parser.add_subparsers(dest="command", required=True)
    index = commands.add_parser("index")
    index.add_argument("--exclude", action="append", default=[], help="Additional relative-path glob")
    for name in ("search", "read", "serve"):
        command = commands.add_parser(name)
        command.add_argument("--response-bytes", type=int, default=6000)
        command.add_argument("--evidence-bytes", type=int, default=24000)
        command.add_argument("--calls", type=int, default=DEFAULT_CALLS)
        if name == "search":
            command.add_argument("query")
        elif name == "read":
            command.add_argument("path")
            command.add_argument("--start", type=int, default=1)
            command.add_argument("--lines", type=int, default=40)
    commands.add_parser("stats", help="Sum the call log and print the savings ratio")
    launch = commands.add_parser("copilot")
    launch.add_argument("prompt")
    launch.add_argument("--run", action="store_true", help="Launch interactive CLI; otherwise print configuration")
    args = parser.parse_args()
    try:
        root = args.root.resolve(strict=True)
        if not root.is_dir():
            raise ValueError("Workspace root must be a directory")
        state = args.state.resolve() if args.state else state_directory(root)
        if args.command == "index":
            print(encode(index_workspace(root, state, args.exclude)))
            return 0
        if args.command == "stats":
            print(stats(state / "calls.jsonl"))
            return 0
        if args.command == "copilot":
            if not (state / "index.sqlite3").is_file():
                raise ValueError("Index workspace before launching Copilot")
            command = launch_command(root, state, args.prompt)
            print(encode({"command": command, "limits": {
                "broker_response_bytes": 6000, "broker_evidence_bytes": 24000,
                "broker_calls": DEFAULT_CALLS, "exact_tokens": False, "model_output_cap": False,
                "hidden_context_control": False, "silent_success": "advisory",
                "mode": "read-only", "scope": "per MCP session or idle gap",
                "host_integration": "Copilot CLI only; revalidate after CLI updates",
            }}), flush=True)
            return subprocess.call(command) if args.run else 0
        broker = Broker(root, state, args.response_bytes, args.evidence_bytes, args.calls, state / "calls.jsonl")
        try:
            if args.command == "serve":
                serve(broker)
                return 0
            arguments = {"query": args.query} if args.command == "search" else {
                "path": args.path, "start": args.start, "lines": args.lines}
            result = broker.call("agentbudget_" + args.command, arguments)
            print(encode(result))
            return 1 if "error" in result else 0
        finally:
            broker.close()
    except (ValueError, OSError, sqlite3.Error, subprocess.SubprocessError) as error:
        print(encode({"error": str(error)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
