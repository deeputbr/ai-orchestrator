# Provider contract sources

Checked 2026-09-29; implementation uses official sources, not community proxy endpoints.

- TypeSafe [Quick start](https://docs.typesafe.ai/introduction/quickstart): `POST https://api.typesafe.ai/v1/systemone`, `model`, `state`, named questions, typed answers, `usage.input_tokens` and `usage.output_tokens`.
- TypeSafe [API reference](https://docs.typesafe.ai/api): Choice, Score and Noul request/response contracts, structured state, criteria limits and typed probability/confidence fields. The adapter implements a conservative string-only authoring subset and validates every response.
- TypeSafe [Models](https://docs.typesafe.ai/models): Jev 1.13 pricing checked 2026-09-29 is $0.042 per million input tokens with free output tokens; `jev-latest` currently resolves to `jev-1.13.0`.
- TypeSafe [Patterns](https://docs.typesafe.ai/patterns): independent atomic questions, speculative fan-out, confidence-gated routing and deterministic composition motivate the hybrid workflow design.
- OpenAI [Structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs): Responses `text.format` with strict JSON Schema; responses may refuse or be incomplete, so downstream validation remains necessary.
- OpenAI [Codex authentication](https://developers.openai.com/codex/auth): Codex supports ChatGPT sign-in for subscription access and API-key sign-in for usage-based API billing. The adapter requires the ChatGPT method and removes API credentials from its child environment.
- OpenAI [Codex non-interactive mode](https://developers.openai.com/codex/noninteractive): `codex exec` supports JSONL output and structured output schemas for automation. The adapter uses both and validates the resulting event stream.
- OpenAI [Codex pricing](https://developers.openai.com/codex/pricing): Codex use is included with eligible ChatGPT plans, subject to shared limits. Local token observations are therefore kept separate from API USD accounting.
- OpenAI [GPT-6 Sol](https://developers.openai.com/api/docs/models/gpt-6-sol) and [GPT-6 Astra](https://developers.openai.com/api/docs/models/gpt-6-astra): preserve the explicitly requested model IDs. Sol access was live-verified through ChatGPT sign-in; Astra and direct API access were not.
- OpenAI [Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching): keep static prompt content stable and record cached input usage. This release sends a stable instruction/schema prefix with compact task state; it makes no cache-hit guarantee and does not request explicit paid cache writes.

Direct API prices are deliberately unconfigured. They must be verified for the account and service tier before direct API calls. Provider responses are tested through injectable fixtures; the separate subscription smoke test verifies only the installed ChatGPT/Codex Sol path.
