"""Validate workflow imports consistently across the CLI and web interface."""
from orchestrator.core.models import Task

FIELDS = {"id", "description", "task_type", "workflow_id", "job_id", "parent_task", "risk", "complexity", "capabilities", "dependencies", "context_files", "inputs", "validators", "metadata", "sensitive", "requires_approval"}
FLAGS = {"architecture", "security", "irreversible", "routing_reviewed", "context_sufficient", "user"}


def import_tasks(store, project_id, data):
    if not isinstance(data, dict) or set(data) != {"tasks"} or not isinstance(data["tasks"], list) or not data["tasks"]:
        raise ValueError("A workflow must contain a non-empty tasks list")
    if len(data["tasks"]) > 500:
        raise ValueError("Import at most 500 tasks at a time")
    tasks = []
    for item in data["tasks"]:
        if not isinstance(item, dict) or set(item) - FIELDS:
            raise ValueError("Workflow contains runtime state fields or unsupported task fields")
        metadata = item.get("metadata", {})
        if not isinstance(metadata, dict) or set(metadata) - FLAGS:
            raise ValueError("Only declared routing flags and user metadata may be imported")
        tasks.append(Task(**(item | {"project_id": project_id})))
    store.add_tasks(project_id, tasks)
    return {"added": len(tasks)}
