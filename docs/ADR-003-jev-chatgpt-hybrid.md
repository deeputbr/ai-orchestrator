# ADR 003 — Jev and ChatGPT hybrid decision architecture

Accepted 2026-09-29 to preserve Jev's intended role while using included ChatGPT capacity for generation.

The primary live profile uses deterministic code first, Jev for typed judgments, and the ChatGPT-authenticated Codex runtime for Sol/Astra generation. Generative tasks receive one batched Jev routing assessment before deterministic policy selects Sol, Astra, or human review. Workflow authors can also add `jev_decision` tasks containing bounded Choice, Score, and Noul questions. These tasks receive explicit state, selected context, and dependency outputs, so Sol/Astra can generate candidates and Jev can choose, classify, or score them afterward.

Question packs are workflow data, not executable instructions. Prefer static packs when dimensions are known. When candidate-dependent questions are needed, an upstream Sol/Astra task can return the pack as JSON and `questions_from_dependency` loads it for a later `jev_decision`. The engine validates types, identifiers, criteria counts, score levels and total size before dispatch. Jev's typed answers are persisted with probabilities, confidence, model version, usage, price snapshot and cost. Deterministic code remains responsible for routing and actions; Jev does not generate prose or authorize side effects.

The default configuration uses the official Jev 1.13 rate verified on 2026-09-29: $0.042 per million input tokens and zero output-token cost. Model aliases and pricing can change, so configuration and source notes must be reviewed when upgrading. Sol/Astra usage remains separate subscription accounting with no invented USD value or remaining percentage.
