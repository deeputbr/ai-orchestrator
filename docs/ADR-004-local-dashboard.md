# ADR 004: Local browser dashboard

Status: implemented, 2026-10-01.

## Decision

Provide a dependency-free browser interface using Python's `ThreadingHTTPServer`, bundled HTML/CSS/JavaScript, and the existing core API. A native universal macOS app bundle opens the server status window and dashboard without requiring typed commands; alternate macOS and Linux launchers are included. The server binds exclusively to IPv4 loopback.

Each request owns its SQLite connection. A workflow runs in a background thread, with a process mutex admitting one run and refusing mutations while it is active. The existing file lock also excludes other CLI processes. Read requests remain available during execution for polling. There is no run cancellation button because interrupting dispatched requests requires reconciliation.

Workflow import validation is shared with the CLI. Human actions call the existing approval, resume, and reconciliation functions. A pre-execution edit can change only descriptive/input/risk/approval fields; task identity and dependency graph stay fixed and executed history cannot be rewritten.

The server validates the Host, Origin, browser fetch context, and a process-random token for mutations, limits JSON request bodies, sends a same-origin content security policy, and serves only explicitly listed assets. User/provider text is escaped before rendering. It does not enable CORS or log requests.

Jev credentials entered in the browser stay in process memory. The runner passes the key directly to both Jev adapters, avoiding global environment mutation and keeping it out of the Codex child environment. Clearing the session key returns to environment-based credentials. No account credentials are copied and no live calls are made by the setup check.

## Validation and scope

HTTP integration tests cover local execution, approval lifecycle, import rejection, privacy gates, mutation origin/token checks, busy-run exclusion, ephemeral credentials, edits, project configuration, and uncertain-outcome reconciliation. Browser checks cover forms, simulation, approval/continuation, custom local tasks, Jev option entry, downloads, and mobile layout.

The dashboard exposes current capabilities. Coding execution, creative applications, and video production remain later milestones. This is a single-user local app, not a hosted multi-user service. Python 3.11+ and the installed ChatGPT/Codex runtime are prerequisites for the primary live profile.
