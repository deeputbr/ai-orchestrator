"""SQLite is canonical. Events are JSON records, never raw provider error bodies."""
from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
import fcntl
import json
import sqlite3
from orchestrator.core.models import Project, Task, now


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.path.chmod(0o600)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY);
        INSERT OR IGNORE INTO schema_version VALUES(1);
        CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS tasks(
          project_id TEXT REFERENCES projects(id), id TEXT, data TEXT NOT NULL,
          PRIMARY KEY(project_id,id));
        CREATE TABLE IF NOT EXISTS events(
          seq INTEGER PRIMARY KEY, project_id TEXT, task_id TEXT, created_at TEXT, kind TEXT, data TEXT);
        CREATE TABLE IF NOT EXISTS attempts(
          id INTEGER PRIMARY KEY, project_id TEXT, task_id TEXT, tier TEXT, mode TEXT,
          status TEXT, reserved INTEGER, cost INTEGER, data TEXT, created_at TEXT,
          FOREIGN KEY(project_id, task_id) REFERENCES tasks(project_id,id));
        """)
        self.db.commit()
        if self.db.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] != 1:
            raise ValueError("Unsupported database schema version")

    def close(self):
        self.db.close()

    @contextmanager
    def lock(self):
        with self.path.with_suffix(".lock").open("a") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValueError("Another runner is using this database") from exc
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def create_project(self, project):
        with self.db:
            self.db.execute("INSERT INTO projects VALUES (?,?)", (project.id, json.dumps(asdict(project))))
            self.event(project.id, None, "project_created", {"budget_microusd": project.budget_microusd})

    def update_project(self, project):
        project.__post_init__()
        with self.db:
            cursor = self.db.execute("UPDATE projects SET data=? WHERE id=?", (json.dumps(asdict(project)), project.id))
            if cursor.rowcount != 1:
                raise ValueError("Unknown project")
            self.event(project.id, None, "project_updated", {"budget_microusd": project.budget_microusd, "allow_external": project.allow_external})

    def project(self, project_id):
        row = self.db.execute("SELECT data FROM projects WHERE id=?", (project_id,)).fetchone()
        if row is None:
            raise ValueError(f"Unknown project: {project_id}")
        return Project(**json.loads(row[0]))

    def add_tasks(self, project_id, tasks):
        self.project(project_id)
        existing = {t.id: t for t in self.tasks(project_id)}
        incoming = {t.id: t for t in tasks}
        if len(incoming) != len(tasks) or set(existing) & set(incoming):
            raise ValueError("Duplicate task ID")
        if any(t.project_id != project_id for t in tasks):
            raise ValueError("Task project mismatch")
        combined = existing | incoming
        visiting, visited = set(), set()
        def visit(key):
            if key not in combined:
                raise ValueError(f"Unknown dependency: {key}")
            if key in visiting:
                raise ValueError("Dependency cycle")
            if key in visited:
                return
            visiting.add(key)
            for dependency in combined[key].dependencies:
                visit(dependency)
            visiting.remove(key)
            visited.add(key)
        for key in combined:
            visit(key)
        with self.db:
            for task in tasks:
                self.db.execute("INSERT INTO tasks VALUES (?,?,?)", (project_id, task.id, json.dumps(task.to_dict())))
                self.event(project_id, task.id, "task_added", {"task_type": task.task_type})

    def tasks(self, project_id):
        return [Task(**json.loads(row[0])) for row in self.db.execute("SELECT data FROM tasks WHERE project_id=? ORDER BY rowid", (project_id,))]

    def task(self, project_id, task_id):
        for task in self.tasks(project_id):
            if task.id == task_id:
                return task
        raise ValueError(f"Unknown task: {task_id}")

    def save(self, task):
        task.updated_at = now()
        with self.db:
            cursor = self.db.execute("UPDATE tasks SET data=? WHERE project_id=? AND id=?", (json.dumps(task.to_dict()), task.project_id, task.id))
            if cursor.rowcount != 1:
                raise ValueError("Cannot update unknown task")

    def event(self, project_id, task_id, kind, data):
        self.db.execute("INSERT INTO events(project_id,task_id,created_at,kind,data) VALUES (?,?,?,?,?)", (project_id, task_id, now(), kind, json.dumps(data)))

    def events(self, project_id):
        return [{**dict(row), "data": json.loads(row["data"])} for row in self.db.execute("SELECT * FROM events WHERE project_id=? ORDER BY seq", (project_id,))]

    def attempts(self, project_id, task_id=None):
        rows = self.db.execute("SELECT * FROM attempts WHERE project_id=? ORDER BY id", (project_id,))
        return [{**dict(r), "data": json.loads(r["data"])} for r in rows if task_id is None or r["task_id"] == task_id]

    def spend(self, project_id):
        totals = dict.fromkeys(("JEV", "SOL", "ASTRA", "HIGGSFIELD", "OTHER", "BLENDER"), 0)
        reserved = 0
        for a in self.attempts(project_id):
            totals[a["tier"]] = totals.get(a["tier"], 0) + (a["cost"] or 0)
            if a["status"] in ("STARTED", "UNKNOWN"):
                reserved += a["reserved"]
        return {"by_tier_microusd": totals, "total_microusd": sum(totals.values()), "reserved_microusd": reserved}

    def subscription_usage(self, project_id):
        attempts = [a for a in self.attempts(project_id) if a["data"].get("billing") == "chatgpt_subscription"]
        dispatched = [a for a in attempts if a["data"].get("dispatched") is not False]
        total = {"input_tokens": 0, "output_tokens": 0, "cached_tokens": 0}
        for attempt in dispatched:
            for key in total:
                total[key] += attempt["data"].get("usage", {}).get(key, 0)
        return {"billing": "chatgpt_subscription", "calls": len(dispatched),
                "attempt_records": len(attempts),
                "by_tier": dict(Counter(a["tier"] for a in dispatched)), "known_tokens": total,
                "calls_without_usage": sum("usage" not in a["data"] for a in dispatched),
                "not_dispatched_calls": sum(a["data"].get("dispatched") is False for a in attempts),
                "unresolved_calls": sum(a["status"] in ("STARTED", "UNKNOWN") for a in attempts),
                "remaining_account_allowance": None, "usd_cost": None,
                "note": "Uses shared ChatGPT Work/Codex limits and any account credits. Tokens cannot be converted to remaining percentage here."}

    def summary(self, project_id):
        p = self.project(project_id)
        tasks = self.tasks(project_id)
        spend = self.spend(project_id)
        return {"project": asdict(p), "tasks": len(tasks), "statuses": dict(Counter(t.status for t in tasks)),
                "jobs": {job: dict(Counter(t.status for t in tasks if t.job_id == job)) for job in sorted({t.job_id for t in tasks})},
                "execution_modes": dict(Counter(t.metadata.get("execution_mode", "not_run") for t in tasks)),
                "completed": [t.id for t in tasks if t.status == "COMPLETED"],
                "outstanding": [t.id for t in tasks if t.status != "COMPLETED"],
                "blockers": [{"task": t.id, "reason": t.escalation_reason} for t in tasks if t.status in ("FAILED", "WAITING_FOR_APPROVAL")],
                "spend": spend, "subscription_usage": self.subscription_usage(project_id), "budget_remaining_microusd": p.budget_microusd - spend["total_microusd"] - spend["reserved_microusd"]}
