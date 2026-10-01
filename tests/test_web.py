import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from orchestrator.core.config import Settings
from orchestrator.web.server import Application, Server, dollars


class WebTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = Application(Path(self.tmp.name) / "state.sqlite3", Settings(), self.tmp.name)
        self.server = Server(("127.0.0.1", 0), self.app)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def request(self, path, data=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        defaults = {"Content-Type": "application/json", "X-Orchestrator-Token": self.app.token}
        defaults.update(headers or {})
        connection.request("GET" if data is None else "POST", path, body=json.dumps(data) if data is not None else None, headers=defaults)
        response = connection.getresponse()
        status, content_type, body = response.status, response.getheader("Content-Type"), response.read()
        connection.close()
        return status, json.loads(body) if content_type.startswith("application/json") else body.decode()

    def action(self, name, data, status=200):
        code, result = self.request("/api/actions/" + name, data)
        self.assertEqual(code, status, result)
        return result

    def create(self):
        return self.action("create", {"id": "demo", "goal": "Test UI", "budget_usd": "0.50"})

    def await_run(self):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            result = self.request("/api/state")[1]
            if result["job"]["status"] != "running":
                self.assertIsNone(result["job"]["error"])
                return result
            time.sleep(0.01)
        self.fail("Run did not finish")

    def test_dashboard_and_local_lifecycle(self):
        self.assertIn("Your ideas, in motion", self.request("/")[1])
        self.create()
        self.action("import", {"project_id": "demo", "template": "local"})
        self.action("run", {"project_id": "demo"}, 202)
        self.await_run()
        result = self.request("/api/projects/demo")[1]
        self.assertEqual(result["summary"]["statuses"], {"COMPLETED": 2})
        self.assertEqual(result["tasks"][0]["output"], {"result": 4})
        self.assertEqual(result["summary"]["spend"]["total_microusd"], 0)

    def test_approval_does_not_bypass_other_gates(self):
        self.create()
        self.action("import", {"project_id": "demo", "template": "content"})
        self.action("run", {"project_id": "demo"}, 202)
        self.await_run()
        result = self.request("/api/projects/demo")[1]
        self.assertEqual(result["summary"]["statuses"], {"COMPLETED": 2, "WAITING_FOR_APPROVAL": 1})
        self.action("approve", {"project_id": "demo", "task_id": "outline"}, 400)
        self.action("approve", {"project_id": "demo", "task_id": "editorial-review"})
        self.action("run", {"project_id": "demo"}, 202)
        self.await_run()
        self.assertEqual(self.request("/api/projects/demo")[1]["summary"]["statuses"], {"COMPLETED": 3})

    def test_cross_origin_and_missing_token_cannot_mutate(self):
        data = {"id": "demo", "goal": "Test"}
        for headers in ({"X-Orchestrator-Token": ""}, {"Origin": "https://evil.invalid"}, {"Host": "evil.invalid"}, {"Sec-Fetch-Site": "cross-site"}):
            self.assertEqual(self.request("/api/actions/create", data, headers)[0], 403)
        self.assertEqual(self.request("/api/state", headers={"Host": "evil.invalid"})[0], 403)
        self.assertEqual(self.request("/api/state")[1]["projects"], [])

    def test_import_runtime_and_cycle_rejected_atomically(self):
        self.create()
        for tasks in ([{"id": "a", "description": "Bad", "approved": True}], [{"id": "a", "description": "A", "dependencies": ["b"]}, {"id": "b", "description": "B", "dependencies": ["a"]}]):
            self.action("import", {"project_id": "demo", "workflow": {"tasks": tasks}}, 400)
        self.assertEqual(self.request("/api/projects/demo")[1]["tasks"], [])

    def test_session_key_never_returned_or_persisted(self):
        key = "fake-token-that-must-not-be-saved"
        self.action("connections", {"jev_key": key})
        self.create()
        self.assertNotIn(key, json.dumps(self.request("/api/state")[1]))
        self.assertNotIn(key, json.dumps(self.request("/api/projects/demo")[1]))
        for file in Path(self.tmp.name).iterdir():
            self.assertNotIn(key.encode(), file.read_bytes())
        self.assertTrue(self.app.connection_info()["jev_key_present"])
        self.action("connections", {"jev_key": ""})
        self.assertIsNone(self.app.jev_key)

    def test_busy_runner_blocks_second_run_and_mutations(self):
        self.create()
        entered, release = threading.Event(), threading.Event()
        def hold(*args):
            entered.set()
            release.wait(3)
        with patch("orchestrator.web.server.Engine.run", hold):
            self.action("run", {"project_id": "demo"}, 202)
            self.assertTrue(entered.wait(2))
            self.action("run", {"project_id": "demo"}, 400)
            self.action("connections", {"jev_key": "x"}, 400)
            self.action("shutdown", {}, 400)
            self.assertEqual(self.request("/api/projects/demo")[0], 200)
            release.set()
            self.await_run()

    def test_live_key_injected_without_changing_environment(self):
        self.create()
        self.action("connections", {"jev_key": "session-only-token"})
        engines = []
        class FakeEngine:
            def __init__(self, *args, **kwargs):
                engines.append(kwargs)
            def run(self, project_id):
                pass
        with patch("orchestrator.web.server.Engine", FakeEngine):
            self.action("run", {"project_id": "demo", "live": True}, 202)
            self.await_run()
        self.assertEqual(engines[0]["jev"].key, "session-only-token")
        self.assertEqual(engines[0]["jev_decision"].key, "session-only-token")

    def test_project_settings_and_invalid_money(self):
        self.create()
        self.action("configure", {"project_id": "demo", "budget_usd": "2.25", "allow_external": True})
        project = self.request("/api/projects/demo")[1]["summary"]["project"]
        self.assertEqual(project["budget_microusd"], 2250000)
        self.assertTrue(project["allow_external"])
        for value in ("NaN", "Infinity", "-1", "0.0000001", "not-money"):
            with self.assertRaises(ValueError):
                dollars(value)

    def test_unknown_outcome_requires_explicit_reconciliation(self):
        self.create()
        self.action("import", {"project_id": "demo", "workflow": {"tasks": [{"id": "a", "description": "Test"}]}})
        from orchestrator.core.state import Store
        store = Store(self.app.db)
        with store.lock(), store.db:
            store.db.execute("INSERT INTO attempts(project_id,task_id,tier,mode,status,reserved,cost,data,created_at) VALUES ('demo','a','SOL','live','UNKNOWN',0,NULL,?, 'now')", (json.dumps({"billing": "chatgpt_subscription"}),))
        store.close()
        self.action("reconcile", {"project_id": "demo", "attempt_id": 1, "cost_usd": "1", "note": "verified"}, 400)
        self.action("reconcile", {"project_id": "demo", "attempt_id": 1, "cost_usd": "0", "note": "Verified subscription outcome"})
        self.assertEqual(self.request("/api/projects/demo")[1]["attempts"][0]["status"], "RECONCILED")

    def test_live_privacy_gate_still_enforced(self):
        self.create()
        self.action("import", {"project_id": "demo", "workflow": {"tasks": [{"id": "a", "description": "Draft text"}]}})
        self.action("run", {"project_id": "demo", "live": True}, 202)
        self.await_run()
        task = self.request("/api/projects/demo")[1]["tasks"][0]
        self.assertEqual(task["metadata"]["gate"]["kind"], "privacy")
        self.assertEqual(self.request("/api/projects/demo")[1]["attempts"], [])

    def test_edit_only_before_execution_and_preserve_dependency_graph(self):
        self.create()
        self.action("import", {"project_id": "demo", "template": "local"})
        self.action("edit-task", {"project_id": "demo", "task_id": "count", "changes": {"inputs": {"text": "Custom brief"}}})
        self.action("edit-task", {"project_id": "demo", "task_id": "count", "changes": {"dependencies": ["normalize"]}}, 400)
        self.action("run", {"project_id": "demo"}, 202)
        self.await_run()
        self.assertEqual(self.request("/api/projects/demo")[1]["tasks"][0]["output"], {"result": 2})
        self.action("edit-task", {"project_id": "demo", "task_id": "count", "changes": {"description": "changed"}}, 400)


if __name__ == "__main__":
    unittest.main()
