"""Only deterministic validators in this increment; never interpret success as quality."""
import json
from orchestrator.adapters.openai import validate_result


def check_validators(validators):
    for rule in validators:
        kind = rule.get("type")
        if kind not in ("contains", "equals", "json", "min_length"):
            raise ValueError("Unknown validator; register it before running")
        if kind == "contains" and not isinstance(rule.get("value"), str):
            raise ValueError("contains validator requires a string value")
        if kind == "min_length" and (type(rule.get("value")) is not int or rule["value"] < 0):
            raise ValueError("min_length validator requires a non-negative integer")
        if kind == "equals" and "value" not in rule:
            raise ValueError("equals validator requires a value")


def validate(output, validators, generated=False):
    check_validators(validators)
    checks = []
    if generated:
        checks.append({"validator": "structured_result", "passed": validate_result(output)})
    text = output.get("result", "") if isinstance(output, dict) else output
    for rule in validators:
        kind = rule.get("type")
        if kind == "contains":
            passed = isinstance(text, str) and isinstance(rule.get("value"), str) and rule["value"] in text
        elif kind == "equals":
            passed = text == rule.get("value")
        elif kind == "json":
            try:
                json.loads(text)
                passed = True
            except (ValueError, TypeError):
                passed = False
        elif kind == "min_length":
            size = rule.get("value")
            passed = type(size) is int and size >= 0 and isinstance(text, str) and len(text) >= size
        else:
            raise ValueError("Unknown validator; register it before running")
        checks.append({"validator": kind, "passed": passed, "expected": rule.get("value")})
    return checks
