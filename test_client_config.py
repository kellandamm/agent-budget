import json
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest

import agent_budget
import client_config


class ClientConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "workspace with spaces \u2603"
        self.root.mkdir()
        self.state = Path(self.temp.name).resolve() / "state"
        (self.root / "guide.md").write_text("# Evidence\nbudget control\n", encoding="utf-8")
        agent_budget.index_workspace(self.root, self.state, [])

    def test_all_exported_launch_commands_work(self):
        for client in client_config.CLIENTS:
            with self.subTest(client=client):
                content = client_config.configuration(client, self.root, self.state, calls=1)
                config = tomllib.loads(content) if client == "codex" else json.loads(content)
                key = "mcp_servers" if client == "codex" else "servers" if client == "vscode" else "mcpServers"
                server = config[key]["agentbudget"]
                self.assertTrue(Path(server["command"]).is_absolute())
                self.assertTrue(Path(server["args"][1]).is_absolute())
                requests = [
                    {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                     "params": {"protocolVersion": "2024-11-05"}},
                    {"jsonrpc": "2.0", "method": "notifications/initialized"},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                    {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
                        "name": "agentbudget_search", "arguments": {"query": "budget"}}},
                    {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {
                        "name": "agentbudget_read", "arguments": {"path": "guide.md"}}},
                ]
                run = subprocess.run(
                    [server["command"], *server["args"]],
                    input="\n".join(map(json.dumps, requests)) + "\n",
                    text=True, encoding="utf-8", capture_output=True, check=True, timeout=10,
                )
                responses = [json.loads(line) for line in run.stdout.splitlines()]
                self.assertEqual(len(responses), 4)
                self.assertEqual(len(responses[1]["result"]["tools"]), 2)
                self.assertFalse(responses[2]["result"]["isError"])
                self.assertTrue(responses[3]["result"]["isError"])
                evidence = json.loads(responses[2]["result"]["content"][0]["text"])
                self.assertEqual(evidence["matches"][0]["path"], "guide.md")

    def test_unindexed_workspace_and_bad_budgets_fail(self):
        with self.assertRaises(agent_budget.sqlite3.Error):
            client_config.configuration("cursor", self.root, self.state / "missing")
        with self.assertRaises(ValueError):
            client_config.configuration("cursor", self.root, self.state, calls=0)

    def test_existing_settings_are_not_overwritten(self):
        destination = Path(self.temp.name) / "settings.json"
        original = '{"existing":"keep"}'
        destination.write_text(original, encoding="utf-8")
        run = subprocess.run(
            [sys.executable, str(Path(client_config.__file__).resolve()),
             "--client", "cursor", "--root", str(self.root), "--state", str(self.state),
             "--output", str(destination)],
            text=True, encoding="utf-8", capture_output=True, timeout=10,
        )
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("error", json.loads(run.stderr))
        self.assertEqual(destination.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
