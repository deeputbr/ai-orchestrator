"""Bounded synchronous dependency engine. Call run() to acquire the runner lock."""
from dataclasses import asdict
import hashlib
import json
import time
from orchestrator.adapters.base import AdapterError
from orchestrator.adapters.jev import Assessment, MockJev, JevAdapter, JevDecisionAdapter, MockJevDecision, QUESTIONS
from orchestrator.adapters.codex import CodexAdapter
from orchestrator.adapters.openai import MockOpenAI, OpenAIAdapter
from orchestrator.adapters.http import JsonTransport
from orchestrator.core.config import Settings
from orchestrator.core.context import collect
from orchestrator.core.costs import cost, estimate
from orchestrator.core.local import execute
from orchestrator.core.models import Status, Tier, now
from orchestrator.core.routing import route
from orchestrator.core.validation import validate, check_validators


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


class Engine:
    def __init__(self, store, settings=None, *, live=False, context_root=".", jev=None, jev_decision=None, sol=None, astra=None):
        self.store = store
        self.settings = settings or Settings()
        self.live = live
        self.mode = "live" if live else "mock"
        self.context_root = context_root
        cfg = self.settings
        transport = JsonTransport(cfg.timeout_seconds)
        self.jev = jev or (JevAdapter(cfg.models["jev"]["id"], transport) if live else MockJev())
        self.jev_decision = jev_decision or (JevDecisionAdapter(cfg.models["jev"]["id"], transport) if live else MockJevDecision())
        def generator(tier):
            if not live:
                return MockOpenAI(tier)
            if cfg.openai_backend == "chatgpt":
                return CodexAdapter(cfg.models[tier]["id"], cfg.codex_binary, cfg.codex_timeout_seconds)
            return OpenAIAdapter(cfg.models[tier]["id"], transport, max_output_tokens=cfg.max_output_tokens)
        self.generators = {
            Tier.SOL: sol or generator("sol"),
            Tier.ASTRA: astra or generator("astra"),
        }
        if not live and any(x.external for x in (self.jev, self.jev_decision, *self.generators.values())):
            raise ValueError("External adapters cannot run in mock mode")

    def pause(self, task, reason, gate="human", details=None):
        task.status = Status.WAITING_FOR_APPROVAL
        task.escalation_reason = reason
        recommendations = {
            "approval": "Inspect task and pending request, then approve or revise the task.",
            "budget": "Reduce scope or update the relevant budget and prices, then resume.",
            "privacy": "Keep this work local, or explicitly revise project data-sharing settings.",
            "reconcile": "Inspect the provider billing/result and record a verified charge; do not replay blindly.",
            "configuration": "Fix configuration or task inputs, then resume.",
            "subscription": "Inspect ChatGPT usage/model access or raise the local call cap; no API fallback is allowed.",
            "provider": "Inspect credentials or provider status; revise the task before another paid attempt.",
            "human": "Review evidence and clarify or revise the task before resuming.",
        }
        task.metadata["gate"] = {"kind": gate, "reason": reason,
                                 "recommended_action": recommendations.get(gate, "Inspect task"),
                                 "consequence": "Dependent tasks remain paused; no new request will run until resumed.",
                                 **(details or {})}
        self.store.save(task)
        with self.store.db:
            self.store.event(task.project_id, task.id, "paused", task.metadata["gate"])

    def run(self, project_id):
        with self.store.lock():
            return self.run_locked(project_id)

    def run_locked(self, project_id):
        project = self.store.project(project_id)
        # Crash recovery never assumes a paid request was not sent.
        for task in self.store.tasks(project_id):
            if task.status == Status.RUNNING:
                self.pause(task, "Interrupted run: inspect attempts and reconcile any unknown charge", "reconcile")
        progress = True
        while progress:
            progress = False
            tasks = {t.id: t for t in self.store.tasks(project_id)}
            for task in tasks.values():
                if task.status != Status.PENDING:
                    continue
                if any(tasks[d].status != Status.COMPLETED for d in task.dependencies):
                    continue
                self.execute_task(project, task, tasks)
                progress = True
        return self.store.summary(project_id)

    def execute_task(self, project, task, tasks):
        try:
            check_validators(task.validators)
        except ValueError as exc:
            self.pause(task, str(exc), "configuration")
            return
        previous_mode = task.metadata.get("execution_mode")
        if previous_mode and previous_mode != self.mode:
            self.pause(task, "Cannot mix mock and live attempts; create a fresh task", "human")
            return
        if self.live and any(tasks[d].metadata.get("execution_mode") == "mock" and tasks[d].selected_tier != "CODE" for d in task.dependencies):
            self.pause(task, "Live task depends on simulated output; create a fresh live workflow", "human")
            return
        task.metadata["execution_mode"] = self.mode
        decision = route(task, None, self.settings)
        if decision.tier == Tier.CODE:
            if task.requires_approval and not task.approved:
                self.pause(task, "Approval required for this local task", "approval", {"expected_cost_microusd": 0})
                return
            task.selected_tier, task.selected_model = Tier.CODE, task.task_type
            task.status = Status.RUNNING
            self.store.save(task)
            task.attempts += 1
            try:
                task.output = execute(task.task_type, task.inputs)
                task.validation_results = validate(task.output, task.validators)
                task.status = Status.COMPLETED if all(c["passed"] for c in task.validation_results) else Status.FAILED
                if task.status == Status.FAILED:
                    task.escalation_reason = "Deterministic validation failed; inspect inputs"
            except (ValueError, KeyError, TypeError) as exc:
                task.status = Status.FAILED
                task.escalation_reason = "Deterministic input or validation error: " + type(exc).__name__
            self.store.save(task)
            with self.store.db:
                self.store.event(project.id, task.id, "local_finished", {"status": task.status, "validation": task.validation_results})
            return
        if decision.tier == Tier.HUMAN:
            self.pause(task, decision.reason)
            return
        def sensitive_ancestor(key):
            ancestor = tasks[key]
            return ancestor.sensitive or any(sensitive_ancestor(d) for d in ancestor.dependencies)
        if self.live and (not project.allow_external or task.sensitive or any(sensitive_ancestor(d) for d in task.dependencies)):
            self.pause(task, "External data sharing is disabled for this project or task", "privacy")
            return
        if task.requires_approval and not task.approved:
            self.pause(task, "Approval required before processing this task", "approval", {"expected_cost_microusd": None})
            return
        try:
            context = collect(self.context_root, task.context_files, self.settings.max_context_bytes)
            state = {"description": task.description, "task_type": task.task_type, "risk": task.risk,
                     "complexity": task.complexity, "capabilities": task.capabilities, "inputs": task.inputs,
                     "context": context, "dependencies": {d: tasks[d].output for d in task.dependencies}}
            if len(json.dumps(state).encode()) > self.settings.max_context_bytes:
                raise ValueError("Combined context budget exceeded; narrow inputs and dependencies")
            task.metadata["context_manifest"] = [{k: v for k, v in c.items() if k != "text"} for c in context]
            if task.task_type == "jev_decision":
                questions = task.inputs.get("questions")
                question_source = task.inputs.get("questions_from_dependency")
                if question_source is not None:
                    if questions is not None:
                        raise ValueError("Use either static questions or questions_from_dependency, not both")
                    if not isinstance(question_source, str) or question_source not in task.dependencies:
                        raise ValueError("questions_from_dependency must name a completed dependency")
                    generated = tasks[question_source].output
                    generated = generated.get("result") if isinstance(generated, dict) and "result" in generated else generated
                    if isinstance(generated, str):
                        generated = json.loads(generated)
                    if isinstance(generated, dict) and set(generated) == {"questions"}:
                        generated = generated["questions"]
                    questions = generated
                decision_state = {
                    "task": task.description,
                    "input": {k: v for k, v in task.inputs.items() if k not in ("questions", "questions_from_dependency")},
                    "context": context,
                    "dependencies": {d: tasks[d].output for d in task.dependencies},
                }
                if len(json.dumps(decision_state).encode()) > self.settings.max_context_bytes:
                    raise ValueError("Jev decision state exceeds context limit; narrow inputs and dependencies")
                payload = self.jev_decision.prepare(decision_state, questions)
                if len(json.dumps(payload).encode()) > self.settings.max_context_bytes:
                    raise ValueError("Combined Jev decision request exceeds context limit")
                reply = self.call(project, task, Tier.JEV, self.jev_decision, payload)
                if reply is None:
                    return
                task.output = reply.output
                task.validation_results = validate(task.output, task.validators)
                task.status = Status.COMPLETED if all(c["passed"] for c in task.validation_results) else Status.FAILED
                if task.status == Status.FAILED:
                    task.escalation_reason = "Jev decision validation failed; inspect typed answers"
                self.store.save(task)
                return
            # Catch runtime/login problems before paying Jev for an unusable execution path.
            if self.live and self.settings.openai_backend == "chatgpt":
                for adapter in self.generators.values():
                    if hasattr(adapter, "preflight"):
                        try:
                            adapter.preflight()
                        except AdapterError as exc:
                            self.pause(task, exc.category, "subscription")
                            return
            fingerprint = digest({"state": state, "assessment_provider": self.settings.assessment_provider})
            cached = task.metadata.get("assessment")
            if self.settings.assessment_provider == "declared":
                if task.metadata.get("routing_reviewed") is not True:
                    self.pause(task, "Declared routing requires metadata.routing_reviewed=true after reviewing risk, complexity and flags", "human")
                    return
                signals = {k: 0.0 for k, q in QUESTIONS.items() if q["type"] == "noul"}
                signals.update(generation=1.0, context_sufficient=float(task.metadata.get("context_sufficient") is True))
                for key in ("architecture", "security", "irreversible"):
                    signals[key] = float(task.metadata.get(key) is True)
                assessment = Assessment(signals, task.risk, task.complexity, 1.0)
                task.metadata["assessment_source"] = "operator_declared_not_model_confidence"
                task.metadata["assessment"] = assessment.to_dict()
                task.metadata["assessment_fingerprint"] = fingerprint
                self.store.save(task)
                with self.store.db:
                    self.store.event(project.id, task.id, "declared_routing", {"risk": task.risk, "complexity": task.complexity})
            elif cached and task.metadata.get("assessment_fingerprint") == fingerprint:
                assessment = Assessment(**cached)
            else:
                reply = self.call(project, task, Tier.JEV, self.jev, self.jev.prepare(state))
                if reply is None:
                    return
                assessment = Assessment(**reply.output)
                task.metadata["assessment_source"] = "jev" if self.jev.external else "mock"
                task.metadata["assessment"] = reply.output
                task.metadata["assessment_fingerprint"] = fingerprint
                self.store.save(task)
        except (ValueError, TypeError, KeyError, OSError) as exc:
            self.pause(task, str(exc), "configuration")
            return
        while True:
            attempts = self.store.attempts(project.id, task.id)
            failures = {tier: sum(a["tier"] == tier and a["status"] == "FAILED" for a in attempts) for tier in (Tier.SOL, Tier.ASTRA)}
            decision = route(task, assessment, self.settings, failures[Tier.SOL], failures[Tier.ASTRA])
            task.selected_tier = decision.tier
            task.escalation_reason = decision.reason
            self.store.save(task)
            with self.store.db:
                self.store.event(project.id, task.id, "routed", {"tier": decision.tier, "reason": decision.reason})
            if decision.tier == Tier.HUMAN:
                self.pause(task, decision.reason)
                return
            failed = [a for a in attempts if a["status"] == "FAILED" and a["tier"] in ("SOL", "ASTRA")]
            if failed:
                # Latest evidence plus bounded attempt summaries, not entire conversation histories.
                last = failed[-1]["data"]
                state["repair"] = {"failures": [{"tier": a["tier"], "error": a["data"].get("error"), "validation": a["data"].get("validation")} for a in failed],
                                   "previous_output": last.get("output"), "instruction": "Correct the specific failures; change the approach using this evidence."}
            if len(json.dumps(state).encode()) > self.settings.max_context_bytes:
                self.pause(task, "Repair evidence exceeds context limit; compact evidence before resuming", "configuration")
                return
            adapter = self.generators[decision.tier]
            try:
                reply = self.call(project, task, decision.tier, adapter, adapter.prepare(state))
            except (ValueError, TypeError, KeyError) as exc:
                self.pause(task, str(exc), "configuration")
                return
            if reply is None:
                # Validation failures can repair; transport and authorization failures stop.
                if task.status == Status.PENDING:
                    continue
                return
            task.output = reply.output
            task.status = Status.COMPLETED
            task.metadata.pop("gate", None)
            self.store.save(task)
            return

    def call(self, project, task, tier, adapter, payload):
        cfg = self.settings
        external = adapter.external
        billing = getattr(adapter, "billing", "api" if external else "simulation")
        token_priced = external and billing == "api"
        subscription = billing == "chatgpt_subscription"
        spec = cfg.models[tier.lower()]
        if hasattr(adapter, "preflight"):
            try:
                adapter.preflight()
            except AdapterError as exc:
                self.pause(task, exc.category, "subscription" if subscription else "provider")
                return None
        if subscription:
            calls = [a for a in self.store.attempts(project.id) if a["data"].get("billing") == billing and a["data"].get("dispatched") is not False]
            if len(calls) >= cfg.max_subscription_calls or (tier == Tier.ASTRA and sum(a["tier"] == "ASTRA" for a in calls) >= cfg.max_subscription_astra_calls):
                self.pause(task, "Local subscription call limit reached; remaining account allowance is unknown", "subscription")
                return None
        upper = estimate(payload, spec, cfg.max_output_tokens) if token_priced else 0
        usage = self.store.spend(project.id)
        outstanding = [a for a in self.store.attempts(project.id, task.id) if a["status"] in ("STARTED", "UNKNOWN")]
        if outstanding:
            self.pause(task, "Unresolved request outcome; reconcile before retry", "reconcile")
            return None
        if token_priced and (upper > cfg.max_single_call_microusd or usage["total_microusd"] + usage["reserved_microusd"] + upper > project.budget_microusd):
            self.pause(task, "Estimated request exceeds call or project budget", "budget", {"expected_cost_microusd": upper})
            return None
        astra_reserved = sum(a["reserved"] for a in self.store.attempts(project.id) if a["tier"] == "ASTRA" and a["status"] in ("STARTED", "UNKNOWN"))
        if token_priced and tier == Tier.ASTRA and usage["by_tier_microusd"]["ASTRA"] + astra_reserved + upper > cfg.max_astra_microusd:
            self.pause(task, "Astra budget exceeded", "budget", {"expected_cost_microusd": upper})
            return None
        request_hash = digest({"billing": billing, "payload": payload})
        approval = task.metadata.get("approved_request", {})
        approved_cost = approval.get("hash") == request_hash and upper <= approval.get("max_cost_microusd", -1)
        if upper > cfg.approval_above_microusd and not approved_cost:
            task.metadata["pending_request"] = payload
            self.pause(task, "Review estimated cost and external request before execution", "approval", {"request_hash": request_hash, "expected_cost_microusd": upper, "tier": tier})
            return None
        attempts = self.store.attempts(project.id, task.id)
        if any(a["tier"] == tier and a["data"].get("request_hash") == request_hash and a["data"].get("dispatched") is not False for a in attempts):
            self.pause(task, "Identical paid/model request already attempted; change input or review result", "human")
            return None
        record = {"request_hash": request_hash, "request": payload, "price_snapshot": spec if token_priced else None,
                  "billing": billing, "cost_basis": "configured_token_rates" if token_priced else billing,
                  "estimated_max_microusd": upper if not subscription else None}
        task.status = Status.RUNNING
        task.selected_tier = tier
        task.selected_model = adapter.model
        task.attempts += 1
        # Persist reservation and running status together before crossing the boundary.
        with self.store.db:
            cursor = self.store.db.execute("INSERT INTO attempts(project_id,task_id,tier,mode,status,reserved,cost,data,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                                           (project.id, task.id, tier, self.mode, "STARTED", upper, None, json.dumps(record), now()))
            attempt_id = cursor.lastrowid
            task.updated_at = now()
            self.store.db.execute("UPDATE tasks SET data=? WHERE project_id=? AND id=?", (json.dumps(task.to_dict()), project.id, task.id))
        started = time.monotonic()
        reply, error, charged, status = None, None, None if subscription else 0, "SUCCEEDED"
        try:
            reply = adapter.invoke(payload)
            charged = cost(reply.usage, spec) if token_priced else (None if subscription else 0)
            record.update(output=reply.output, usage=asdict(reply.usage), model=reply.model, latency_ms=reply.latency_ms, response_id=reply.response_id, provider_metadata=reply.metadata)
            checks = [] if tier == Tier.JEV else validate(reply.output, task.validators, generated=True)
            record["validation"] = checks
            task.validation_results = checks
            if not all(c["passed"] for c in checks):
                error, status = "validation_failed", "FAILED"
        except AdapterError as exc:
            error = exc.category
            record["provider_metadata"] = exc.metadata
            if exc.not_dispatched:
                record["dispatched"] = False
            status = "UNKNOWN" if exc.uncertain else "FAILED"
            if exc.usage is not None:
                charged = cost(exc.usage, spec) if token_priced else (None if subscription else 0)
                record["usage"] = asdict(exc.usage)
            if exc.status:
                record["http_status"] = exc.status
        except Exception as exc:
            # Unexpected errors after dispatch must not release a potentially billable reservation.
            error, status = "adapter_or_validator_error:" + type(exc).__name__, "UNKNOWN"
        record["latency_ms"] = int((time.monotonic() - started) * 1000)
        if error:
            record["error"] = error
        with self.store.db:
            self.store.db.execute("UPDATE attempts SET status=?,cost=?,data=? WHERE id=?", (status, charged, json.dumps(record), attempt_id))
            self.store.event(project.id, task.id, "attempt_finished", {"attempt_id": attempt_id, "tier": tier, "status": status, "cost_microusd": charged, "latency_ms": record["latency_ms"], "error": error})
        u = record.get("usage", {})
        task.input_tokens += u.get("input_tokens", 0)
        task.output_tokens += u.get("output_tokens", 0)
        task.cached_tokens += u.get("cached_tokens", 0)
        task.model_cost_microusd += charged or 0
        task.metadata.pop("approved_request", None)
        task.metadata.pop("pending_request", None)
        self.store.save(task)
        if status == "UNKNOWN":
            self.pause(task, f"{error}: provider outcome or usage unknown; reconcile attempt {attempt_id}", "reconcile")
            return None
        if error:
            repairable = error in ("validation_failed", "invalid_structured_output", "incomplete_response") and tier != Tier.JEV
            task.status = Status.PENDING if repairable else Status.WAITING_FOR_APPROVAL
            task.escalation_reason = error
            if not repairable:
                task.metadata["gate"] = {"kind": "subscription" if subscription else "provider", "reason": error}
            self.store.save(task)
            return None
        if token_priced and charged > upper:
            self.pause(task, "Actual estimated charge exceeded reservation; inspect pricing before continuing", "budget")
            return None
        return reply
