from dataclasses import dataclass, field
from typing import Any
from orchestrator.core.models import Usage


class AdapterError(Exception):
    """Only sanitized category/status is exposed; provider bodies may contain secrets."""
    def __init__(self, category, *, uncertain=False, usage=None, status=None, not_dispatched=False, metadata=None):
        self.metadata = metadata or {}
        self.not_dispatched = not_dispatched
        self.category = category
        self.uncertain = uncertain
        self.usage = usage
        self.status = status
        super().__init__(category + (f" (HTTP {status})" if status else ""))


@dataclass
class Reply:
    output: Any
    usage: Usage
    model: str
    latency_ms: int
    response_id: str | None = None
    metadata: dict = field(default_factory=dict)


def usage_from(raw, cached=False):
    try:
        if not isinstance(raw, dict):
            raise ValueError("Usage must be an object")
        if cached and not isinstance(raw.get("input_tokens_details", {}), dict):
            raise ValueError("Invalid cached token details")
        return Usage(raw["input_tokens"], raw["output_tokens"], raw.get("input_tokens_details", {}).get("cached_tokens", 0) if cached else 0)
    except (KeyError, TypeError, ValueError) as exc:
        raise AdapterError("invalid_usage", uncertain=True) from exc
