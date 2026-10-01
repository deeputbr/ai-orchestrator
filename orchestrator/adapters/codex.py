"""Subscription execution via the official CLI; never imports or copies credentials."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time
from orchestrator.adapters.base import AdapterError, Reply
from orchestrator.adapters.openai import RESULT_SCHEMA, SYSTEM, validate_result
from orchestrator.core.models import Usage

BUNDLED = (
    "/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex",
    "/Applications/Codex.app/Contents/Resources/codex",
)
REQUIRED = ("--json", "--output-schema", "--ephemeral", "--ignore-user-config", "--sandbox")
ENV_ALLOW = {"HOME", "USER", "LOGNAME", "PATH", "TMPDIR", "LANG", "LC_ALL", "CODEX_HOME", "USERPROFILE", "SystemRoot"}
# Verified against the bundled CLI's feature list. Unknown settings fail via strict-config.
DISABLED_FEATURES = ("shell_tool", "unified_exec", "shell_snapshot", "multi_agent", "multi_agent_v2",
                     "apps", "plugins", "remote_plugin", "hooks", "memories", "browser_use",
                     "computer_use", "image_generation", "skill_search", "skill_mcp_dependency_install",
                     "code_mode", "goals", "unbounded_connection_retries")

IGNORABLE_WARNINGS = (
    "Under-development features enabled:",
    "Code Mode is unavailable because code-mode host is disabled.",
)


def child_environment(source=None):
    source = os.environ if source is None else source
    return {k: v for k, v in source.items() if k in ENV_ALLOW}


def bounded_process(args, *, input="", cwd=None, env=None, timeout=15, max_bytes=2_000_000):
    """No shell; bounded disk-backed output and process-group cleanup on every exit."""
    with tempfile.TemporaryFile() as prompt, tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        prompt.write(input.encode())
        prompt.seek(0)
        process = subprocess.Popen(args, stdin=prompt, stdout=out, stderr=err, cwd=cwd, env=env, start_new_session=True)
        deadline = time.monotonic() + timeout
        try:
            while process.poll() is None:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Codex deadline exceeded")
                if os.fstat(out.fileno()).st_size + os.fstat(err.fileno()).st_size > max_bytes:
                    raise OverflowError("Codex output limit exceeded")
                time.sleep(0.05)
            if os.fstat(out.fileno()).st_size + os.fstat(err.fileno()).st_size > max_bytes:
                raise OverflowError("Codex output limit exceeded")
            out.seek(0)
            err.seek(0)
            return subprocess.CompletedProcess(args, process.returncode, out.read().decode("utf-8", errors="replace"), err.read().decode("utf-8", errors="replace"))
        finally:
            # Also stop descendants if the parent exited while a child still holds resources.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()


def classify_error(text):
    value = text.lower()
    if any(x in value for x in ("usage limit", "rate limit", "rate_limit", "quota", "credits")):
        return "subscription_limit_reached"
    if any(x in value for x in ("not supported", "model_not_found", "model is not", "model not", "access to model")):
        return "subscription_model_unavailable"
    if any(x in value for x in ("unauthorized", "not logged in", "authentication", "401")):
        return "chatgpt_authentication_required"
    return "codex_execution_failed"


def parse_events(text, requested_model, elapsed_ms, returncode=0, error_text=""):
    usage = None
    thread_id = None
    final = None
    completed = 0
    failure = None
    tool_used = False
    item_types = []
    warnings = []
    try:
        for line in text.splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            kind = event["type"]
            if kind == "thread.started":
                thread_id = event.get("thread_id")
            elif kind == "turn.completed":
                u = event["usage"]
                usage = Usage(u["input_tokens"], u["output_tokens"], u.get("cached_input_tokens", 0))
                completed += 1
            elif kind == "turn.failed":
                failure = classify_error(json.dumps(event.get("error", {})))
            elif kind == "error":
                failure = classify_error(event.get("message", ""))
            elif kind == "item.completed":
                item = event["item"]
                item_types.append(item["type"])
                if item["type"] == "agent_message":
                    final = item["text"]
                elif item["type"] == "error" and any(
                    item.get("message", "").startswith(prefix) for prefix in IGNORABLE_WARNINGS
                ):
                    warnings.append(item["message"])
                elif item["type"] not in ("reasoning", "plan"):
                    tool_used = True
    except (ValueError, TypeError, KeyError):
        raise AdapterError("invalid_codex_events", uncertain=True, usage=usage) from None
    if returncode and not text.strip() and "Error loading config.toml:" in error_text:
        raise AdapterError("codex_configuration_error", not_dispatched=True)
    if returncode and not text.strip() and "failed to initialize in-process app-server client" in error_text:
        raise AdapterError("codex_runtime_initialization_blocked", not_dispatched=True)
    if returncode or failure:
        category = failure or classify_error(error_text)
        raise AdapterError(category, uncertain=usage is None, usage=usage)
    if tool_used:
        raise AdapterError("unexpected_codex_item", uncertain=usage is None, usage=usage, metadata={"item_types": item_types, "thread_id": thread_id})
    if completed != 1 or usage is None:
        raise AdapterError("codex_completion_unverified", uncertain=True, usage=usage)
    try:
        output = json.loads(final)
        if not validate_result(output):
            raise ValueError("Invalid result")
    except (ValueError, TypeError):
        raise AdapterError("invalid_structured_output", usage=usage) from None
    return Reply(output, usage, requested_model, elapsed_ms, thread_id,
                 {"billing": "chatgpt_subscription", "model_source": "requested_cli_model",
                  "remaining_allowance": None, "usd_cost": None, "runtime_warnings": warnings})


class CodexAdapter:
    external = True
    billing = "chatgpt_subscription"

    def __init__(self, model, binary="auto", timeout=180, runner=None):
        self.model = model
        self.binary = binary
        self.timeout = timeout
        self.runner = runner or bounded_process
        self.resolved = None

    def _run(self, args, **kwargs):
        return self.runner(args, env=child_environment(), **kwargs)

    def preflight(self):
        if self.resolved is None:
            candidates = [self.binary] if self.binary != "auto" else [*BUNDLED, shutil.which("codex")]
            for binary in candidates:
                if not binary or not Path(binary).is_file():
                    continue
                try:
                    result = self._run([binary, "exec", "--help"], timeout=10)
                except (OSError, TimeoutError, OverflowError):
                    continue
                if result.returncode == 0 and all(flag in result.stdout for flag in REQUIRED):
                    self.resolved = str(Path(binary).resolve())
                    break
            if self.resolved is None:
                raise AdapterError("current_codex_runtime_required")
        try:
            # Inspect auth before enforcing it; do not trigger a login or logout on mismatched auth.
            status = self._run([self.resolved, "login", "status"], timeout=10)
        except (OSError, TimeoutError, OverflowError):
            raise AdapterError("codex_auth_check_failed") from None
        lines = (status.stdout + "\n" + status.stderr).splitlines()
        if status.returncode or not any(line.strip() == "Logged in using ChatGPT" for line in lines):
            raise AdapterError("chatgpt_login_required_no_api_fallback")
        return {"backend": self.billing, "binary": self.resolved, "authenticated_with": "ChatGPT",
                "remaining_allowance": None, "model_access": "not_probed"}

    def prepare(self, state):
        return {"backend": "chatgpt_codex", "invocation_policy_version": 3, "model": self.model, "instructions": SYSTEM,
                "state": state, "schema": RESULT_SCHEMA, "reasoning_effort": "low",
                "timeout_seconds": self.timeout, "output_token_cap": "not_exposed_by_cli"}

    def invoke(self, payload):
        self.preflight()
        started = time.monotonic()
        try:
            with tempfile.TemporaryDirectory(prefix="orchestrator-codex-") as temp:
                schema = Path(temp) / "result.schema.json"
                schema.write_text(json.dumps(payload["schema"]))
                args = [self.resolved, "exec", "--ignore-user-config", "--strict-config", "--ephemeral",
                        "--skip-git-repo-check", "--sandbox", "read-only", "--json", "--color", "never",
                        "--model", self.model, "--output-schema", str(schema), "--cd", temp]
                overrides = ['forced_login_method="chatgpt"', 'model_provider="openai"',
                             'approval_policy="never"', 'web_search="disabled"', 'project_doc_max_bytes=0',
                             'model_reasoning_effort="low"', 'history.persistence="none"',
                             'suppress_unstable_features_warning=true',
                             'features.view_image=false',
                             'features.skip_host_skill_discovery=true']
                overrides.extend(f"features.{name}=false" for name in DISABLED_FEATURES)
                for value in overrides:
                    args.extend(["-c", value])
                args.append("-")
                prompt = payload["instructions"] + "\nUse only the supplied data. Do not call tools, read files, or change files. Keep the result concise.\n" + json.dumps(payload["state"], ensure_ascii=False)
                result = self._run(args, input=prompt, cwd=temp, timeout=self.timeout)
        except (TimeoutError, OverflowError):
            raise AdapterError("codex_outcome_unknown", uncertain=True) from None
        except OSError:
            raise AdapterError("codex_launch_failed") from None
        return parse_events(result.stdout, self.model, int((time.monotonic() - started) * 1000), result.returncode, result.stderr)
