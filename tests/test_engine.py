import json
from pathlib import Path
import tempfile
import unittest
from orchestrator.adapters.base import AdapterError, Reply
from orchestrator.adapters.jev import MockJev, MockJevDecision
from orchestrator.adapters.openai import MockOpenAI
from orchestrator.core.config import Settings
from orchestrator.core.control import approve, resume, reconcile
from orchestrator.core.context import collect
from orchestrator.core.engine import Engine
from orchestrator.core.models import Project, Task, Usage
from orchestrator.core.state import Store


class BillableJev(MockJev):
    external = True
    def invoke(self, payload):
        reply = super().invoke(payload)
        reply.usage = Usage(100, 10)
        return reply


class BillableSol(MockOpenAI):
    external = True
    def invoke(self, payload):
        reply = super().invoke(payload)
        reply.usage = Usage(200, 20)
        return reply


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = Store(self.root / "state.sqlite3")
        self.db.create_project(Project("p", "Test workflow", allow_external=True))
        self.cfg = Settings(openai_backend="api")
        for spec in self.cfg.models.values():
            spec.update(input_per_million="1", output_per_million="2", cached_per_million="0.1")

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def add(self, **kwargs):
        task = Task("a", "p", "Produce an answer", **kwargs)
        self.db.add_tasks("p", [task])
        return task

    def task(self):
        return self.db.task("p", "a")

    def test_deterministic_never_calls_models(self):
        self.add(task_type="word_count", inputs={"text": "one two"}, validators=[{"type": "equals", "value": 2}])
        jev, sol = MockJev(), MockOpenAI("sol")
        e = Engine(self.db, jev=jev, sol=sol)
        e.run("p")
        e.run("p")
        self.assertEqual(self.task().output, {"result": 2})
        self.assertEqual(self.task().attempts, 1)
        self.assertEqual(jev.calls + len(sol.calls), 0)

    def test_direct_jev_decision_uses_dependencies_without_generation(self):
        self.db.add_tasks("p", [
            Task("source", "p", "Count candidates", task_type="word_count", inputs={"text": "A B C"}),
            Task("choose", "p", "Choose the best candidate", task_type="jev_decision", dependencies=["source"],
                 inputs={"state": {"goal": "Prefer the first valid candidate"}, "questions": {
                     "best": {"type": "choice", "instructions": "Which candidate best fits?", "criteria": {"a": "Candidate A", "b": "Candidate B"}}
                 }})
        ])
        routing, decisions, sol = MockJev(), MockJevDecision(), MockOpenAI("sol")
        Engine(self.db, jev=routing, jev_decision=decisions, sol=sol).run("p")
        chosen = self.db.task("p", "choose")
        self.assertEqual(chosen.status, "COMPLETED")
        self.assertEqual(chosen.output["answers"]["best"]["choice"], "a")
        self.assertEqual(decisions.calls[0]["state"]["dependencies"]["source"], {"result": 3})
        self.assertEqual(routing.calls + len(sol.calls), 0)

    def test_jev_questions_can_come_from_a_validated_dependency(self):
        question_pack = {"questions": {"relevant": {"type": "noul", "instructions": "The supplied item is relevant."}}}
        self.db.add_tasks("p", [
            Task("question-plan", "p", "Prepare Jev questions", task_type="json_normalize",
                 inputs={"text": json.dumps(question_pack)}),
            Task("judge", "p", "Judge the item", task_type="jev_decision", dependencies=["question-plan"],
                 inputs={"state": {"item": "example"}, "questions_from_dependency": "question-plan"})
        ])
        decisions = MockJevDecision()
        Engine(self.db, jev_decision=decisions).run("p")
        self.assertEqual(self.db.task("p", "judge").status, "COMPLETED")
        self.assertEqual(decisions.calls[0]["questions"], question_pack["questions"])

    def test_repair_twice_then_astra_uses_evidence(self):
        self.add(validators=[{"type": "contains", "value": "correct"}])
        bad = {"result": "bad", "notes": []}
        sol = MockOpenAI("sol", [bad, bad])
        astra = MockOpenAI("astra", [{"result": "correct", "notes": []}])
        e = Engine(self.db, sol=sol, astra=astra)
        e.run("p")
        self.assertEqual(self.task().status, "COMPLETED")
        self.assertEqual(self.task().selected_tier, "ASTRA")
        self.assertIn("repair", json.loads(sol.calls[1]["input"]))
        self.assertNotEqual(sol.calls[0], sol.calls[1])
        self.assertEqual([a["tier"] for a in self.db.attempts("p")], ["JEV", "SOL", "SOL", "ASTRA"])

    def test_exhaustion_goes_human(self):
        self.add(validators=[{"type": "contains", "value": "absent"}])
        Engine(self.db).run("p")
        self.assertEqual(self.task().selected_tier, "HUMAN")
        self.assertEqual(self.task().status, "WAITING_FOR_APPROVAL")
        self.assertEqual(len(self.db.attempts("p")), 4)

    def test_approval_persists_and_resumes(self):
        self.add(requires_approval=True)
        Engine(self.db).run("p")
        self.assertEqual(self.task().status, "WAITING_FOR_APPROVAL")
        self.assertEqual(self.db.attempts("p"), [])
        approve(self.db, "p", "a")
        self.db.close()
        self.db = Store(self.root / "state.sqlite3")
        Engine(self.db).run("p")
        self.assertEqual(self.task().status, "COMPLETED")

    def test_budget_stops_before_external_call(self):
        self.add()
        jev = BillableJev()
        self.cfg.max_single_call_microusd = 1
        Engine(self.db, self.cfg, live=True, jev=jev).run("p")
        self.assertEqual(jev.calls, 0)
        self.assertEqual(self.task().metadata["gate"]["kind"], "budget")
        with self.assertRaises(ValueError):
            approve(self.db, "p", "a")

    def test_sensitive_stays_local(self):
        self.add(sensitive=True)
        jev = BillableJev()
        Engine(self.db, self.cfg, live=True, jev=jev).run("p")
        self.assertEqual(jev.calls, 0)
        self.assertEqual(self.task().metadata["gate"]["kind"], "privacy")

    def test_paid_failure_usage_is_counted(self):
        self.add(validators=[{"type": "contains", "value": "correct"}])
        sol = BillableSol("sol", [{"result": "bad", "notes": []}, {"result": "correct", "notes": []}])
        Engine(self.db, self.cfg, live=True, jev=BillableJev(), sol=sol).run("p")
        self.assertEqual(self.db.spend("p")["total_microusd"], 600)
        self.assertEqual(self.task().model_cost_microusd, 600)
        self.assertEqual(self.task().input_tokens, 500)
        self.assertEqual(self.task().status, "COMPLETED")

    def test_unknown_outcome_requires_reconciliation(self):
        self.add()
        sol = BillableSol("sol", [AdapterError("timeout", uncertain=True)])
        e = Engine(self.db, self.cfg, live=True, jev=BillableJev(), sol=sol)
        e.run("p")
        self.assertEqual(self.task().metadata["gate"]["kind"], "reconcile")
        self.assertGreater(self.db.spend("p")["reserved_microusd"], 0)
        with self.assertRaises(ValueError):
            resume(self.db, "p", "a")
        a = self.db.attempts("p")[-1]
        reconcile(self.db, "p", a["id"], 50, "Verified billing")
        self.assertEqual(self.db.spend("p")["reserved_microusd"], 0)
        resume(self.db, "p", "a")
        e.run("p")
        self.assertEqual(len(sol.calls), 1)
        self.assertIn("Identical", self.task().escalation_reason)

    def test_interrupted_running_is_not_replayed(self):
        t = self.add()
        t.status = "RUNNING"
        self.db.save(t)
        jev = MockJev()
        Engine(self.db, jev=jev).run("p")
        self.assertEqual(jev.calls, 0)
        self.assertEqual(self.task().metadata["gate"]["kind"], "reconcile")

    def test_dependency_waits_for_approval(self):
        self.add(requires_approval=True)
        self.db.add_tasks("p", [Task("b", "p", "Follow-up", dependencies=["a"])])
        Engine(self.db).run("p")
        self.assertEqual(self.db.task("p", "b").status, "PENDING")
        approve(self.db, "p", "a")
        Engine(self.db).run("p")
        self.assertEqual(self.db.task("p", "b").status, "COMPLETED")

    def test_context_rejects_escape_secret_symlink_and_size(self):
        (self.root / "safe.txt").write_text("safe")
        (self.root / "link").symlink_to("/etc/hosts")
        for name in ("../secret", ".env", "x.pem", "link"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                collect(self.root, [name], 100)
        with self.assertRaises(ValueError):
            collect(self.root, ["safe.txt"], 2)
        self.assertEqual(collect(self.root, ["safe.txt"], 4)[0]["text"], "safe")

    def test_mock_cannot_silently_become_live(self):
        self.add(requires_approval=True)
        Engine(self.db).run("p")
        approve(self.db, "p", "a")
        jev = BillableJev()
        Engine(self.db, self.cfg, live=True, jev=jev).run("p")
        self.assertEqual(jev.calls, 0)
        self.assertIn("mix mock and live", self.task().escalation_reason)

    def test_cost_approval_applies_to_one_request(self):
        self.add()
        self.cfg.approval_above_microusd = 1
        jev, sol = BillableJev(), BillableSol("sol")
        e = Engine(self.db, self.cfg, live=True, jev=jev, sol=sol)
        e.run("p")
        self.assertEqual(jev.calls, 0)
        approve(self.db, "p", "a")
        e.run("p")
        self.assertEqual(jev.calls, 1)
        self.assertEqual(len(sol.calls), 0)
        approve(self.db, "p", "a")
        e.run("p")
        self.assertEqual(len(sol.calls), 1)
        self.assertEqual(self.task().status, "COMPLETED")

    def test_bad_validator_blocks_before_spending(self):
        self.add(validators=[{"type": "unknown"}])
        jev = BillableJev()
        Engine(self.db, self.cfg, live=True, jev=jev).run("p")
        self.assertEqual(jev.calls, 0)
        self.assertEqual(self.task().metadata["gate"]["kind"], "configuration")

    def test_sensitive_dependency_cannot_leak(self):
        self.add(task_type="word_count", inputs={"text": "private data"}, sensitive=True)
        self.db.add_tasks("p", [Task("b", "p", "Explain private result", dependencies=["a"])])
        jev = BillableJev()
        Engine(self.db, self.cfg, live=True, jev=jev).run("p")
        self.assertEqual(jev.calls, 0)
        self.assertEqual(self.db.task("p", "b").metadata["gate"]["kind"], "privacy")

    def test_astra_limit_blocks_without_downgrading(self):
        self.add(risk="HIGH")
        self.cfg.max_astra_microusd = 1
        astra = BillableSol("astra")
        sol = BillableSol("sol")
        Engine(self.db, self.cfg, live=True, jev=BillableJev(), sol=sol, astra=astra).run("p")
        self.assertEqual(len(sol.calls) + len(astra.calls), 0)
        self.assertEqual(self.task().metadata["gate"]["kind"], "budget")

    def test_project_budget_includes_existing_spend(self):
        self.add()
        p = self.db.project("p")
        p.budget_microusd = 1
        self.db.update_project(p)
        jev = BillableJev()
        Engine(self.db, self.cfg, live=True, jev=jev).run("p")
        self.assertEqual(jev.calls, 0)

    def test_missing_price_blocks_before_network(self):
        self.add()
        jev = BillableJev()
        Engine(self.db, Settings(openai_backend="api"), live=True, jev=jev).run("p")
        self.assertEqual(jev.calls, 0)
        self.assertIn("pricing", self.task().escalation_reason)

    def test_higher_cost_invalidates_approval(self):
        self.add()
        self.cfg.approval_above_microusd = 1
        jev = BillableJev()
        e = Engine(self.db, self.cfg, live=True, jev=jev)
        e.run("p")
        approve(self.db, "p", "a")
        self.cfg.models["jev"]["input_per_million"] = "2"
        e.run("p")
        self.assertEqual(jev.calls, 0)
        self.assertEqual(self.task().metadata["gate"]["kind"], "approval")

    def test_unsupported_capability_does_not_call_jev(self):
        self.add(capabilities=["shell"])
        jev = MockJev()
        Engine(self.db, jev=jev).run("p")
        self.assertEqual(jev.calls, 0)
        self.assertEqual(self.task().status, "WAITING_FOR_APPROVAL")
