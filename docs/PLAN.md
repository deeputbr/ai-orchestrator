# Implementation plan

1. **Bootstrap:** typed project/task/job records, validated TOML settings, SQLite state and event history, integer micro-USD accounting, CLI, deterministic operations and persistence tests.
2. **Jev:** injectable transport, official System One request format, routing assessments and reusable workflow decisions with separate atomic Choice/Score/Noul questions, strict typed answers, confidence, usage and safe error handling; offline fixtures and contract tests.
3. **OpenAI:** interchangeable Sol/Astra targets through ChatGPT-authenticated Codex execution by default or an explicit Responses API backend; bounded structured output, separate subscription/API accounting, latency, refusal/incomplete handling and transport tests.
4. **Routing:** externally configured thresholds and escalation triggers; registered deterministic operations first; Jev signals combined by code; default Sol; justified Astra; uncertain/missing evidence to human.

A small dependency scheduler, persisted attempts, approval/resume and budget enforcement will connect the four increments into a usable demonstration. Full coding automation and creative adapters remain separate later milestones. No GUI automation or distributed infrastructure in this increment.

Acceptance: offline CLI demo survives reopening the database; deterministic work never calls models; invalid provider output fails closed; Sol failure evidence reaches repair; repeated failure selects Astra; budget/privacy/approval gates prevent calls; ledger retains failed-call usage. Test each milestone before proceeding.
