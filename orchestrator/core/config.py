from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
import tomllib


@dataclass
class Settings:
    openai_backend: str = "chatgpt"
    codex_binary: str = "auto"
    codex_timeout_seconds: int = 180
    max_subscription_calls: int = 20
    max_subscription_astra_calls: int = 2
    assessment_provider: str = "jev"
    max_context_bytes: int = 24000
    max_output_tokens: int = 2048
    timeout_seconds: int = 45
    max_single_call_microusd: int = 250000
    max_astra_microusd: int = 1000000
    approval_above_microusd: int = 100000
    confidence_threshold: float = 0.8
    sol_max_attempts: int = 2
    astra_max_attempts: int = 1
    astra_triggers: list[str] = field(default_factory=lambda: ["high_risk", "architecture", "security", "irreversible", "very_high_complexity", "repeated_failure"])
    models: dict = field(default_factory=lambda: {k: {"id": v, "input_per_million": "", "output_per_million": "", "cached_per_million": ""} for k, v in {"jev": "jev-latest", "sol": "gpt-6-sol", "astra": "gpt-6-astra"}.items()})

    def __post_init__(self):
        if self.openai_backend not in ("chatgpt", "api"):
            raise ValueError("openai_backend must be chatgpt or api; automatic fallback is unsupported")
        if self.assessment_provider not in ("jev", "declared"):
            raise ValueError("assessment_provider must be jev or declared")
        if not isinstance(self.codex_binary, str) or not self.codex_binary:
            raise ValueError("codex_binary must be auto or an executable path")
        for name in ("max_context_bytes", "max_output_tokens", "timeout_seconds", "codex_timeout_seconds", "sol_max_attempts", "astra_max_attempts"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(f"{name} must be a positive integer")
        for name in ("max_single_call_microusd", "max_astra_microusd", "approval_above_microusd", "max_subscription_calls", "max_subscription_astra_calls"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 0:
                raise ValueError(f"{name} must be non-negative")
        if type(self.confidence_threshold) not in (float, int) or not 0.5 < self.confidence_threshold <= 1:
            raise ValueError("confidence_threshold must be in (0.5, 1]")
        known = {"high_risk", "architecture", "security", "irreversible", "very_high_complexity", "repeated_failure"}
        if not isinstance(self.astra_triggers, list) or any(x not in known for x in self.astra_triggers):
            raise ValueError("Unknown Astra trigger")
        if set(self.models) != {"jev", "sol", "astra"}:
            raise ValueError("Configure jev, sol and astra models")
        for spec in self.models.values():
            if not isinstance(spec.get("id"), str) or not spec["id"]:
                raise ValueError("Model id is required")
            for key in ("input_per_million", "output_per_million", "cached_per_million"):
                value = spec.get(key, "")
                if value != "":
                    try:
                        number = Decimal(str(value))
                    except InvalidOperation as exc:
                        raise ValueError("Invalid price") from exc
                    if not number.is_finite() or number < 0:
                        raise ValueError("Prices must be finite and non-negative")

    @classmethod
    def load(cls, path=None):
        if path is None:
            return cls()
        with Path(path).open("rb") as stream:
            raw = tomllib.load(stream)
        allowed = {"execution", "budgets", "routing", "models", "providers", "subscription"}
        if set(raw) - allowed:
            raise ValueError("Unknown settings section")
        values = {}
        for section in ("execution", "budgets", "routing", "providers", "subscription"):
            values.update(raw.get(section, {}))
        if "models" in raw:
            values["models"] = raw["models"]
        return cls(**values)
