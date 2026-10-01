"""Human actions. The CLI serializes these under the store lock."""
import json
from orchestrator.core.models import Status


def approve(store, project_id, task_id):
    task = store.task(project_id, task_id)
    gate = task.metadata.get("gate", {})
    if task.status != Status.WAITING_FOR_APPROVAL or gate.get("kind") != "approval":
        raise ValueError("Only an explicit approval gate can be approved; budgets/privacy/routing cannot be bypassed")
    if "request_hash" in gate:
        task.metadata["approved_request"] = {"hash": gate["request_hash"], "max_cost_microusd": gate["expected_cost_microusd"]}
    else:
        task.approved = True
    task.status = Status.PENDING
    task.metadata.pop("gate", None)
    store.save(task)
    with store.db:
        store.event(project_id, task_id, "human_approved", gate)
    return {"task": task_id, "status": task.status}


def resume(store, project_id, task_id):
    task = store.task(project_id, task_id)
    if task.status not in (Status.WAITING_FOR_APPROVAL, Status.FAILED):
        raise ValueError("Task is not paused or failed")
    if task.metadata.get("gate", {}).get("kind") == "approval":
        raise ValueError("Use approve for an approval gate")
    if any(a["status"] in ("STARTED", "UNKNOWN") for a in store.attempts(project_id, task_id)):
        raise ValueError("Reconcile unknown requests before resuming")
    task.status = Status.PENDING
    task.metadata.pop("gate", None)
    store.save(task)
    with store.db:
        store.event(project_id, task_id, "human_resumed", {})
    return {"task": task_id, "status": task.status}


def reconcile(store, project_id, attempt_id, amount, note, not_dispatched=False):
    if type(amount) is not int or amount < 0 or not note.strip():
        raise ValueError("Reconciliation requires a non-negative verified charge and note")
    matches = [a for a in store.attempts(project_id) if a["id"] == attempt_id]
    if not matches or matches[0]["status"] not in ("STARTED", "UNKNOWN"):
        raise ValueError("Attempt is not awaiting reconciliation")
    attempt = matches[0]
    subscription = attempt["data"].get("billing") == "chatgpt_subscription"
    if subscription and amount != 0:
        raise ValueError("Subscription usage cannot be reconciled as an API USD charge; use 0 with an outcome note")
    if not_dispatched and amount != 0:
        raise ValueError("A request not dispatched cannot have a charge")
    data = attempt["data"] | {"reconciliation": note}
    if not_dispatched:
        data["dispatched"] = False
    task = store.task(project_id, attempt["task_id"])
    task.model_cost_microusd += amount - (attempt["cost"] or 0)
    with store.db:
        store.db.execute("UPDATE attempts SET status='RECONCILED',cost=?,data=? WHERE id=?", (None if subscription else amount, json.dumps(data), attempt_id))
        store.db.execute("UPDATE tasks SET data=? WHERE project_id=? AND id=?", (json.dumps(task.to_dict()), project_id, task.id))
        store.event(project_id, task.id, "charge_reconciled", {"attempt_id": attempt_id, "cost_microusd": amount, "note": note})
    return {"attempt_id": attempt_id, "status": "RECONCILED", "cost_microusd": amount, "next": "Inspect saved output. Use a revised task for another call; identical requests are not replayed."}
