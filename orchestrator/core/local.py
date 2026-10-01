"""Small trusted registry; no eval, arbitrary shell or model-authored tool names."""
import hashlib
import json


def execute(operation, inputs):
    if operation == "json_normalize":
        return {"result": json.loads(inputs["text"])}
    if operation == "sha256":
        return {"result": hashlib.sha256(inputs["text"].encode()).hexdigest()}
    if operation == "word_count":
        return {"result": len(inputs["text"].split())}
    raise ValueError(f"Unknown deterministic operation: {operation}")


OPERATIONS = {"json_normalize", "sha256", "word_count"}
