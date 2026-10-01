import tempfile
import unittest
from pathlib import Path
from orchestrator.core.config import Settings
from orchestrator.core.local import execute
from orchestrator.core.models import Project, Task
from orchestrator.core.state import Store


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "state.sqlite3"
        self.store = Store(self.path)
        self.store.create_project(Project("demo", "Test core"))

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_persistence_and_summary(self):
        self.store.add_tasks("demo", [Task("one", "demo", "Do work")])
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.task("demo", "one").description, "Do work")
        self.assertEqual(self.store.summary("demo")["budget_remaining_microusd"], 1_000_000)
        self.assertEqual(len(self.store.events("demo")), 2)

    def test_cycle_is_atomic(self):
        with self.assertRaises(ValueError):
            self.store.add_tasks("demo", [Task("a", "demo", "A", dependencies=["b"]), Task("b", "demo", "B", dependencies=["a"])])
        self.assertEqual(self.store.tasks("demo"), [])

    def test_missing_dependency(self):
        with self.assertRaises(ValueError):
            self.store.add_tasks("demo", [Task("a", "demo", "A", dependencies=["missing"])])

    def test_invalid_records(self):
        for kwargs in ({"risk": "BAD"}, {"sensitive": "false"}, {"dependencies": "abc"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                Task("a", "demo", "A", **kwargs)
        with self.assertRaises(ValueError):
            Project("../escape", "No")

    def test_config(self):
        self.assertEqual(Settings.load("config/settings.toml").models["sol"]["id"], "gpt-6-sol")
        with self.assertRaises(ValueError):
            Settings(confidence_threshold=float("nan"))

    def test_local_operations(self):
        self.assertEqual(execute("word_count", {"text": "a b"}), {"result": 2})
        self.assertEqual(execute("json_normalize", {"text": "{\"a\": 1}"}), {"result": {"a": 1}})
        with self.assertRaises(ValueError):
            execute("shell", {})

    def test_lock(self):
        other = Store(self.path)
        try:
            with self.store.lock(), self.assertRaises(ValueError):
                with other.lock():
                    pass
        finally:
            other.close()
