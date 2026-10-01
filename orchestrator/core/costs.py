"""USD per million tokens numerically equals micro-USD per token."""
from decimal import Decimal, ROUND_CEILING
import json
from orchestrator.core.models import Usage


def rates(spec):
    keys = ("input_per_million", "output_per_million", "cached_per_million")
    if any(spec.get(k, "") == "" for k in keys):
        raise ValueError("Live pricing is unconfigured; set all three per-million rates")
    values = [Decimal(str(spec[k])) for k in keys]
    if any(not v.is_finite() or v < 0 for v in values):
        raise ValueError("Invalid pricing")
    return values


def cost(usage, spec):
    inp, out, cached = rates(spec)
    value = (usage.input_tokens - usage.cached_tokens) * inp + usage.cached_tokens * cached + usage.output_tokens * out
    return int(value.to_integral_value(rounding=ROUND_CEILING))


def estimate(payload, spec, max_output_tokens):
    # Deliberately conservative text-only envelope, not a tokenizer or billing guarantee.
    # Include question/schema overhead. Do not spend speculative cache discounts.
    input_bound = 2 * len(json.dumps(payload, ensure_ascii=False).encode()) + 2048
    inp, out, cached = rates(spec)
    value = Decimal(input_bound) * max(inp, cached) * Decimal("1.25") + max_output_tokens * out
    return int(value.to_integral_value(rounding=ROUND_CEILING))
