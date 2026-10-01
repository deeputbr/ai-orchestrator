"""Policy, not model opinion, selects the executable tier."""
from orchestrator.core.local import OPERATIONS
from orchestrator.core.models import Decision, Tier


def route(task, assessment, settings, sol_failures=0, astra_failures=0):
    if task.task_type in OPERATIONS:
        return Decision(Tier.CODE, "Registered deterministic operation")
    if task.task_type == "jev_decision":
        return Decision(Tier.JEV, "Direct typed Jev decision")
    unsupported = set(task.capabilities) - {"text", "reasoning", "code_proposal"}
    if unsupported:
        return Decision(Tier.HUMAN, "Unsupported capabilities: " + ", ".join(sorted(unsupported)))
    if task.task_type not in ("generation", "coding", "research", "content"):
        return Decision(Tier.HUMAN, "Unsupported task type; register a capable adapter")
    if astra_failures >= settings.astra_max_attempts:
        return Decision(Tier.HUMAN, "Astra attempts exhausted; review collected evidence")
    if assessment is None:
        return Decision(Tier.JEV, "Obtain bounded routing signals")
    threshold = settings.confidence_threshold
    s = assessment.signals
    if assessment.confidence < threshold:
        return Decision(Tier.HUMAN, "Low Jev choice confidence; clarify task or supply evidence")
    if s["context_sufficient"] < threshold:
        return Decision(Tier.HUMAN, "Insufficient or uncertain context")
    if s["visual"] > 1 - threshold or "vision" in task.capabilities:
        return Decision(Tier.HUMAN, "Visual input adapter is not implemented in V1")
    # Noul is P(yes), not a confidence field. An ambiguous security answer must not route cheap.
    for key in ("architecture", "security", "irreversible"):
        if 1 - threshold < s[key] < threshold:
            return Decision(Tier.HUMAN, f"Ambiguous {key} signal; clarify before execution")
    if s["deterministic"] >= threshold:
        return Decision(Tier.HUMAN, "Likely deterministic but no registered operation; add local handler")
    triggers = {
        "high_risk": task.risk in ("HIGH", "CRITICAL") or assessment.risk in ("HIGH", "CRITICAL"),
        "architecture": s["architecture"] >= threshold or task.metadata.get("architecture") is True,
        "security": s["security"] >= threshold or task.metadata.get("security") is True,
        "irreversible": s["irreversible"] >= threshold or task.metadata.get("irreversible") is True,
        "very_high_complexity": task.complexity == "VERY_HIGH" or assessment.complexity == "VERY_HIGH",
        "repeated_failure": sol_failures >= settings.sol_max_attempts,
    }
    active = [key for key in settings.astra_triggers if triggers[key]]
    if active:
        return Decision(Tier.ASTRA, "Policy escalation: " + ", ".join(active))
    if sol_failures >= settings.sol_max_attempts:
        return Decision(Tier.HUMAN, "Sol exhausted and repeated-failure escalation disabled")
    return Decision(Tier.SOL, "Default capable generation tier")
