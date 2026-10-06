"""RET-C3-010 — detect_discrepancy tool (Cat-3 ReAct action, v1 deterministic stub).

Called by ThinkNode when the (stub) LLM decides it needs to compare the approved
price list against actual shelf/POS prices. Reads price_records from state,
computes discrepancies, and returns a structured dict that ToolActNode appends to
state["tool_results"]; the discrepancy list is also written onto state["discrepancies"].

framework.* / shared.* imports only — no agenticstar, no live external I/O.
"""

from typing import Any

from shared.utils.audit_logger import emit_trace_event

_HIGH_SEVERITY_DELTA: int = 50


def detect_discrepancy(state: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Detect price/promo discrepancies from pre-loaded price_records.

    Args:
        state: Current agent state (read price_records).
        config: Injectable config dict (constructor-injectable stub hook) — tests
            may inject a fake record set via config["price_records"].

    Returns:
        dict with keys:
          discrepancies: list[dict] — [{store_id, sku, approved_price, actual_price, delta, severity}]
          checked_count: int        — total records evaluated
          tool: str                 — "detect_discrepancy" (for the tool_results log)
    """
    config = config or {}
    # production wires the real ESL/POS API here — v1 reads pre-loaded records only.
    # The caller's records are surfaced by AutonomousInitializeNode into
    # state["input_context"]["price_records"], not top-level state, so read both paths.
    records = (
        config.get("price_records")
        or state.get("price_records")
        or state.get("input_context", {}).get("price_records", [])
    )

    discrepancies: list[dict[str, Any]] = []
    for record in records:
        approved = record.get("approved_price")
        actual = record.get("actual_price")
        if approved is None or actual is None:
            continue
        delta = approved - actual
        if delta == 0:
            continue
        severity = "HIGH" if abs(delta) > _HIGH_SEVERITY_DELTA else "MEDIUM"
        discrepancies.append(
            {
                "store_id": record.get("store_id"),
                "sku": record.get("sku"),
                "approved_price": approved,
                "actual_price": actual,
                "delta": delta,
                "severity": severity,
            }
        )

    # Persist onto the domain field so build_think_prompt / downstream tools see them.
    state["discrepancies"] = discrepancies

    # Wheel-confirmed: write the checked records back onto
    # TOP-LEVEL state. build_think_prompt() reads len(state.get("price_records", []))
    # for its "price_records loaded: N" line, but the caller's records live in
    # state["input_context"] — so that line is 0 until detect runs. Writing the records
    # here makes the NEXT ThinkNode iteration see N>0, which lets the stub's step-0
    # guard (price_records > 0 AND discrepancies == 0) fire and terminate a
    # no-discrepancy (out-of-scope) run gracefully instead of looping detect to
    # max_iterations → ERROR.
    state["price_records"] = records

    emit_trace_event("detect_discrepancy_called", {"checked_count": len(records)}, state)

    return {
        "discrepancies": discrepancies,
        "checked_count": len(records),
        "tool": "detect_discrepancy",
    }
