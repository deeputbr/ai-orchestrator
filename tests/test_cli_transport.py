import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError, URLError
from orchestrator.adapters.http import JsonTransport, NoRedirect
from orchestrator.adapters.base import AdapterError


class CLITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "db.sqlite3")

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, *args, code=0):
        result = subprocess.run([sys.executable, "-m", "orchestrator", "--db", self.db, *args], capture_output=True, text=True)
        self.assertEqual(result.returncode, code, result.stderr)
        return json.loads(result.stdout if code == 0 else result.stderr)

    def test_documented_local_lifecycle(self):
        self.call("create", "demo", "--goal", "Test")
        self.call("add", "demo", "examples/local.json")
        result = self.call("run", "demo")
        self.assertEqual(result["statuses"], {"COMPLETED": 2})
        tasks = self.call("tasks", "demo")
        self.assertEqual(tasks[0]["output"], {"result": 4})
        self.assertEqual(tasks[1]["output"], {"result": {"ok": True}})
        self.assertEqual(self.call("attempts", "demo"), [])
        exported = Path(self.tmp.name) / "project.json"
        self.call("export", "demo", str(exported))
        self.assertEqual(json.loads(exported.read_text())["completed"], ["count", "normalize"])
        self.call("export", "demo", str(exported), code=2)

    def test_content_approval_lifecycle(self):
        self.call("create", "writing", "--goal", "Draft")
        self.call("add", "writing", "workflows/content_draft.json")
        state = self.call("run", "writing")
        self.assertEqual(state["statuses"], {"COMPLETED": 2, "WAITING_FOR_APPROVAL": 1})
        self.call("approve", "writing", "editorial-review")
        state = self.call("run", "writing")
        self.assertEqual(state["statuses"], {"COMPLETED": 3})
        self.assertEqual(state["spend"]["total_microusd"], 0)

    def test_coding_proposal_sample(self):
        self.call("create", "code", "--goal", "Proposal")
        self.call("add", "code", "workflows/coding_proposal.json")
        state = self.call("run", "code")
        self.assertEqual(state["statuses"], {"COMPLETED": 2, "WAITING_FOR_APPROVAL": 1})
        self.call("approve", "code", "review-proposal")
        self.assertEqual(self.call("run", "code")["statuses"], {"COMPLETED": 3})

    def test_import_rejects_runtime_state_and_unknown_project(self):
        self.call("status", "unknown", code=2)
        self.call("create", "p", "--goal", "Test")
        path = Path(self.tmp.name) / "bad.json"
        path.write_text(json.dumps({"tasks": [{"id": "a", "description": "Task", "approved": True}]}))
        self.call("add", "p", str(path), code=2)
        self.assertEqual(self.call("tasks", "p"), [])


class TransportTests(unittest.TestCase):
    def test_missing_key_makes_no_request(self):
        with patch("orchestrator.adapters.http.build_opener") as opener:
            with self.assertRaises(AdapterError):
                JsonTransport().post("https://api.openai.com/v1/responses", "", {})
            opener.assert_not_called()

    def test_http_errors_are_sanitized_and_not_retried(self):
        for status in (401, 429, 503, 408):
            opener = MagicMock()
            opener.open.side_effect = HTTPError("https://example.invalid", status, "secret-body", {}, None)
            with patch("orchestrator.adapters.http.build_opener", return_value=opener):
                with self.assertRaises(AdapterError) as caught:
                    JsonTransport().post("https://api.openai.com/v1/responses", "secret-key", {})
                self.assertNotIn("secret", str(caught.exception))
                self.assertEqual(opener.open.call_count, 1)
                self.assertEqual(caught.exception.uncertain, status in (503, 408))

    def test_timeout_preserves_unknown_outcome(self):
        opener = MagicMock()
        opener.open.side_effect = URLError("secret-url")
        with patch("orchestrator.adapters.http.build_opener", return_value=opener):
            with self.assertRaises(AdapterError) as caught:
                JsonTransport().post("https://api.openai.com/v1/responses", "test", {})
            self.assertTrue(caught.exception.uncertain)
            self.assertNotIn("secret", str(caught.exception))

    def test_redirects_not_followed(self):
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.invalid"))
