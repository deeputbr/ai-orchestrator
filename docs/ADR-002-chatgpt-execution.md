# ADR 002 — ChatGPT-authenticated execution

Accepted 2026-09-28 following the user's preference to use included application limits.

Keep offline/mock execution as the default invocation mode. For live Sol/Astra work, default to the official Codex CLI signed in with ChatGPT; the direct OpenAI API adapter requires explicit `openai_backend = "api"`. No fallback exists between billing methods. Jev remains the primary routing and bounded-decision API with its own user-supplied token.

Use a current installed CLI, preferring the desktop-bundled runtime on macOS when it meets required capabilities. Probe help and login status without a model call. Never read/copy OAuth tokens or change shared login state. Strip API keys and provider overrides from child environments, require ChatGPT login, force OpenAI/ChatGPT authentication, ignore user execution customization and run with restricted tools in a temporary read-only working directory. Preserve administrator restrictions.

Record subscription calls, known token counts and uncertain outcomes separately from API USD estimates. Do not convert token counts into subscription percentages or claim zero consumption. Cap project subscription invocations and Astra invocations; shared remaining allowance is unavailable to this adapter and stays null. Rate-limit/auth/model-access errors pause without API fallback. CLI output is bounded, deadlines kill the process group, and ambiguous dispatch outcomes are not replayed.

The CLI may add its own instructions and protocol context and does not expose the Responses max-output-token parameter here. The adapter records that limitation; timeout/call limits are controls, not a hard token or credit ceiling. A bounded Sol smoke test passed on 2026-09-29 through ChatGPT sign-in, returning the required structured result. Astra access remains unverified.
