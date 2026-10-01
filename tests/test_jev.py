import unittest
from orchestrator.adapters.jev import JevAdapter, JevDecisionAdapter, QUESTIONS, Assessment, decision_questions
from orchestrator.adapters.base import AdapterError
from orchestrator.core.costs import cost, estimate
from orchestrator.core.models import Usage


class Transport:
    def __init__(self, result):
        self.result = result
        self.calls = []
    def post(self, url, key, payload):
        self.calls.append((url, payload))
        return self.result


def fixture():
    answers = {}
    for name, question in QUESTIONS.items():
        if question["type"] == "noul":
            answers[name] = {"type": "noul", "noul": 0.99 if name == "context_sufficient" else 0.01}
        else:
            choice = "LOW" if name == "risk" else "MEDIUM"
            answers[name] = {"type": "choice", "choice": choice, "confidence": 0.95,
                             "probabilities": {k: float(k == choice) for k in question["criteria"]}}
    return {"model": "jev-1.13.0", "answers": answers, "usage": {"input_tokens": 120, "output_tokens": 40}}


class JevTests(unittest.TestCase):
    def test_official_request_and_usage(self):
        transport = Transport(fixture())
        adapter = JevAdapter(transport=transport, key="test")
        reply = adapter.invoke(adapter.prepare({"description": "Draft text"}))
        self.assertEqual(reply.usage.input_tokens, 120)
        self.assertEqual(reply.model, "jev-1.13.0")
        self.assertEqual(reply.output["confidence"], 0.95)
        self.assertEqual(transport.calls[0][0], "https://api.typesafe.ai/v1/systemone")
        self.assertEqual(len(transport.calls[0][1]["questions"]), 10)

    def test_malformed_answer_preserves_usage(self):
        raw = fixture()
        raw["answers"]["risk"]["choice"] = "Invented"
        with self.assertRaises(AdapterError) as caught:
            JevAdapter(transport=Transport(raw), key="test").invoke({})
        self.assertEqual(caught.exception.usage.input_tokens, 120)

    def test_probabilities_reject_bad_values(self):
        for value in (True, -0.1, 1.1, float("nan"), "0.9"):
            raw = fixture()
            raw["answers"]["security"]["noul"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                Assessment.parse(raw["answers"])

    def test_missing_usage_is_unknown(self):
        raw = fixture()
        del raw["usage"]
        with self.assertRaises(AdapterError) as caught:
            JevAdapter(transport=Transport(raw), key="test").invoke({})
        self.assertTrue(caught.exception.uncertain)

    def test_costs_cached_and_rounding(self):
        spec = {"input_per_million": "2", "output_per_million": "10", "cached_per_million": "0.2"}
        self.assertEqual(cost(Usage(100, 5, 50), spec), 160)
        self.assertGreater(estimate({"input": "abc"}, spec, 100), 1000)
        with self.assertRaises(ValueError):
            cost(Usage(), {})

    def test_generic_choice_score_and_noul_decisions(self):
        questions = {
            "best": {"type": "choice", "instructions": "Which candidate best fits?", "criteria": {"a": "Candidate A", "b": "Candidate B"}},
            "safe": {"type": "noul", "instructions": "The selected candidate is safe."},
            "quality": {"type": "score", "instructions": "Rate quality.", "criteria": ["Poor", "Acceptable", "Excellent"]},
        }
        raw = {"model": "jev-1.13.0", "answers": {
            "best": {"type": "choice", "choice": "b", "confidence": 0.8, "probabilities": {"a": 0.1, "b": 0.9}},
            "safe": {"type": "noul", "noul": 0.95},
            "quality": {"type": "score", "score": 1.7, "confidence": 0.6, "probabilities": {"0": 0.0, "1": 0.3, "2": 0.7}},
        }, "usage": {"input_tokens": 90, "output_tokens": 30}}
        transport = Transport(raw)
        adapter = JevDecisionAdapter(transport=transport, key="test")
        reply = adapter.invoke(adapter.prepare({"candidates": ["A", "B"]}, questions))
        self.assertEqual(reply.output["answers"]["best"]["choice"], "b")
        self.assertEqual(reply.output["answers"]["quality"]["levels"][2], "Excellent")
        self.assertEqual(transport.calls[0][1]["questions"], questions)

    def test_generic_questions_fail_closed(self):
        invalid = {"bad id": {"type": "choice", "instructions": "Pick", "criteria": {"a": "A", "b": "B"}}}
        with self.assertRaises(ValueError):
            decision_questions(invalid)
        with self.assertRaises(ValueError):
            decision_questions({"score": {"type": "score", "instructions": "Rate", "criteria": ["Only one"]}})
