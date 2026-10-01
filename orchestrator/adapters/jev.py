"""Official TypeSafe System One adapter. Jev never emits prose or code."""
from dataclasses import dataclass, asdict
import json
import math
import os
import re
import time
from orchestrator.adapters.base import AdapterError, Reply, usage_from
from orchestrator.adapters.http import JsonTransport
from orchestrator.core.models import Usage

QUESTIONS = {
    "deterministic": {"type": "noul", "instructions": "Can trusted deterministic code fully solve this task without interpretation or generation?"},
    "generation": {"type": "noul", "instructions": "Does the task require generating new prose or code?"},
    "deep_reasoning": {"type": "noul", "instructions": "Does the task require difficult multi-step reasoning?"},
    "visual": {"type": "noul", "instructions": "Does completing this task require interpreting images or video? Judge the description only."},
    "architecture": {"type": "noul", "instructions": "Does this involve significant system architecture decisions?"},
    "security": {"type": "noul", "instructions": "Does this involve security-sensitive design or changes?"},
    "irreversible": {"type": "noul", "instructions": "Does the requested execution have irreversible consequences?"},
    "context_sufficient": {"type": "noul", "instructions": "Is the supplied context sufficient to execute the task?"},
    "risk": {"type": "choice", "instructions": "Classify consequences of failure.", "criteria": {"LOW": "Minor reversible inconvenience", "MEDIUM": "Meaningful but recoverable impact", "HIGH": "Serious harm or high-value consequences", "CRITICAL": "Irreversible or critical harm"}},
    "complexity": {"type": "choice", "instructions": "Classify task difficulty.", "criteria": {"LOW": "Routine", "MEDIUM": "Ordinary engineering or generation", "HIGH": "Difficult reasoning", "VERY_HIGH": "Complex cross-system reasoning or severe ambiguity"}},
}

QUESTION_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,79}")
MAX_QUESTION_BYTES = 32_000


def decision_questions(value):
    """Validate a bounded TypeSafe question pack before it crosses the API boundary."""
    if not isinstance(value, dict) or not 1 <= len(value) <= 64:
        raise ValueError("Jev decisions require 1-64 typed questions")
    if len(json.dumps(value, ensure_ascii=False).encode()) > MAX_QUESTION_BYTES:
        raise ValueError("Jev question pack exceeds 32000 bytes")
    checked = {}
    for name, question in value.items():
        if not isinstance(name, str) or not QUESTION_ID.fullmatch(name) or not isinstance(question, dict):
            raise ValueError("Invalid Jev question id or definition")
        kind = question.get("type")
        instructions = question.get("instructions")
        if kind not in ("noul", "choice", "score") or not isinstance(instructions, str) or not instructions.strip():
            raise ValueError("Jev questions need a supported type and instructions")
        if len(instructions.encode()) > 4_000:
            raise ValueError("Jev question instructions are too large")
        expected = {"type", "instructions", "criteria"} if kind != "noul" or "criteria" in question else {"type", "instructions"}
        if set(question) != expected:
            raise ValueError("Jev question contains unsupported fields")
        if kind == "noul" and "criteria" in question:
            criteria = question["criteria"]
            if (not isinstance(criteria, dict) or set(criteria) != {"true", "false"}
                    or any(not isinstance(v, str) or not v.strip() for v in criteria.values())):
                raise ValueError("Jev Noul criteria must describe true and false")
        elif kind == "choice":
            criteria = question["criteria"]
            if not isinstance(criteria, dict) or not 2 <= len(criteria) <= 255:
                raise ValueError("Jev Choice requires 2-255 options")
            if any(not isinstance(k, str) or not QUESTION_ID.fullmatch(k) or (v is not None and (not isinstance(v, str) or not v.strip()))
                   for k, v in criteria.items()):
                raise ValueError("Jev Choice options require safe ids and descriptions")
        elif kind == "score":
            criteria = question["criteria"]
            if not isinstance(criteria, list) or not 2 <= len(criteria) <= 10 or any(not isinstance(x, str) or not x.strip() for x in criteria):
                raise ValueError("Jev Score requires 2-10 described levels")
        checked[name] = question
    return checked


def decision_answers(raw, questions):
    if not isinstance(raw, dict) or set(raw) != set(questions):
        raise ValueError("Jev decision answers do not match the questions")
    parsed = {}
    for name, question in questions.items():
        answer = raw[name]
        kind = question["type"]
        if not isinstance(answer, dict) or answer.get("type") != kind:
            raise ValueError("Jev decision answer has the wrong type")
        if kind == "noul":
            parsed[name] = {"type": kind, "noul": probability(answer.get("noul"))}
            continue
        confidence = probability(answer.get("confidence"))
        probabilities = answer.get("probabilities")
        if not isinstance(probabilities, dict) or abs(sum(probability(x) for x in probabilities.values()) - 1) > 0.02:
            raise ValueError("Invalid Jev probability distribution")
        if kind == "choice":
            options = set(question["criteria"])
            if set(probabilities) != options or answer.get("choice") not in options:
                raise ValueError("Invalid Jev Choice answer")
            parsed[name] = {"type": kind, "choice": answer["choice"], "confidence": confidence,
                            "probabilities": {k: probability(v) for k, v in probabilities.items()}}
        else:
            expected = {str(i) for i in range(len(question["criteria"]))}
            score = answer.get("score")
            if set(probabilities) != expected or type(score) not in (float, int) or not math.isfinite(score) or not 0 <= score <= len(expected) - 1:
                raise ValueError("Invalid Jev Score answer")
            parsed[name] = {"type": kind, "score": float(score), "confidence": confidence,
                            "probabilities": {k: probability(v) for k, v in probabilities.items()},
                            "levels": question["criteria"]}
    return parsed


def probability(value):
    if type(value) not in (float, int) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError("Invalid probability")
    return float(value)


@dataclass(frozen=True)
class Assessment:
    signals: dict[str, float]
    risk: str
    complexity: str
    confidence: float

    def __post_init__(self):
        required = {k for k, q in QUESTIONS.items() if q["type"] == "noul"}
        if not isinstance(self.signals, dict) or set(self.signals) != required:
            raise ValueError("Assessment signals are incomplete")
        for value in self.signals.values():
            probability(value)
        probability(self.confidence)
        if self.risk not in QUESTIONS["risk"]["criteria"] or self.complexity not in QUESTIONS["complexity"]["criteria"]:
            raise ValueError("Invalid risk or complexity")

    def to_dict(self):
        return asdict(self)

    @classmethod
    def parse(cls, raw):
        if not isinstance(raw, dict):
            raise ValueError("Answers must be an object")
        signals, confidences = {}, []
        for name, question in QUESTIONS.items():
            answer = raw[name]
            if not isinstance(answer, dict):
                raise ValueError("Answer must be an object")
            if answer["type"] != question["type"]:
                raise ValueError("Wrong answer type")
            if question["type"] == "noul":
                signals[name] = probability(answer["noul"])
            else:
                if answer["choice"] not in question["criteria"]:
                    raise ValueError("Unknown choice")
                probabilities = answer["probabilities"]
                if not isinstance(probabilities, dict):
                    raise ValueError("Distribution must be an object")
                if set(probabilities) != set(question["criteria"]):
                    raise ValueError("Incomplete distribution")
                if abs(sum(probability(x) for x in probabilities.values()) - 1) > 0.02:
                    raise ValueError("Invalid distribution")
                confidences.append(probability(answer["confidence"]))
        return cls(signals, raw["risk"]["choice"], raw["complexity"]["choice"], min(confidences))


class JevAdapter:
    external = True
    def __init__(self, model="jev-latest", transport=None, key=None):
        self.model = model
        self.transport = transport or JsonTransport()
        self.key = key if key is not None else os.getenv("JEV_API_KEY", os.getenv("TYPESAFE_API_KEY", ""))

    def prepare(self, state):
        return {"model": self.model, "state": state, "questions": QUESTIONS}

    def invoke(self, payload):
        started = time.monotonic()
        raw = self.transport.post("https://api.typesafe.ai/v1/systemone", self.key, payload)
        if not isinstance(raw, dict):
            raise AdapterError("invalid_response", uncertain=True)
        usage = usage_from(raw.get("usage"))
        try:
            assessment = Assessment.parse(raw["answers"])
            model = raw["model"]
            if not isinstance(model, str) or not model:
                raise ValueError("Missing model")
        except (KeyError, TypeError, ValueError):
            raise AdapterError("invalid_assessment", usage=usage) from None
        return Reply(assessment.to_dict(), usage, model, int((time.monotonic() - started) * 1000))


class JevDecisionAdapter(JevAdapter):
    """Runs workflow-authored atomic questions; code decides what to do with answers."""
    def prepare(self, state, questions):
        return {"model": self.model, "state": state, "questions": decision_questions(questions)}

    def invoke(self, payload):
        started = time.monotonic()
        raw = self.transport.post("https://api.typesafe.ai/v1/systemone", self.key, payload)
        if not isinstance(raw, dict):
            raise AdapterError("invalid_response", uncertain=True)
        usage = usage_from(raw.get("usage"))
        try:
            answers = decision_answers(raw["answers"], payload["questions"])
            model = raw["model"]
            if not isinstance(model, str) or not model:
                raise ValueError("Missing model")
        except (KeyError, TypeError, ValueError):
            raise AdapterError("invalid_decision", usage=usage) from None
        return Reply({"answers": answers}, usage, model, int((time.monotonic() - started) * 1000))


class MockJev(JevAdapter):
    external = False
    def __init__(self, assessment=None):
        self.model = "mock-jev"
        self.calls = 0
        self.assessment = assessment

    def invoke(self, payload):
        self.calls += 1
        state = payload["state"]
        # Fixtures are explicitly simulated, not a substitute classifier for live mode.
        signals = {k: 0.01 for k, v in QUESTIONS.items() if v["type"] == "noul"}
        signals.update(generation=0.99, context_sufficient=0.99)
        output = self.assessment or Assessment(signals, state.get("risk", "LOW"), state.get("complexity", "MEDIUM"), 0.99)
        return Reply(output.to_dict(), Usage(), self.model, 0)


class MockJevDecision(JevDecisionAdapter):
    external = False
    def __init__(self):
        self.model = "mock-jev"
        self.calls = []

    def invoke(self, payload):
        self.calls.append(payload)
        answers = {}
        for name, question in payload["questions"].items():
            if question["type"] == "noul":
                answers[name] = {"type": "noul", "noul": 0.5}
            elif question["type"] == "choice":
                options = list(question["criteria"])
                answers[name] = {"type": "choice", "choice": options[0], "confidence": 1.0,
                                 "probabilities": {k: float(k == options[0]) for k in options}}
            else:
                answers[name] = {"type": "score", "score": 0.0, "confidence": 1.0,
                                 "probabilities": {str(i): float(i == 0) for i in range(len(question["criteria"]))}}
        return Reply({"answers": decision_answers(answers, payload["questions"])}, Usage(), self.model, 0,
                     metadata={"simulated": True})
