import unittest
from orchestrator.adapters.openai import OpenAIAdapter
from orchestrator.adapters.base import AdapterError
from orchestrator.adapters.jev import Assessment
from orchestrator.core.config import Settings
from orchestrator.core.models import Task, Tier
from orchestrator.core.routing import route
from tests.test_jev import Transport, fixture


def response():
    return {"id": "resp_test", "model": "gpt-6-sol", "status": "completed",
            "usage": {"input_tokens": 100, "output_tokens": 20, "input_tokens_details": {"cached_tokens": 60}},
            "output": [{"type": "message", "content": [{"type": "output_text", "text": '{"result":"hello","notes":[]}'}]}]}


class OpenAITests(unittest.TestCase):
    def test_structured_request_and_cached_usage(self):
        t = Transport(response())
        a = OpenAIAdapter("gpt-6-sol", transport=t, key="test")
        reply = a.invoke(a.prepare({"description": "Hello"}))
        self.assertEqual(reply.output["result"], "hello")
        self.assertEqual(reply.usage.cached_tokens, 60)
        body = t.calls[0][1]
        self.assertFalse(body["store"])
        self.assertTrue(body["text"]["format"]["strict"])

    def test_refusal_and_incomplete_keep_usage(self):
        for kind in ("refusal", "incomplete"):
            raw = response()
            if kind == "refusal":
                raw["output"][0]["content"] = [{"type": "refusal", "refusal": "No"}]
            else:
                raw["status"] = "incomplete"
            with self.subTest(kind=kind), self.assertRaises(AdapterError) as caught:
                OpenAIAdapter("gpt-6-sol", transport=Transport(raw), key="test").invoke({})
            self.assertEqual(caught.exception.usage.output_tokens, 20)

    def test_invalid_json_and_extra_fields(self):
        for text in ("bad json", '{"result":"ok","notes":[],"extra":1}'):
            raw = response()
            raw["output"][0]["content"][0]["text"] = text
            with self.assertRaises(AdapterError):
                OpenAIAdapter("gpt-6-sol", transport=Transport(raw), key="test").invoke({})


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.a = Assessment.parse(fixture()["answers"])
        self.task = Task("a", "p", "Draft text")
        self.cfg = Settings()

    def test_default_sol(self):
        self.assertEqual(route(self.task, self.a, self.cfg).tier, Tier.SOL)

    def test_deterministic_first(self):
        self.task.task_type = "sha256"
        self.assertEqual(route(self.task, None, self.cfg).tier, Tier.CODE)

    def test_risk_architecture_security_and_failure(self):
        for field in ("architecture", "security", "irreversible"):
            a = Assessment(self.a.signals | {field: 0.99}, "LOW", "MEDIUM", 0.99)
            self.assertEqual(route(self.task, a, self.cfg).tier, Tier.ASTRA)
        self.task.risk = "HIGH"
        self.assertEqual(route(self.task, self.a, self.cfg).tier, Tier.ASTRA)
        self.task.risk = "LOW"
        self.assertEqual(route(self.task, self.a, self.cfg, sol_failures=2).tier, Tier.ASTRA)
        self.assertEqual(route(self.task, self.a, self.cfg, astra_failures=1).tier, Tier.HUMAN)

    def test_ambiguous_missing_context_and_visual(self):
        for field, value in (("security", 0.5), ("context_sufficient", 0.2), ("visual", 0.9), ("deterministic", 0.99)):
            a = Assessment(self.a.signals | {field: value}, "LOW", "MEDIUM", 0.99)
            self.assertEqual(route(self.task, a, self.cfg).tier, Tier.HUMAN)

    def test_config_can_disable_failure_escalation(self):
        self.cfg.astra_triggers = []
        self.assertEqual(route(self.task, self.a, self.cfg, sol_failures=2).tier, Tier.HUMAN)
