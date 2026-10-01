import argparse
import json
import os
import sqlite3
import sys
from orchestrator.core.models import Project, Task
from orchestrator.core.state import Store
from orchestrator.core.config import Settings
from orchestrator.core.engine import Engine
from orchestrator.adapters.codex import CodexAdapter
from orchestrator.adapters.base import AdapterError
from orchestrator.core.control import approve, resume, reconcile
from pathlib import Path


def parser():
    p = argparse.ArgumentParser(description="Local-first personal AI orchestrator")
    p.add_argument("--db", default=".orchestrator/state.sqlite3")
    p.add_argument("--config")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="Check Codex runtime and ChatGPT sign-in without a model call")
    create = sub.add_parser("create", help="Create a project")
    create.add_argument("project")
    create.add_argument("--goal", required=True)
    create.add_argument("--budget-microusd", type=int, default=1_000_000)
    create.add_argument("--allow-external", action="store_true")
    configure = sub.add_parser("configure-project", help="Update project budget or external-data consent")
    configure.add_argument("project")
    configure.add_argument("--budget-microusd", type=int)
    configure.add_argument("--external", choices=["allow", "deny"])
    add = sub.add_parser("add", help="Import a JSON workflow's tasks")
    add.add_argument("project")
    add.add_argument("file")
    run = sub.add_parser("run", help="Execute ready tasks; mock providers by default")
    run.add_argument("project")
    run.add_argument("--live", action="store_true", help="Enable live execution; ChatGPT is the default OpenAI backend")
    run.add_argument("--context-root", default=".")
    for name in ("approve", "resume"):
        item = sub.add_parser(name)
        item.add_argument("project")
        item.add_argument("task")
    rec = sub.add_parser("reconcile", help="Record a verified charge for an interrupted request; does not replay it")
    rec.add_argument("project")
    rec.add_argument("attempt", type=int)
    rec.add_argument("--cost-microusd", type=int, help="Verified API charge; omit with --not-dispatched")
    rec.add_argument("--note", required=True)
    rec.add_argument("--not-dispatched", action="store_true", help="Attest verified startup failure before any provider request")
    exp = sub.add_parser("export", help="Save compact project state JSON")
    exp.add_argument("project")
    exp.add_argument("file")
    for name in ("status", "tasks", "events", "attempts"):
        item = sub.add_parser(name)
        item.add_argument("project")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    store = None
    try:
        if args.command == "doctor":
            cfg = Settings.load(args.config)
            if cfg.openai_backend == "chatgpt":
                result = CodexAdapter(cfg.models["sol"]["id"], cfg.codex_binary, cfg.codex_timeout_seconds).preflight()
                result["note"] = "No model call made. Account model access and remaining allowance are not verified."
            else:
                result = {"backend": "openai_api", "credential": "OPENAI_API_KEY",
                          "credential_present": bool(os.environ.get("OPENAI_API_KEY")),
                          "note": "No API call made. Model access and pricing are not verified."}
            result.update(openai_backend=cfg.openai_backend, assessment_provider=cfg.assessment_provider)
            generation_ready = result.get("authenticated_with") == "ChatGPT" or result.get("credential_present", False)
            if cfg.assessment_provider == "jev":
                jev_prices = cfg.models["jev"]
                result["jev"] = {
                    "model": jev_prices["id"],
                    "credential": "JEV_API_KEY or TYPESAFE_API_KEY",
                    "credential_present": bool(os.environ.get("JEV_API_KEY") or os.environ.get("TYPESAFE_API_KEY")),
                    "prices_configured": all(jev_prices.get(k, "") != "" for k in ("input_per_million", "output_per_million", "cached_per_million")),
                    "live_call_made": False,
                }
                result["ready_for_live"] = generation_ready and result["jev"]["credential_present"] and result["jev"]["prices_configured"]
            else:
                result["ready_for_live"] = generation_ready
            print(json.dumps(result, indent=2))
            return 0
        store = Store(args.db)
        with store.lock():
            if args.command == "create":
                store.create_project(Project(args.project, args.goal, args.budget_microusd, args.allow_external))
                result = store.summary(args.project)
            elif args.command == "configure-project":
                project = store.project(args.project)
                if args.budget_microusd is not None:
                    project.budget_microusd = args.budget_microusd
                if args.external:
                    project.allow_external = args.external == "allow"
                store.update_project(project)
                result = store.summary(args.project)
            elif args.command == "add":
                with open(args.file) as stream:
                    data = json.load(stream)
                allowed = {"id", "description", "task_type", "workflow_id", "job_id", "parent_task", "risk", "complexity", "capabilities", "dependencies", "context_files", "inputs", "validators", "metadata", "sensitive", "requires_approval"}
                for item in data["tasks"]:
                    if set(item) - allowed:
                        raise ValueError("Workflow contains runtime state fields")
                    if set(item.get("metadata", {})) - {"architecture", "security", "irreversible", "routing_reviewed", "context_sufficient", "user"}:
                        raise ValueError("Only declared routing flags and user metadata may be imported")
                tasks = [Task(**(t | {"project_id": args.project})) for t in data["tasks"]]
                if any(t.status != "PENDING" or t.approved or t.attempts for t in tasks):
                    raise ValueError("Imported tasks must be new and unapproved")
                store.add_tasks(args.project, tasks)
                result = {"added": len(tasks)}
            elif args.command == "run":
                result = Engine(store, Settings.load(args.config), live=args.live, context_root=args.context_root).run_locked(args.project)
            elif args.command == "approve":
                result = approve(store, args.project, args.task)
            elif args.command == "resume":
                result = resume(store, args.project, args.task)
            elif args.command == "reconcile":
                if args.cost_microusd is None and not args.not_dispatched:
                    raise ValueError("--cost-microusd is required unless --not-dispatched is used")
                result = reconcile(store, args.project, args.attempt, args.cost_microusd or 0, args.note, args.not_dispatched)
            elif args.command == "export":
                result = store.summary(args.project)
                target = Path(args.file)
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("x") as stream:
                    json.dump(result, stream, indent=2)
            elif args.command == "attempts":
                store.project(args.project)
                result = store.attempts(args.project)
            elif args.command == "tasks":
                store.project(args.project)
                result = [t.to_dict() for t in store.tasks(args.project)]
            elif args.command == "events":
                store.project(args.project)
                result = store.events(args.project)
            else:
                result = store.summary(args.project)
        print(json.dumps(result, indent=2))
        return 0
    except (ValueError, TypeError, KeyError, OSError, sqlite3.Error, AdapterError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    finally:
        if store:
            store.close()
