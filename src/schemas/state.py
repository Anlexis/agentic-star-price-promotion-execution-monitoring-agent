"""AgentCore Platform v1.0 — RET-C3-010 domain state (Cat 3)."""

# ADR-005: State must be a flat TypedDict — never Pydantic BaseModel.
# LangGraph checkpoints use msgpack serialization; Pydantic objects
# cause silent corruption. Extend AutonomousState (the framework Cat-3
# state base) with agent-specific fields only. Do NOT add credentials,
# secrets, or Pydantic models.
#
# Cat-3 rule (the framework rules §2-2): templates MUST NOT redeclare the state class —
# inherit it from the framework. AutonomousBaseGraph.state_schema returns
# AutonomousState by default; src/graph/graph.py overrides state_schema to
# return THIS subclass so the domain fields are carried through the loop.
#
# This is NOT the Cat-1/2 AgentState pattern — Cat 3 subclasses
# AutonomousState, which already adds the ReAct-loop machinery (iterations,
# tool_calls, tool_results, thoughts, working_memory, artifacts, cost_usd).

from typing import Any

from framework.schemas.autonomous_state import AutonomousState


class State(AutonomousState):
    """Domain state for RET-C3-010 (Autonomous Multi-Store Price & Promo Correction).

    Extends AutonomousState (framework Cat-3 base) with retail price-check domain
    fields. AutonomousBaseGraph.state_schema is overridden in graph.py to return
    this class.

    Fields inherited from AutonomousState (do NOT redeclare):
        plan, thoughts, tool_calls, tool_results, working_memory, memory_loaded,
        artifacts, final_output, cost_usd, iterations — plus all AgentState base
        fields (user_input, status, session_id, schema_version,
        caller_trust_level, node_history, error_log, etc.).

    All fields must be primitives / JSON-serialisable (msgpack constraint, ADR-005).
    """

    # ── Domain fields — RET-C3-010 specific ───────────────────────────────────
    price_records: list[dict[str, Any]]  # pre-loaded approved-vs-actual records (input)
    discrepancies: list[dict[str, Any]]  # {store_id, sku, approved_price, actual_price, delta, severity}
    dispatched_tasks: list[dict[str, Any]]  # {task_id, store_id, sku, correction, deadline, priority, dispatched_at}
    verification_results: list[dict[str, Any]]  # {task_id, store_id, sku, resolved, checked_at}
    escalations: list[dict[str, Any]]  # {store_id, sku, area_manager, escalated_at}
    compliance_report: dict[str, Any]  # final report assembled by assemble_output()
    keihyo_risk_flags: list[dict[str, Any]]  # 景品表示法-risk items: {store_id, sku, escalated_at}
    step_count: int  # domain mirror of iterations — prompt context only
