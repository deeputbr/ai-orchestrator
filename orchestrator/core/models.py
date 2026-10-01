"""Persisted records. Untrusted JSON is checked before it becomes a task."""
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import StrEnum
import re
from typing import Any


def now():
    return datetime.now(timezone.utc).isoformat()


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", value):
        raise ValueError("IDs must be 1–80 letters, numbers, dots, underscores or hyphens")
    return value


class Status(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"


class Tier(StrEnum):
    CODE = "CODE"
    JEV = "JEV"
    SOL = "SOL"
    ASTRA = "ASTRA"
    HUMAN = "HUMAN"


@dataclass
class Project:
    id: str
    goal: str
    budget_microusd: int = 1_000_000
    allow_external: bool = False
    phase: str = "created"
    decisions: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=now)

    def __post_init__(self):
        identifier(self.id)
        if not isinstance(self.goal, str) or not self.goal.strip():
            raise ValueError("Project goal is required")
        if type(self.budget_microusd) is not int or self.budget_microusd < 0:
            raise ValueError("Budget must be a non-negative integer in micro-USD")
        if type(self.allow_external) is not bool:
            raise ValueError("allow_external must be boolean")


@dataclass
class Task:
    id: str
    project_id: str
    description: str
    task_type: str = "generation"
    workflow_id: str = "default"
    job_id: str = "default"
    parent_task: str | None = None
    status: str = Status.PENDING
    risk: str = "LOW"
    complexity: str = "MEDIUM"
    capabilities: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    context_files: list[str] = field(default_factory=list)
    inputs: dict[str, Any] = field(default_factory=dict)
    validators: list[dict] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    sensitive: bool = False
    requires_approval: bool = False
    approved: bool = False
    selected_tier: str | None = None
    selected_model: str | None = None
    attempts: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    model_cost_microusd: int = 0
    external_cost_microusd: int = 0
    validation_results: list[dict] = field(default_factory=list)
    escalation_reason: str | None = None
    output: Any = None
    created_at: str = field(default_factory=now)
    updated_at: str = field(default_factory=now)

    def __post_init__(self):
        for value in (self.id, self.project_id, self.workflow_id, self.job_id):
            identifier(value)
        if not isinstance(self.description, str) or not self.description.strip():
            raise ValueError("Task description is required")
        Status(self.status)
        if self.risk not in ("LOW", "MEDIUM", "HIGH", "CRITICAL"):
            raise ValueError("Invalid risk")
        if self.complexity not in ("LOW", "MEDIUM", "HIGH", "VERY_HIGH"):
            raise ValueError("Invalid complexity")
        for name in ("dependencies", "capabilities", "context_files"):
            values = getattr(self, name)
            if not isinstance(values, list) or not all(isinstance(x, str) for x in values):
                raise ValueError(f"{name} must be a string list")
        if self.id in self.dependencies or len(set(self.dependencies)) != len(self.dependencies):
            raise ValueError("Duplicate or self dependency")
        for name in ("sensitive", "requires_approval", "approved"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be boolean")
        if not isinstance(self.inputs, dict) or not isinstance(self.metadata, dict):
            raise ValueError("inputs and metadata must be objects")
        if not isinstance(self.validators, list) or not all(isinstance(v, dict) for v in self.validators):
            raise ValueError("validators must be a list of objects")

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class Decision:
    tier: Tier
    reason: str


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0

    def __post_init__(self):
        if any(type(x) is not int or x < 0 for x in (self.input_tokens, self.output_tokens, self.cached_tokens)):
            raise ValueError("Invalid token usage")
        if self.cached_tokens > self.input_tokens:
            raise ValueError("Cached tokens exceed input tokens")
