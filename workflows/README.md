# Workflow definitions

`coding_proposal.json` and `content_draft.json` are runnable V1 examples. They produce structured text results and approval gates. They do not modify repositories or render video.

`video_blueprint.json` is **not importable**: it is the future workflow design with capability requirements and gates. Its separate `stages` format intentionally prevents accidental execution as a supported V1 workflow.

Import a runnable file with `python3 -m orchestrator add PROJECT FILE`. Task IDs are unique within a project; use new IDs or a new project for another job. Dependencies can refer to tasks in the same import or tasks already present. Runtime fields/assessment caches cannot be imported.

Allowed task types: the registered local operations, `generation`, `coding`, `research`, `content`. The generative types currently share the text-only result adapter. Supported capabilities: `text`, `reasoning`, `code_proposal`. Unsupported capabilities pause for human/tool integration.

Each result is available to dependent tasks. Select only necessary dependencies; their combined inputs and context must fit the configured byte limit. Add validators matching the actual objective; `contains` alone is not a quality review.
