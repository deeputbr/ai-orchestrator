# Dashboard validation — 2026-10-01

Release: 0.4.0.

- Full Python suite: **81 tests passed** (70 existing tests plus 11 HTTP integration tests).
- JavaScript syntax: `node --check orchestrator/web/static/app.js` passed.
- Whitespace validation: `git diff --check` passed.
- Universal macOS launcher built with Clang for arm64 and x86_64; Finder/Launch Services startup verified with the dashboard responding at port 8765. The launcher displays a Terminal status window; users do not type commands.
- Real Chromium browser: created a project, imported the content template, executed a simulation, observed the editorial approval gate, approved it, and continued to completion.
- Created a custom word-count task with “Make useful work easier”; the result was 4.
- Entered a Jev choice with two options; simulation produced a clearly labelled choice and confidence, using the validated typed question format.
- Edited an unrun task's brief, reopened task details, and verified the saved input.
- Downloaded a project snapshot through the browser.
- Connected and cleared a fake session token without invoking Jev; no key is returned by the API or persisted in SQLite.
- Updated the project budget and sharing consent; verified that live execution presents a separate confirmation before dispatch.
- Desktop layout visually reviewed at 1440 pixels; mobile layout reviewed at 390 pixels with document width equal to the viewport width (no horizontal overflow).
- Browser console after the final frontend fixes: zero errors and zero warnings.

Browser testing used a separate database under the system temporary directory. The user's main dashboard database was left empty. No live Jev or generative model calls were made for UI validation. Python 3.11+ remains a prerequisite; Linux launcher behavior was not exercised on this Mac.
