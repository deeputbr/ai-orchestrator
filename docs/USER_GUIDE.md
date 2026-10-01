# Using AI Orchestrator

## Open the app

Double-click **Start Orchestrator.app** on your Mac. A Terminal window displays server status; no commands need to be typed. The browser opens the dashboard at **http://127.0.0.1:8765**. Keep the app inside the project folder, where it can find the code and workflow library. Python 3.11 or newer must be installed.

Closing a browser tab leaves the app running. Reopening the launcher returns you to the same app. To stop it, open **Connections & setup** and choose **Stop local app**. The app waits for a workflow to finish before allowing shutdown.

The alternative **Start Orchestrator.command** launcher starts the same dashboard and displays a Terminal window. You do not need to type commands. Linux users can launch **Start Orchestrator.desktop** after allowing their desktop to run it.

## First project

Choose **New project**. Use a short project name such as `my-newsletter`, describe the outcome you want, and set an API budget in dollars. External sharing starts disabled.

Choose **Local starter** from the workflow library and press **Run workflow**. Two local tasks complete with real results and no provider calls. Click a task name to inspect its inputs, output, validation, and history.

For an AI workflow demonstration, create another project, add **Content draft**, and run in **Simulation**. The outline and script complete with clearly marked simulated output. The final review pauses. Review the details, choose **Approve this request**, and run the workflow again to continue.

## Your own work

Choose **Add task** and supply a name, instructions, and input text or brief. AI writing/reasoning tasks use Jev routing and Sol/Astra generation in live mode. Local tasks support word counts, text hashes, and JSON normalization.

Choose **Jev chooses between options** to outsource a bounded choice. Describe the criteria in Instructions, add any background in the brief, and enter at least two options, one per line. The app creates the required typed Jev request and displays the selected option and confidence. Simulation chooses a fixture answer and is labelled accordingly.

Select earlier tasks under **Run after these tasks** to create a dependency. Their results are supplied as context. Optional approval and local-only settings apply to each task.

Use **Edit task** to customize descriptions, briefs, risk, complexity, and approval/local-only flags before execution. Task names, types, and dependencies remain fixed during edits. Imported tasks keep additional inputs and validators. For advanced Jev packs or custom context files, import a workflow JSON file through **Import workflow JSON**. Bundled templates contain sample briefs; review and customize them before live execution.

Executed tasks preserve their history. Add a new task with a new name for revised work or to move from simulated AI results to live AI results. The engine prevents mixing simulation and live attempts or feeding simulated AI outputs into live tasks.

## Connect Jev and run live

Open **Connections & setup**, paste your Jev token, and choose **Connect Jev**. The token stays in app memory and is never returned by the dashboard API or written to project storage. Re-enter it after stopping and reopening the app. A token provided through `JEV_API_KEY` or `TYPESAFE_API_KEY` is also supported.

**Check connections** verifies the configured generation credentials and Jev token/pricing presence without making a model call. It does not verify Jev token validity, account model access, or remaining account allowance.

The bundled profile uses the existing ChatGPT/Codex sign-in for Sol and Astra. The UI does not ask for or copy those credentials. If sign-in needs attention, complete it in the installed ChatGPT/Codex application and check again. A separately configured direct OpenAI API backend is labelled as API billing.

In **Project settings**, explicitly enable sharing with the configured providers. Choose **Live**, press **Run workflow**, and review the confirmation. This can consume Jev API budget and shared ChatGPT/Codex usage. The engine performs the same checks as the CLI before sending each request.

## Review and resolve pauses

**Needs attention** counts approval gates and paused/failed tasks. Each gate gives the reason and suggested next action.

- **Approve this request** approves only an explicit approval gate. It cannot override a budget, sharing restriction, provider issue, or uncertain charge.
- **Recheck task** queues a paused or failed task after you fix the cause. Press **Run workflow** to execute it. Identical dispatched requests are not automatically replayed.
- For an uncertain provider outcome, open **Review details**. Check the provider result or billing, enter the verified API amount and a verification note, and record the outcome. Subscription outcomes use zero API dollars. Select “never reached the provider” only when you have verified that fact. This records the outcome without replaying the request.

The settings file `config/settings.toml` controls model IDs, pricing, routing policy, and subscription call caps. Project API budgets and sharing settings can be changed directly in the dashboard. A task paused by an account usage limit still needs the provider allowance to become available.

## Data and costs

The dashboard and CLI share `.orchestrator/state.sqlite3` in the repository. Projects, tasks, attempts, and audit events persist across app restarts. API charges are estimates in USD, with unresolved reservations included in the budget display. ChatGPT calls are counted separately; the app does not invent a USD cost or remaining subscription percentage.

**Download task result** exports the selected output. **Download project snapshot** exports the summary, tasks, attempt history, and audit events for review. These may contain your task inputs and outputs. Snapshots are not restore files; the import action accepts new workflow definitions only.

The app runs on this computer and binds to a local address. No frontend libraries, fonts, or assets load from third-party websites. Existing tasks and safety checks remain in the Python core.
