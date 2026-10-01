# Personal AI Orchestrator

A reusable, local-first Python core that chooses deterministic code, Jev, GPT-6 Sol, GPT-6 Astra or human review, with persisted state, cost estimates and explicit approval gates.

**Current release:** Milestones 1–4 implemented and tested, plus the minimal dependency engine needed to exercise them. Live Sol execution through the ChatGPT-authenticated Codex runtime was verified on 2026-09-29 without an OpenAI API key. Jev and the optional direct OpenAI API backend remain fixture-tested but live-unverified. Full repository editing, creative applications and video production are later milestones.

## Start in under a minute

Requires Python 3.11+ on macOS/Linux. No runtime packages, API keys or network access needed:

```sh
cd /path/to/ai-orchestrator
python3 -m unittest discover -v
python3 -m orchestrator create demo --goal "Exercise local orchestration"
python3 -m orchestrator add demo examples/local.json
python3 -m orchestrator run demo
python3 -m orchestrator status demo
```

This really computes word counts and normalizes JSON locally. `run` uses mock providers by default. Generative mock results are explicitly marked `SIMULATED`; a completed mock task proves orchestration, not model output quality. Status reports execution modes.

For a generation/approval demonstration:

```sh
python3 -m orchestrator create writing --goal "Exercise a content workflow"
python3 -m orchestrator add writing workflows/content_draft.json
python3 -m orchestrator run writing
python3 -m orchestrator tasks writing
python3 -m orchestrator approve writing editorial-review
python3 -m orchestrator run writing
python3 -m orchestrator status writing
```

Optional installed command (packaging tools may need network on first install):

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/orchestrator --help
```

The no-install `python3 -m orchestrator` path is fully supported and was tested on Python 3.13.3/macOS.

## What works

- Project, job and task identities; validated dependency graphs and extensible task metadata.
- SQLite persistence, JSON event history, compact project summaries and sequential execution.
- Deterministic `word_count`, `json_normalize`, and `sha256` operations.
- Jev routing assessments and reusable `jev_decision` tasks through the official TypeSafe endpoint, with batched Choice, Score and Noul questions, strict response checking and mock adapters.
- Sol/Astra structured generation through the ChatGPT-authenticated Codex runtime by default, with separate subscription-call accounting.
- An explicit opt-in OpenAI Responses API backend with refusal/incomplete detection and cached-token cost accounting.
- Configurable routing, evidence-based repair, bounded escalation and human review.
- Project/call/Astra budgets, persisted reservations, request-specific cost approvals and uncertain-charge reconciliation.
- Explicit context files, byte limits, secret-like path checks, data-sharing consent and sensitive dependency protection.
- Human-readable JSON status, per-tier cost totals, attempt history and replay metadata.

No model-generated commands are executed. Generated code is returned as a proposal. Compile/test/lint validators and automatic repository changes are not yet implemented.

## Configuration and live execution

`config/settings.toml` is the primary hybrid configuration. Jev performs bounded classifications and judgments; Sol/Astra generate and reason through the current ChatGPT-authenticated Codex runtime. Pass it explicitly:

```sh
python3 -m orchestrator --config config/settings.toml status demo
```

Without `--config`, built-in defaults are used; there is no implicit configuration search. TOML replaces YAML so Python can read settings without another dependency.

### Jev plus ChatGPT subscription execution

Export your Jev token, then check both sides of the hybrid setup. `doctor` verifies ChatGPT authentication and Jev key presence without making a model call:

```sh
export JEV_API_KEY="your-token"
python3 -m orchestrator --config config/settings.toml doctor
python3 -m orchestrator create hybrid-demo --goal "Verify Jev plus included-usage execution" --allow-external --budget-microusd 10000
python3 -m orchestrator add hybrid-demo examples/hybrid-smoke.json
python3 -m orchestrator --config config/settings.toml run hybrid-demo --live
python3 -m orchestrator status hybrid-demo
```

For a composition example where Sol creates candidates and Jev selects and scores them, import `workflows/jev_choice.json`. The second task is a `jev_decision` containing three atomic questions evaluated together. Its Jev state includes the upstream Sol output:

```sh
python3 -m orchestrator create strategy-demo --goal "Generate, then choose efficiently" --allow-external --budget-microusd 10000
python3 -m orchestrator add strategy-demo workflows/jev_choice.json
python3 -m orchestrator --config config/settings.toml run strategy-demo --live
```

Use static question packs when the decision dimensions and options are known; this is the cheapest path. When they depend on generated candidates, `workflows/jev_dynamic_questions.json` shows Sol producing a TypeSafe question pack and a later `jev_decision` loading it through `questions_from_dependency`. The runtime parses and validates question IDs, types, option counts, score levels and request size before sending anything to Jev. Jev returns typed probabilities and confidence; deterministic code records the answers and controls subsequent branching. Jev does not generate prose or code.

The bundled Jev rate is the official Jev 1.13 price checked on 2026-09-29: $0.042 per million input tokens, with free output tokens. Verify it when upgrading or pinning another model. The application accepts `JEV_API_KEY` or `TYPESAFE_API_KEY` and never automatically sources `.env` files.

Sol/Astra subscription calls are recorded separately. Their local `usd_cost` and remaining account allowance stay `null`: usage is shared with ChatGPT/Codex, while the runtime does not expose a reliable remaining percentage. Local call caps limit this project; they do not measure the account allowance.

### Optional direct API billing

To bill Sol/Astra through the OpenAI API, explicitly set `openai_backend = "api"`, export `OPENAI_API_KEY`, and configure current per-million-token rates. All money is in USD; 1,000,000 micro-USD = $1. Empty prices mean **unconfigured**, never free. Rates and computed charges are estimates, not provider invoices.

For every live mode, create a fresh project with `--allow-external`, run with `--live`, and inspect `tasks` if execution pauses. Mock and live attempts cannot be mixed in a task, and live generation cannot consume simulated generative dependency output. Use new task IDs for a real run.

A project sharing setting covers task descriptions, selected input/context and dependency outputs. A task marked `sensitive` and all its descendants stay local. Secret-like path checks are a guardrail, not a PII detector: review selected files before enabling external sharing.

## CLI and Python API

Global `--db PATH` and `--config PATH` precede the command. Default database: `.orchestrator/state.sqlite3` under your current directory.

| Command | Purpose |
| --- | --- |
| `doctor` | Verify ChatGPT authentication, or API-key presence for the selected backend, without a model call |
| `create PROJECT --goal TEXT` | Create project; external sharing disabled by default |
| `configure-project PROJECT --budget-microusd N --external allow\|deny` | Explicitly revise budget/consent |
| `add PROJECT workflow.json` | Atomically import new task definitions; cycles and runtime fields rejected |
| `run PROJECT [--live] [--context-root PATH]` | Execute ready tasks until complete or gated |
| `status PROJECT` | Counts, jobs, blockers, modes, tier spend and budget remaining |
| `tasks PROJECT` | Task metadata, output, routing, validation and pending approval request |
| `attempts PROJECT` | Local prompts, request parameters, usage, output, failures and price snapshots |
| `events PROJECT` | Compact structured audit events |
| `approve PROJECT TASK` | Approve an explicit approval gate only |
| `resume PROJECT TASK` | Recheck a paused/failed task after fixing the cause |
| `reconcile PROJECT ATTEMPT --cost-microusd N --note TEXT` | Record verified API billing for an unknown outcome; never replay automatically |
| `reconcile PROJECT ATTEMPT --not-dispatched --note TEXT` | Close an attempt proven not to have reached the provider |
| `export PROJECT FILE` | Write a new compact project snapshot; refuses overwrite |

A paused run returns exit code 0 because scheduling completed normally. Automation must inspect `statuses`/`blockers`. Invalid input/configuration at the CLI boundary returns code 2. Per-task provider/configuration failures are persisted as gates.

```python
from orchestrator.core.models import Project, Task
from orchestrator.core.state import Store
from orchestrator.core.engine import Engine

store = Store(".orchestrator/state.sqlite3")
try:
    with store.lock():
        store.create_project(Project("api-demo", "Count words"))
        store.add_tasks("api-demo", [Task("count", "api-demo", "Count text",
            task_type="word_count", inputs={"text": "small useful increments"})])
    print(Engine(store).run("api-demo"))
finally:
    store.close()
```

All Python API mutations must acquire `store.lock()`; `Engine.run()` acquires it itself. The CLI does this automatically. Direct database edits are unsupported. Back up the SQLite database using SQLite's backup API or after closing all processes (including WAL files if present).

## Routing and validation

Registered pure local operation → local execution. A `jev_decision` task goes directly to Jev with its validated typed questions. Other non-local tasks first ask Jev ten independent routing questions in one call. Deterministic policy combines those answers with task metadata; routine work goes to Sol. Enabled high-risk/architecture/security/irreversibility/very-high-complexity triggers go to Astra. Two failed Sol generations escalate to Astra by default; one failed Astra attempt pauses for human review. These counts are tuning parameters.

Low confidence, insufficient context, ambiguous consequential signals, unsupported capabilities, or unimplemented deterministic work pause for clarification. Assessment cannot authorize code execution or external side effects. The current generation adapters are text-only; visual interpretation pauses instead of pretending to review media.

Supported validators: `contains`, `equals`, `json`, `min_length`, plus mandatory generated-result structure. These establish only those explicit checks. They do not establish factual correctness, source verification, compilation, or visual quality. Add real domain validators before treating outputs as production-ready.

Retries include validation/error evidence and prior output, within the context limit. Duplicate request fingerprints are not replayed. HTTP/auth/rate-limit errors pause; this version does not silently retry network calls. Timeouts, server errors and unexpected post-dispatch exceptions keep a reservation and require reconciliation. Reconciliation updates billing only: inspect saved results and create a revised task if another call is needed.

## Cost and approval behavior

Status tracks Jev, Sol, Astra, Higgsfield, other APIs and Blender separately; unimplemented categories remain zero. Jev and direct OpenAI API call history includes provider model/version, token counts, latency, mode and pricing snapshots. Cached tokens get the configured cached rate. The request estimate assumes no cache discount and includes an input safety margin; it is a conservative planning envelope, **not a guaranteed provider spending cap**. Provider-side limits are needed for a hard external ceiling.

ChatGPT-authenticated calls are tracked in `subscription_usage`, separate from USD estimates. The CLI identifies the requested model because the Codex event stream does not report an independently verified serving model. Known token usage is recorded, but subscription consumption is never converted into dollars or an invented remaining percentage.

Budget checks include both recorded charges and unresolved reservations. A required expensive tier is never silently downgraded to fit the budget. A changed request requires fresh cost approval. An actual estimate above the reservation pauses further work.

## Use from ChatGPT Work / Codex

Open this repository in a ChatGPT Work/Codex local project. The Work window can inspect `status`, import a workflow, run the CLI, read outputs and summarize blockers through its local execution tools. For Sol/Astra, the default live backend reuses the installed Codex runtime's ChatGPT sign-in, so it consumes the shared ChatGPT/Codex allowance and does not need `OPENAI_API_KEY`. The orchestrator never reads or copies OAuth credentials and has no API fallback.

This release does not register a Work plugin/MCP server, expose an HTTP server, or control the Work UI. Jev requires the token you provide; Sol/Astra do not require a separate OpenAI API key in the primary profile.

## Repository map

- `orchestrator/core`: records, state, configuration, routing, costs, context, validators, engine and human actions.
- `orchestrator/adapters`: ChatGPT/Codex, direct OpenAI API, Jev, injectable transport and explicit offline mocks.
- `orchestrator/cli`: command interface.
- `workflows`: runnable text, coding-proposal and Sol→Jev decision samples, plus a clearly marked future video blueprint.
- `tests`: offline unit, provider contract, engine, transport and CLI tests.
- `docs`: plan, architecture decisions, adapter extension guide, documentation sources and implementation status.

See [architecture](docs/ARCHITECTURE.md), [adding adapters](docs/ADAPTERS.md), [milestone status](docs/STATUS.md), and [verified source references](docs/SOURCES.md).
