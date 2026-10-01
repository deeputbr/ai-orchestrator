import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from orchestrator.adapters.base import AdapterError
from orchestrator.adapters.codex import CodexAdapter, child_environment, parse_events, bounded_process, REQUIRED
from orchestrator.adapters.openai import MockOpenAI, OpenAIAdapter
from orchestrator.core.config import Settings
from orchestrator.core.engine import Engine
from orchestrator.core.models import Task, Project, Usage
from orchestrator.core.state import Store
from orchestrator.core.control import reconcile, resume


def events(result="ok", usage=True):
    value = [{"type": "thread.started", "thread_id": "test-thread"},
             {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps({"result": result, "notes": []})}}]
    if usage:
        value.append({"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 60, "output_tokens": 10}})
    return '\n'.join(json.dumps(x) for x in value)


class FakeRunner:
    def __init__(self, auth="Logged in using ChatGPT", failure=None):
        self.auth = auth
        self.failure = failure
        self.calls = []
    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs))
        if args[-2:] == ["exec", "--help"]:
            text = ' '.join(REQUIRED)
        elif args[-2:] == ["login", "status"]:
            text = self.auth
        else:
            if self.failure:
                raise self.failure
            text = events()
        return subprocess.CompletedProcess(args, 0, text, "")


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.binary = Path(self.tmp.name) / "codex"
        self.binary.write_text("fixture")
    def tearDown(self):
        self.tmp.cleanup()

    def test_credentials_and_provider_overrides_are_not_inherited(self):
        env = child_environment({"HOME": "/home/example", "CODEX_HOME": "/chosen", "PATH": "/bin", "OPENAI_API_KEY": "secret", "CODEX_API_KEY": "secret", "OPENAI_BASE_URL": "https://elsewhere", "CODEX_THREAD_ID": "parent", "JEV_API_KEY": "secret"})
        self.assertEqual(set(env), {"HOME", "CODEX_HOME", "PATH"})
        self.assertEqual(env["CODEX_HOME"], "/chosen")

    def test_subscription_auth_and_restricted_invocation(self):
        runner = FakeRunner()
        adapter = CodexAdapter("gpt-6-sol", str(self.binary), runner=runner)
        reply = adapter.invoke(adapter.prepare({"description": "Hello"}))
        args, kwargs = runner.calls[-1]
        self.assertIn('forced_login_method="chatgpt"', args)
        self.assertIn('model_provider="openai"', args)
        self.assertIn('features.plugins=false', args)
        self.assertIn('features.shell_tool=false', args)
        self.assertIn("--ignore-user-config", args)
        self.assertIn("read-only", args)
        self.assertEqual(args[-1], "-")
        self.assertNotIn("Hello", ' '.join(args))
        self.assertIn("Hello", kwargs["input"])
        self.assertFalse(Path(kwargs["cwd"]).exists())
        self.assertEqual(reply.usage.cached_tokens, 60)
        self.assertEqual(reply.metadata["billing"], "chatgpt_subscription")
        self.assertIsNone(reply.metadata["usd_cost"])

    def test_api_auth_never_launches_generation(self):
        for auth in ("Logged in using an API key", "Not logged in", "WARNING ChatGPT blah"):
            runner = FakeRunner(auth=auth)
            adapter = CodexAdapter("gpt-6-sol", str(self.binary), runner=runner)
            with self.assertRaisesRegex(AdapterError, "chatgpt_login_required_no_api_fallback"):
                adapter.invoke(adapter.prepare({}))
            self.assertEqual(len(runner.calls), 2)

    def test_explicit_old_binary_does_not_fall_back(self):
        def old(args, **kwargs):
            return subprocess.CompletedProcess(args, 0, "ancient help", "")
        adapter = CodexAdapter("gpt-6-sol", str(self.binary), runner=old)
        with self.assertRaisesRegex(AdapterError, "current_codex_runtime_required"):
            adapter.preflight()

    def test_timeout_is_unknown(self):
        adapter = CodexAdapter("gpt-6-sol", str(self.binary), runner=FakeRunner(failure=TimeoutError()))
        with self.assertRaises(AdapterError) as caught:
            adapter.invoke(adapter.prepare({}))
        self.assertTrue(caught.exception.uncertain)

    def test_incomplete_malformed_and_tool_events_fail_closed(self):
        for text in (events(usage=False), 'not json', events() + '\n' + json.dumps({"type": "item.completed", "item": {"type": "command_execution"}})):
            with self.assertRaises(AdapterError):
                parse_events(text, "gpt-6-sol", 10)

    def test_known_runtime_warning_is_recorded_without_rejecting_result(self):
        warning = {
            "type": "item.completed",
            "item": {
                "type": "error",
                "message": "Under-development features enabled: fixture warning",
            },
        }
        reply = parse_events(json.dumps(warning) + "\n" + events(), "gpt-6-sol", 10)
        self.assertEqual(reply.output["result"], "ok")
        self.assertEqual(len(reply.metadata["runtime_warnings"]), 1)

    def test_usage_limit_has_no_fallback_or_raw_error(self):
        text = json.dumps({"type": "turn.failed", "error": {"message": "Usage limit reached secret-account"}})
        with self.assertRaisesRegex(AdapterError, "subscription_limit_reached") as caught:
            parse_events(text, "gpt-6-sol", 10, 1)
        self.assertNotIn("secret-account", str(caught.exception))

    def test_model_unavailable_never_substitutes_model(self):
        text = json.dumps({"type": "error", "message": "The model is not supported for this account"})
        with self.assertRaisesRegex(AdapterError, "subscription_model_unavailable"):
            parse_events(text, "gpt-6-astra", 1, 1)

    def test_bounded_process_cleans_up_timeout_and_output(self):
        with self.assertRaises(TimeoutError):
            bounded_process([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.1)
        with self.assertRaises(OverflowError):
            bounded_process([sys.executable, "-c", "print('x'*10000)"], timeout=5, max_bytes=100)
        reply = bounded_process([sys.executable, "-c", "print('ok')"])
        self.assertEqual(reply.stdout.strip(), "ok")


class SubscriptionFixture(MockOpenAI):
    external = True
    billing = "chatgpt_subscription"
    def preflight(self):
        return {"authenticated_with": "ChatGPT"}
    def invoke(self, payload):
        r = super().invoke(payload)
        r.usage = Usage(100, 10, 50)
        return r


class SubscriptionEngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "state.sqlite3")
        self.store.create_project(Project("p", "Subscription fixture", budget_microusd=0, allow_external=True))
        self.settings = Settings(assessment_provider="declared")
    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()
    def add(self, id="a", **kwargs):
        self.store.add_tasks("p", [Task(id, "p", "Generate text", metadata={"routing_reviewed": True, "context_sufficient": True}, **kwargs)])
    def engine(self, sol=None, astra=None):
        return Engine(self.store, self.settings, live=True, sol=sol or SubscriptionFixture("sol"), astra=astra or SubscriptionFixture("astra"))

    def test_default_backend_and_explicit_api(self):
        self.assertIsInstance(Engine(self.store, live=True).generators["SOL"], CodexAdapter)
        self.assertIsInstance(Engine(self.store, Settings(openai_backend="api"), live=True).generators["SOL"], OpenAIAdapter)
        with self.assertRaises(ValueError):
            Settings(openai_backend="auto")

    def test_no_api_prices_required_and_separate_accounting(self):
        self.add()
        state = self.engine().run("p")
        self.assertEqual(state["statuses"], {"COMPLETED": 1})
        self.assertEqual(state["spend"]["total_microusd"], 0)
        usage = state["subscription_usage"]
        self.assertEqual(usage["calls"], 1)
        self.assertEqual(usage["known_tokens"]["input_tokens"], 100)
        self.assertIsNone(usage["usd_cost"])
        self.assertIsNone(usage["remaining_account_allowance"])
        attempt = self.store.attempts("p")[0]
        self.assertIsNone(attempt["cost"])
        self.assertEqual(attempt["tier"], "SOL")
        self.assertEqual(self.store.task("p", "a").metadata["assessment_source"], "operator_declared_not_model_confidence")

    def test_declared_mode_requires_reviewed_inputs(self):
        self.store.add_tasks("p", [Task("a", "p", "Not reviewed")])
        self.engine().run("p")
        self.assertEqual(self.store.attempts("p"), [])
        self.assertIn("routing_reviewed", self.store.task("p", "a").escalation_reason)

    def test_per_project_call_cap_survives_new_engine(self):
        self.settings.max_subscription_calls = 1
        self.add()
        self.add("b")
        self.engine().run("p")
        self.assertEqual(len(self.store.attempts("p")), 1)
        self.assertEqual(self.store.task("p", "b").metadata["gate"]["kind"], "subscription")
        resume(self.store, "p", "b")
        self.engine().run("p")
        self.assertEqual(len(self.store.attempts("p")), 1)

    def test_astra_cap_pauses_instead_of_downgrading(self):
        self.settings.max_subscription_astra_calls = 0
        self.add(risk="HIGH")
        sol, astra = SubscriptionFixture("sol"), SubscriptionFixture("astra")
        self.engine(sol, astra).run("p")
        self.assertEqual(len(sol.calls) + len(astra.calls), 0)
        self.assertEqual(self.store.task("p", "a").selected_tier, "ASTRA")

    def test_unknown_usage_reconciles_without_usd_charge(self):
        self.add()
        sol = SubscriptionFixture("sol", [AdapterError("subscription_limit_reached", uncertain=True)])
        self.engine(sol).run("p")
        usage = self.store.summary("p")["subscription_usage"]
        self.assertEqual(usage["unresolved_calls"], 1)
        a = self.store.attempts("p")[0]
        with self.assertRaises(ValueError):
            reconcile(self.store, "p", a["id"], 10, "Note")
        reconcile(self.store, "p", a["id"], 0, "No result returned; reviewed account usage")
        self.assertIsNone(self.store.attempts("p")[0]["cost"])
        self.assertEqual(self.store.summary("p")["subscription_usage"]["calls_without_usage"], 1)

    def test_not_dispatched_attempt_is_not_counted_as_subscription_call(self):
        self.add()
        sol = SubscriptionFixture("sol", [AdapterError("codex_configuration_error", not_dispatched=True)])
        self.engine(sol).run("p")
        usage = self.store.summary("p")["subscription_usage"]
        self.assertEqual(usage["calls"], 0)
        self.assertEqual(usage["attempt_records"], 1)
        self.assertEqual(usage["not_dispatched_calls"], 1)

    def test_auth_failure_before_any_jev_charge(self):
        self.settings.assessment_provider = "jev"
        self.add()
        sol = SubscriptionFixture("sol")
        sol.preflight = lambda: (_ for _ in ()).throw(AdapterError("chatgpt_login_required_no_api_fallback"))
        self.engine(sol).run("p")
        self.assertEqual(self.store.attempts("p"), [])
