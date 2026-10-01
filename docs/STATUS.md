# Implementation checkpoint — 2026-09-29

| Milestone | Status | Evidence / remaining work |
| --- | --- | --- |
| 1 Bootstrap | Implemented | Python package, CLI, TOML, typed records, SQLite, local operations, tests |
| 2 Jev | Implemented; live unverified | Routing plus static or Sol-authored Choice/Score/Noul workflow tasks, strict request/response validation, mock, error, usage and cost tests |
| 3 OpenAI | Implemented; ChatGPT/Sol live-verified | ChatGPT-authenticated Codex adapter plus optional Responses API adapter; Astra and direct API remain live-unverified |
| 4 Routing | Implemented | Configurable thresholds/triggers, deterministic-first, Sol repair, Astra and human escalation tests |
| 5 Workflow engine | Minimal working increment | Sequential dependencies, persistence, cost reservations, approval/resume and crash recovery; no parallel workers |
| 6 Coding workflow | Proposal sample only | Full repository inspection/edit/test/repair remains to build |
| 7 Higgsfield | Not started | Requires current docs and account capability review |
| 8 Blender | Not started | Requires executable/version inspection |
| 9 Resolve | Not started | Requires installed version and scripting support inspection |
| 10 End-to-end video | Blueprint only | Deliberately deferred until adapters work |

Two bounded Sol invocations reached the ChatGPT-authenticated Codex runtime while integrating that backend on 2026-09-29. The first returned content plus runtime warning events and was deliberately rejected; the corrected run returned the expected structured result with 8,946 input and 21 output tokens. Combined known usage was 17,838 input and 42 output tokens. That backend-only smoke test used no `OPENAI_API_KEY` or Jev call; consumption belongs to the account's shared ChatGPT/Codex allowance and has no local USD value. Live hybrid verification is waiting for the user-supplied Jev token. Other test usage/cost values are synthetic.

Validation: see `TEST_RESULTS.txt` for the final suite and `DEMO_STATUS.json` for a real local-operation run. CLI lifecycle, dependency scheduling, approval persistence, Sol repair/Astra escalation, privacy inheritance, budget gates, unknown billing, context containment and provider parsing are exercised offline.

Next meaningful checkpoint: after the user adds a Jev token, run one bounded hybrid smoke test that performs Jev routing followed by Sol through ChatGPT. Then verify the Sol→Jev composition workflow. Before automatic software changes, implement isolated patch application and real compiler/test/lint validators (Milestone 6).
