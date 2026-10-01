"""Dependency-free local dashboard. Each request/worker owns its SQLite connection."""
import argparse
from dataclasses import asdict
from decimal import Decimal, InvalidOperation
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import sqlite3
from socketserver import TCPServer
import threading
from urllib.parse import urlsplit
from urllib.request import urlopen
import webbrowser

from orchestrator.adapters.base import AdapterError
from orchestrator.adapters.codex import CodexAdapter
from orchestrator.adapters.http import JsonTransport
from orchestrator.adapters.jev import JevAdapter, JevDecisionAdapter
from orchestrator.core.config import Settings
from orchestrator.core.control import approve, resume, reconcile
from orchestrator.core.engine import Engine
from orchestrator.core.models import Project, Task
from orchestrator.core.state import Store
from orchestrator.core.workflows import import_tasks

ASSETS = Path(__file__).parent / "static"
ROOT = Path(__file__).resolve().parents[2]
MAX_BODY = 1_000_000
TEMPLATES = {
    "local": ("Local starter", "Count words and normalize JSON. No model calls.", "examples/local.json"),
    "content": ("Content draft", "Outline, draft, then pause for your editorial approval.", "workflows/content_draft.json"),
    "coding": ("Coding proposal", "Generate and review a code proposal.", "workflows/coding_proposal.json"),
    "jev-choice": ("Generate, then choose", "Sol creates candidates; Jev selects and scores them.", "workflows/jev_choice.json"),
    "jev-dynamic": ("Sol builds Jev questions", "Sol prepares typed questions; Jev evaluates them.", "workflows/jev_dynamic_questions.json"),
}


def dollars(value):
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise ValueError("Enter a valid USD amount") from None
    if not number.is_finite() or number < 0 or number > 1_000_000 or number * 1_000_000 != (number * 1_000_000).to_integral_value():
        raise ValueError("USD amount must be non-negative, at most $1,000,000, and use at most six decimal places")
    return int(number * 1_000_000)


class Application:
    def __init__(self, db, settings, context_root):
        self.db = str(db)
        self.settings = settings
        self.context_root = str(Path(context_root).resolve())
        self.token = secrets.token_urlsafe(32)
        self.jev_key = None  # Browser-supplied key stays in process memory only.
        self.mutex = threading.Lock()
        self.job = None

    def connection_info(self):
        cfg = self.settings
        return {"backend": cfg.openai_backend, "assessment_provider": cfg.assessment_provider,
                "jev_key_present": bool(self.jev_key if self.jev_key is not None else os.getenv("JEV_API_KEY") or os.getenv("TYPESAFE_API_KEY")),
                "jev_key_source": "session" if self.jev_key else "environment",
                "models": {k: v["id"] for k, v in cfg.models.items()},
                "max_subscription_calls": cfg.max_subscription_calls,
                "max_subscription_astra_calls": cfg.max_subscription_astra_calls,
                "context_root": self.context_root}

    def snapshot(self):
        store = Store(self.db)
        try:
            with self.mutex:
                job = dict(self.job) if self.job else None
                connections = self.connection_info()
            return {"projects": [store.summary(p.id) for p in store.projects()], "job": job,
                    "connections": connections,
                    "templates": [{"id": k, "name": v[0], "description": v[1]} for k, v in TEMPLATES.items()
                                  if (ROOT / v[2]).is_file()]}
        finally:
            store.close()

    def detail(self, project_id):
        store = Store(self.db)
        try:
            return {"summary": store.summary(project_id), "tasks": [t.to_dict() for t in store.tasks(project_id)],
                    "attempts": store.attempts(project_id), "events": store.events(project_id)}
        finally:
            store.close()

    def mutate(self, action, data):
        with self.mutex:
            if self.job and self.job["status"] == "running":
                raise ValueError("A workflow is running. Wait for it to finish before changing projects or connections.")
            if action == "connections":
                key = data.get("jev_key")
                if not isinstance(key, str) or len(key) > 4096 or any(c.isspace() for c in key):
                    raise ValueError("Enter a Jev token without whitespace")
                self.jev_key = key or None
                return self.connection_info()
            if action == "doctor":
                try:
                    if self.settings.openai_backend == "chatgpt":
                        cfg = self.settings
                        status = CodexAdapter(cfg.models["sol"]["id"], cfg.codex_binary, cfg.codex_timeout_seconds).preflight()
                        ready = status.get("authenticated_with") == "ChatGPT"
                    else:
                        ready = bool(os.getenv("OPENAI_API_KEY"))
                        status = {"backend": "api", "credential_present": ready}
                except AdapterError as exc:
                    ready = False
                    status = {"error": str(exc)}
                prices = self.settings.models["jev"]
                jev_ready = self.connection_info()["jev_key_present"] and all(prices.get(k, "") != "" for k in ("input_per_million", "output_per_million", "cached_per_million"))
                return {"generation": status, "ready_for_live": ready and (jev_ready or self.settings.assessment_provider == "declared"),
                        "jev_ready": jev_ready, "note": "No model call made. Model access and remaining ChatGPT allowance are not verified."}
            store = Store(self.db)
            try:
                with store.lock():
                    pid = data.get("project_id")
                    if action == "create":
                        store.create_project(Project(data["id"], data["goal"], dollars(data.get("budget_usd", "1")), data.get("allow_external", False)))
                        return store.summary(data["id"])
                    project = store.project(pid)
                    if action == "configure":
                        project.budget_microusd = dollars(data["budget_usd"])
                        project.allow_external = data["allow_external"]
                        store.update_project(project)
                        return store.summary(pid)
                    if action == "import":
                        if "template" in data:
                            if data["template"] not in TEMPLATES:
                                raise ValueError("Unknown template")
                            workflow = json.loads((ROOT / TEMPLATES[data["template"]][2]).read_text())
                        else:
                            workflow = data["workflow"]
                        return import_tasks(store, pid, workflow)
                    if action == "edit-task":
                        task = store.task(pid, data["task_id"])
                        if task.status != "PENDING" or task.metadata.get("execution_mode") or store.attempts(pid, task.id):
                            raise ValueError("Only tasks that have never run can be edited. Add a new task for revised work.")
                        changes = data["changes"]
                        allowed = {"description", "inputs", "risk", "complexity", "sensitive", "requires_approval"}
                        if not isinstance(changes, dict) or set(changes) - allowed:
                            raise ValueError("Unsupported task changes")
                        updated = Task(**(task.to_dict() | changes | {"approved": False}))
                        store.save(updated)
                        with store.db:
                            store.event(pid, task.id, "task_edited", {})
                        return {"task": task.id, "status": updated.status}
                    if action == "approve":
                        return approve(store, pid, data["task_id"])
                    if action == "resume":
                        return resume(store, pid, data["task_id"])
                    if action == "reconcile":
                        if type(data["attempt_id"]) is not int or type(data.get("not_dispatched", False)) is not bool:
                            raise ValueError("Invalid reconciliation")
                        return reconcile(store, pid, data["attempt_id"], dollars(data["cost_usd"]), data["note"], data.get("not_dispatched", False))
                    if action == "run":
                        live = data.get("live", False)
                        if type(live) is not bool:
                            raise ValueError("Execution mode must be boolean")
                        self.job = {"project_id": pid, "status": "running", "mode": "live" if live else "mock", "error": None}
                        key = self.jev_key
                        worker = threading.Thread(target=self._run, args=(pid, live, key), daemon=True)
                        worker.start()
                        return dict(self.job)
                    raise ValueError("Unknown action")
            finally:
                store.close()

    def _run(self, project_id, live, key):
        store = None
        error = None
        try:
            # The importing connection's lock is released before the worker runs.
            # Serialize acquiring the store lock with the launching request.
            with self.mutex:
                pass
            store = Store(self.db)
            cfg = self.settings
            adapters = {}
            if live:
                transport = JsonTransport(cfg.timeout_seconds)
                adapters = {"jev": JevAdapter(cfg.models["jev"]["id"], transport, key),
                            "jev_decision": JevDecisionAdapter(cfg.models["jev"]["id"], transport, key)}
            Engine(store, cfg, live=live, context_root=self.context_root, **adapters).run(project_id)
        except (ValueError, AdapterError) as exc:
            error = str(exc)
        except Exception:
            error = "The run stopped unexpectedly. Inspect task details and reconcile any uncertain request before retrying."
        finally:
            if store:
                store.close()
            with self.mutex:
                self.job.update(status="failed" if error else "finished", error=error)


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self):
        # HTTPServer's reverse DNS lookup is unnecessary for a loopback-only app.
        TCPServer.server_bind(self)
        self.server_name = "127.0.0.1"
        self.server_port = self.server_address[1]

    def __init__(self, address, app):
        self.app = app
        super().__init__(address, Handler)
        self.origin = f"http://127.0.0.1:{self.server_port}"


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, *_):
        pass  # No prompts, tokens or URLs in access logs.

    def reply(self, status, data, content_type="application/json; charset=utf-8"):
        body = json.dumps(data, allow_nan=False).encode() if content_type.startswith("application/json") else data
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(body)

    def allowed(self, mutation=False):
        expected = urlsplit(self.server.origin).netloc
        if self.headers.get("Host") != expected or self.headers.get("Origin", self.server.origin) != self.server.origin:
            return False
        if self.headers.get("Sec-Fetch-Site", "none") not in ("same-origin", "none"):
            return False
        return not mutation or secrets.compare_digest(self.headers.get("X-Orchestrator-Token", ""), self.server.app.token)

    def do_GET(self):
        if not self.allowed():
            return self.reply(403, {"error": "Use the local dashboard URL"})
        path = urlsplit(self.path).path
        try:
            if path == "/api/session":
                return self.reply(200, {"token": self.server.app.token})
            if path == "/api/state":
                return self.reply(200, self.server.app.snapshot())
            if path.startswith("/api/projects/"):
                return self.reply(200, self.server.app.detail(path.removeprefix("/api/projects/")))
            assets = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"), "/style.css": ("style.css", "text/css; charset=utf-8"), "/favicon.svg": ("favicon.svg", "image/svg+xml")}
            if path in assets:
                name, kind = assets[path]
                return self.reply(200, (ASSETS / name).read_bytes(), kind)
            return self.reply(404, {"error": "Not found"})
        except (ValueError, OSError, sqlite3.Error):
            return self.reply(400, {"error": "Could not load project data. Check the project ID and database availability."})

    def do_POST(self):
        if not self.allowed(mutation=True):
            return self.reply(403, {"error": "Refresh the local dashboard and try again"})
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            return self.reply(415, {"error": "JSON request required"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_BODY:
                return self.reply(413, {"error": "Request must be under 1 MB"})
            data = json.loads(self.rfile.read(length), parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Invalid number")))
            if not isinstance(data, dict):
                raise ValueError("Request must be an object")
            path = urlsplit(self.path).path
            if not path.startswith("/api/actions/"):
                return self.reply(404, {"error": "Not found"})
            if path == "/api/actions/shutdown":
                with self.server.app.mutex:
                    if self.server.app.job and self.server.app.job["status"] == "running":
                        raise ValueError("Wait for the current workflow to finish before stopping the app")
                    self.server.app.job = {"status": "stopping"}
                self.reply(200, {"stopped": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            result = self.server.app.mutate(path.removeprefix("/api/actions/"), data)
            return self.reply(202 if path.endswith("/run") else 200, result)
        except (KeyError, TypeError, ValueError, UnicodeError) as exc:
            # Credential requests never echo untrusted input back to the browser.
            message = str(exc) if isinstance(exc, ValueError) else "Missing or invalid form fields"
            return self.reply(400, {"error": message})
        except sqlite3.IntegrityError:
            return self.reply(409, {"error": "That project or task ID already exists"})
        except (OSError, sqlite3.Error):
            return self.reply(409, {"error": "Local data is unavailable or in use. Try again when the current run finishes."})


def main(argv=None):
    parser = argparse.ArgumentParser(description="Open the local AI Orchestrator dashboard")
    parser.add_argument("--db", default=str(ROOT / ".orchestrator/state.sqlite3"))
    parser.add_argument("--config", default=str(ROOT / "config/settings.toml") if (ROOT / "config/settings.toml").is_file() else None)
    parser.add_argument("--context-root", default=str(ROOT))
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args(argv)
    try:
        app = Application(args.db, Settings.load(args.config), args.context_root)
        # Initialize the DB before opening the browser.
        app.snapshot()
        server = Server(("127.0.0.1", args.port), app)
    except OSError as exc:
        if args.open and args.port:
            try:
                with urlopen(f"http://127.0.0.1:{args.port}/api/state", timeout=2) as response:
                    existing = json.load(response)
                if existing["connections"]["context_root"] == app.context_root:
                    webbrowser.open(f"http://127.0.0.1:{args.port}")
                    return
            except (OSError, ValueError, KeyError):
                pass
        parser.exit(2, f"Cannot start dashboard: {exc}\n")
    except (ValueError, sqlite3.Error) as exc:
        parser.exit(2, f"Cannot start dashboard: {exc}\n")
    print(f"Dashboard: {server.origin}\nData: {args.db}\n", flush=True)
    if args.open:
        webbrowser.open(server.origin)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
