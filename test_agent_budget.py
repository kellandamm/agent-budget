import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import agent_budget as ab
import package_desktop


class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "workspace"
        self.root.mkdir()
        self.state = Path(self.temp.name).resolve() / "state"
        (self.root / "guide.md").write_text(
            "# Guide\n" + "".join(f"retrieval evidence {n}\n" for n in range(180)), encoding="utf-8")
        (self.root / "credentials.json").write_text('{"value":"excluded"}', encoding="utf-8")
        (self.root / "omit.md").write_text("excluded", encoding="utf-8")
        ab.index_workspace(self.root, self.state, ["omit.md"])

    def broker(self, **kwargs):
        broker = ab.Broker(self.root, self.state, **kwargs)
        self.addCleanup(broker.close)
        return broker

    def test_search_and_exclusions(self):
        result = self.broker().call("agentbudget_search", {"query": "retrieval"})
        self.assertEqual(result["matches"][0]["path"], "guide.md")
        self.assertEqual(self.broker().call("agentbudget_search", {"query": "excluded"})["matches"], [])
        self.assertIn("error", self.broker().call("agentbudget_read", {"path": "..\\outside.md"}))

    def test_bounded_read_and_continuation(self):
        broker = self.broker(response_bytes=512)
        result = broker.call("agentbudget_read", {"path": "guide.md", "lines": 200})
        self.assertLessEqual(len(ab.encode(result).encode("utf-8")), 512)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["next_line"], 1 + len(result["lines"]))
        following = broker.call("agentbudget_read", {"path": "guide.md", "start": result["next_line"]})
        self.assertEqual(following["lines"][0], f"retrieval evidence {result['next_line'] - 2}")

    def test_cumulative_budget_and_call_limit(self):
        broker = self.broker(response_bytes=512, evidence_bytes=512)
        broker.call("agentbudget_read", {"path": "guide.md", "lines": 200})
        self.assertIn("Budget exhausted", broker.call("agentbudget_search", {"query": "retrieval"})["error"])
        broker = self.broker(calls=1)
        broker.call("agentbudget_search", {"query": "retrieval"})
        self.assertIn("Budget exhausted", broker.call("agentbudget_search", {"query": "retrieval"})["error"])

    def test_stale_evidence_rejected(self):
        broker = self.broker()
        (self.root / "guide.md").write_text("changed", encoding="utf-8")
        for name, args in (("agentbudget_read", {"path": "guide.md"}),
                           ("agentbudget_search", {"query": "retrieval"})):
            self.assertIn("Stale", broker.call(name, args)["error"])

    def test_long_line_not_silently_truncated(self):
        (self.root / "large.md").write_text("x" * 8000, encoding="utf-8")
        ab.index_workspace(self.root, self.state, ["omit.md"])
        result = self.broker().call("agentbudget_read", {"path": "large.md"})
        self.assertIn("No complete line", result["error"])

    def test_mcp_roundtrip(self):
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
                "name": "agentbudget_search", "arguments": {"query": "retrieval"}}},
        ]
        output = io.StringIO()
        ab.serve(self.broker(), io.StringIO("\n".join(map(json.dumps, requests))), output)
        responses = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(len(responses), 3)
        self.assertEqual(len(responses[1]["result"]["tools"]), 2)
        self.assertFalse(responses[2]["result"]["isError"])

    def test_state_inside_workspace_rejected(self):
        with self.assertRaisesRegex(ValueError, "outside"):
            ab.index_workspace(self.root, self.root / "state", [])

    def test_unicode_and_invalid_ranges(self):
        (self.root / "unicode.md").write_text("evidence \u2603\n" * 100, encoding="utf-8")
        ab.index_workspace(self.root, self.state, ["omit.md"])
        broker = self.broker(response_bytes=512)
        result = broker.call("agentbudget_read", {"path": "unicode.md", "lines": 200})
        self.assertLessEqual(len(ab.encode(result).encode("utf-8")), 512)
        self.assertIn("error", broker.call("agentbudget_read", {"path": "unicode.md", "start": 0}))

    def test_launcher_filters_tools_without_blanket_permissions(self):
        with patch.object(ab.shutil, "which", return_value="copilot"), patch.object(ab.subprocess, "run") as run:
            run.return_value.stdout = '{"mcpServers":{"other":{},"agentbudget":{}}}'
            command = ab.launch_command(self.root, self.state, "Find evidence")
        self.assertIn("--available-tools=agentbudget-agentbudget_search,agentbudget-agentbudget_read", command)
        self.assertIn("--disable-mcp-server", command)
        self.assertIn("other", command)
        self.assertNotIn("--allow-all-tools", command)
        self.assertNotIn("--no-custom-instructions", command)
        config = json.loads((self.state / "copilot-mcp.json").read_text(encoding="utf-8"))
        self.assertEqual(list(config["mcpServers"]), ["agentbudget"])

    def test_failed_rebuild_preserves_previous_index(self):
        (self.root / "new.md").write_text("new evidence", encoding="utf-8")
        with patch.object(ab, "MAX_FILES", 1), self.assertRaises(ValueError):
            ab.index_workspace(self.root, self.state, ["omit.md"])
        self.assertIn("lines", self.broker().call("agentbudget_read", {"path": "guide.md"}))
        self.assertIn("error", self.broker().call("agentbudget_read", {"path": "new.md"}))

    def test_removed_source_is_not_served_from_cache(self):
        (self.root / "guide.md").unlink()
        self.assertIn("error", self.broker().call("agentbudget_read", {"path": "guide.md"}))

    def test_stdio_process(self):
        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "agentbudget_read", "arguments": {"path": "guide.md", "lines": 2}}}
        result = subprocess.run(
            [sys.executable, str(Path(ab.__file__).resolve()), "--root", str(self.root),
             "--state", str(self.state), "serve"],
            input=json.dumps(request) + "\n", text=True, capture_output=True, check=True,
            encoding="utf-8", timeout=10,
        )
        response = json.loads(result.stdout)
        self.assertFalse(response["result"]["isError"])
        self.assertEqual(json.loads(response["result"]["content"][0]["text"])["lines"][0], "# Guide")

    def test_git_ignored_files_are_not_indexed(self):
        git = ab.shutil.which("git")
        if not git:
            self.skipTest("Git unavailable")
        subprocess.run([git, "init", "--quiet", str(self.root)], check=True)
        (self.root / ".gitignore").write_text("ignored.md\n", encoding="utf-8")
        (self.root / "ignored.md").write_text("invisible evidence", encoding="utf-8")
        ab.index_workspace(self.root, self.state, ["omit.md"])
        self.assertEqual(self.broker().call("agentbudget_search", {"query": "invisible"})["matches"], [])

    def plugin_process(self, root, state):
        env = os.environ.copy()
        env["AGENT_BUDGET_ROOT"] = root
        env["AGENT_BUDGET_STATE"] = state
        env["PYTHONUTF8"] = "1"
        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "agentbudget_read", "arguments": {"path": "guide.md", "lines": 1}}}
        return subprocess.run(
            [sys.executable, str(Path(ab.__file__).parent / "plugin_server.py")],
            input=json.dumps(request) + "\n", text=True, capture_output=True,
            encoding="utf-8", timeout=10, env=env,
        )

    def test_plugin_server_uses_explicit_workspace(self):
        result = self.plugin_process(str(self.root), str(self.state))
        self.assertEqual(result.returncode, 0, result.stderr)
        response = json.loads(result.stdout)
        self.assertFalse(response["result"]["isError"])

    def test_plugin_server_rejects_missing_or_relative_root(self):
        for root in ("", "relative"):
            result = self.plugin_process(root, str(self.state))
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            self.assertIn("AGENT_BUDGET_ROOT", result.stderr)

    def test_plugin_server_rejects_unindexed_workspace(self):
        state = self.state / "missing"
        result = self.plugin_process(str(self.root), str(state))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertFalse(state.exists())

    def test_plugin_server_rejects_relative_state(self):
        result = self.plugin_process(str(self.root), "relative")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("AGENT_BUDGET_STATE", result.stderr)

    def test_desktop_package_is_self_contained_and_pinned(self):
        destination = Path(self.temp.name) / "desktop-plugin"
        with patch.object(ab, "state_directory", return_value=self.state):
            result = package_desktop.package(self.root, destination)
        self.assertEqual(result["root"], str(self.root.resolve()))
        self.assertTrue((destination / "skills" / "agent-budget" / "SKILL.md").is_file())
        config = json.loads((destination / "mcp.json").read_text(encoding="utf-8"))
        server = config["mcpServers"]["agentbudget"]
        self.assertTrue(Path(server["command"]).is_absolute())
        self.assertIn(str(self.root.resolve()), server["args"])
        self.assertIn(str(self.state), server["args"])
        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        command = [server["command"]] + [
            value.replace("${PLUGIN_ROOT}", str(destination)) for value in server["args"]]
        run = subprocess.run(command, input=json.dumps(request) + "\n", capture_output=True,
                             text=True, check=True, timeout=10)
        self.assertEqual(len(json.loads(run.stdout)["result"]["tools"]), 2)
        with patch.object(ab, "state_directory", return_value=self.state), self.assertRaises(ValueError):
            package_desktop.package(self.root, destination)

    def test_log_line_shape_mode_and_no_content(self):
        log = self.state / "logs" / "calls.jsonl"
        broker = self.broker(log=log)
        broker.call("agentbudget_search", {"query": "retrieval secretquery"})
        broker.call("agentbudget_read", {"path": "guide.md", "lines": 3})
        entries = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertEqual([e["tool"] for e in entries], ["search", "read"])
        size = (self.root / "guide.md").stat().st_size
        for entry in entries:
            self.assertEqual(set(entry), {"ts", "tool", "paths", "bytes_returned", "full_bytes", "budget_hit"})
            self.assertEqual((entry["paths"], entry["full_bytes"], entry["budget_hit"]), (["guide.md"], size, False))
        text = log.read_text()
        self.assertNotIn("retrieval", text)
        self.assertNotIn("secretquery", text)
        if os.name != "nt":
            self.assertEqual(log.stat().st_mode & 0o777, 0o600)
            self.assertEqual(log.parent.stat().st_mode & 0o777, 0o700)
        broker = self.broker(calls=1, log=log)
        broker.call("agentbudget_read", {"path": "guide.md"})
        broker.call("agentbudget_read", {"path": "guide.md"})
        self.assertTrue(json.loads(log.read_text().splitlines()[-1])["budget_hit"])

    def test_stats_math(self):
        log = self.state / "calls.jsonl"
        log.write_text('{"bytes_returned":100,"full_bytes":1000}\n{"bytes_returned":150,"full_bytes":4000}\n')
        self.assertEqual(ab.stats(log), "calls=2 returned=250 full=5000 ratio=0.0500 saved=95.0%")
        self.assertIn("calls=0", ab.stats(self.state / "missing.jsonl"))

    def test_call_limit_40_and_env_override(self):
        self.assertEqual(ab.DEFAULT_CALLS, 40)
        self.assertEqual(self.broker().calls, 40)
        code = "import agent_budget; print(agent_budget.DEFAULT_CALLS)"
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,
                             cwd=Path(ab.__file__).parent, env={**os.environ, "AGENT_BUDGET_CALLS": "7"})
        self.assertEqual(out.stdout.strip(), "7")

    def test_idle_and_session_reset(self):
        broker = self.broker(calls=1)
        broker.call("agentbudget_search", {"query": "retrieval"})
        self.assertIn("Budget exhausted", broker.call("agentbudget_search", {"query": "retrieval"})["error"])
        broker.last_call -= ab.IDLE_SECONDS + 1
        self.assertNotIn("error", broker.call("agentbudget_search", {"query": "retrieval"}))
        self.assertIn("error", broker.call("agentbudget_search", {"query": "retrieval"}))
        out = io.StringIO()
        ab.serve(broker, io.StringIO('{"jsonrpc":"2.0","id":1,"method":"initialize"}\n'), out)
        self.assertNotIn("error", broker.call("agentbudget_search", {"query": "retrieval"}))


if __name__ == "__main__":
    unittest.main()
