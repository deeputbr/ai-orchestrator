# ADR 001 — Local, explicit, single-runner core

Accepted 2026-09-28. This is a personal orchestration service, so use Python 3.11+, stdlib dataclasses/TOML/HTTP/unittest, SQLite and a CLI/Python API. Avoid installation dependencies for the offline path. JSON documents preserve extensible task metadata; SQL tables enforce identities, task ownership and attempt history. Store USD in integer millionths to avoid floating-point budget drift.

Configuration defines policies and model aliases. Only registered deterministic operations are executable; natural-language claims of determinism do not authorize arbitrary shell commands. Provider transport is injected for tests. Network execution is explicit, projects default to no external sharing, and prices must be supplied before live calls. Unknown billing outcomes reserve the full estimate and require reconciliation rather than automatic replay.

A process lock serializes execution, budget reservations and CLI mutations per database. Interrupted attempts require human reconciliation. This trades concurrency for understandable personal-use semantics. Future multi-worker execution would need leases and transactional claims, not removal of this lock.

CLI JSON output is the initial local control surface for Work/Codex. No undocumented mechanism for remote-controlling ChatGPT Work or using subscription sessions as API credentials is assumed.
