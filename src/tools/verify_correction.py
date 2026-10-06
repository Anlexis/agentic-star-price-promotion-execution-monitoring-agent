"""RET-C3-010 — verify_correction tool (Cat-3 ReAct action, v1 deterministic stub).

Called ~4h after dispatch (or on the LLM's next iteration) to re-check whether a
dispatched correction was acted upon. v1 stub: resolution is driven by an injectable
config["resolved_task_ids"] list rather than a live POS/ESL re-poll.

The stub LLM passes "pending" placeholders, so this tool resolves the next unverified
dispatched task from state["dispatched_tasks"] when args are placeholders.

framework.* / shared.* imports only — no agenticstar, no live external I/O.
"""

from datetime import datetime, timezone
from typing import Any

from shared.utils.audit_logger import emit_trace_event

_PLACEHOLDER: str = "pending"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_target(task_id: str, store_id: str, sku: str, state: dict[str, Any]) -> tuple[str, str, str]:
    """Resolve the real (task_id, store_id, sku) when the stub LLM passes placeholders.

    Pick the next dispatched task that has not yet been verified.
    # production: the real LLM passes the exact task_id from dispatch output.
    """
    if task_id != _PLACEHOLDER:
        return task_id, store_id, sku

    verified_ids = {v.get("task_id") for v in state.get("verification_results", [])}
    for task in state.get("dispatched_tasks", []):
        if task.get("task_id") not in verified_ids:
            return task.get("task_id"), task.get("store_id"), task.get("sku")
    return task_id, store_id, sku


def verify_correction(
    task_id: str,
    store_id: str,
    sku: str,
    state: dict[str, Any],
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Verify whether a dispatched correction task has been resolved (stub).

    Args:
        task_id:  The task reference from dispatch_correction output.
        store_id: Target store.
        sku:      SKU under verification.
        state:    Current agent state (reads dispatched_tasks, appends verification_results).
        config:   Injectable config — inject config["resolved_task_ids"]: list[str] for testing.

    Returns:
        dict with keys:
          task_id: str
          store_id: str
          sku: str
          resolved: bool  — True if correction confirmed
          checked_at: str — ISO-8601 timestamp
          tool: str       — "verify_correction"
    """
    config = config or {}
    task_id, store_id, sku = _resolve_target(task_id, store_id, sku, state)

    # production wires the real POS/ESL re-poll here — v1 reads the injected set.
    resolved_task_ids = config.get("resolved_task_ids", [])
    resolved = task_id in resolved_task_ids

    checked_at = _now_iso()
    result = {
        "task_id": task_id,
        "store_id": store_id,
        "sku": sku,
        "resolved": resolved,
        "checked_at": checked_at,
        "tool": "verify_correction",
    }

    state.setdefault("verification_results", []).append(result)

    emit_trace_event(
        "verify_correction_called",
        {"task_id": task_id, "resolved": resolved},
        state,
    )

    return result
