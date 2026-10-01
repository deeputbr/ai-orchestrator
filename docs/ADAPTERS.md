# Add a provider or deterministic tool

1. Register a pure deterministic operation in `core/local.py` when ordinary code can solve the task. Add input checks and domain tests. Do not register arbitrary shell/eval passthroughs.
2. For a model adapter, implement `prepare(state) -> JSON payload` and `invoke(payload) -> Reply`. Expose `external` and `model`. Inject the transport in tests. `Reply` contains structured output, validated `Usage`, actual provider model and latency.
3. Raise sanitized `AdapterError` categories. Preserve known token usage on rejected output. Mark uncertain billing outcomes with `uncertain=True`. Never hide a retry inside an adapter, because each billable invocation needs its own reservation and record.
4. Add explicit policy capabilities and validators. The router must reject unsupported capabilities. New endpoints require official documentation and a documented data boundary. Do not allow prompts to choose arbitrary endpoints or credentials.
5. Declare the billing basis explicitly. API adapters need prices, budget categories and a conservative preflight estimate. Subscription adapters need separate call/token accounting and must not infer USD cost or remaining allowance. Fixed-price/media providers will need a dedicated settlement implementation.
6. Test the wire contract with fixtures, malformed output, authentication/rate limits, timeouts, changed prompts, budget or call-cap exhaustion, restart and reconciliation. Perform a deliberately bounded live smoke test after authentication and account access are available; direct API tests also require verified prices.

For a new Jev-backed workflow judgment, use `task_type: "jev_decision"` rather than adding a provider. Put a bounded Choice/Score/Noul map in `inputs.questions`, or name a completed generation dependency with `inputs.questions_from_dependency`. The engine validates generated packs before dispatch and includes all declared dependency outputs in Jev state. Prefer static packs when the option set is known; use a Sol/Astra-authored pack only when the candidates or rubric must be discovered at runtime.

## Next adapters

- **Coding (Milestone 6):** deterministic repository search and selected file reads; structured patch proposals; isolated application; explicit compile/test/lint commands with time/output limits; failure evidence; approval before replacing important files. Current `coding_proposal.json` only returns a text proposal.
- **Higgsfield (7):** first inspect current official submission/polling/webhook/cost contracts. Persist external job IDs before polling; never resubmit while status is uncertain. Store media and adjacent metadata under `projects/<project>/assets/<scene>/`.
- **Blender (8):** detect installed executable/version, background subprocess with argument arrays (no shell), trusted Python templates, bounded logs and rendering validators. Compute cost must be labeled measured/estimated/unavailable.
- **Resolve (9):** inspect installed version and shipped scripting docs. Build a timeline manifest. Document unsupported operations before any GUI fallback.
- **Content/video (10):** only after the above are reliable, add source triage, synthesis, scene routing, asset generation, visual QA and timeline/render gates. Jev consumes structured scene/QA signals, never raw image judgments.

A native Work connector and authenticated API can wrap the same core later. No service is listening on a port in this release.
