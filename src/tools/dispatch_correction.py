"""RET-C3-010 — dispatch_correction tool (Cat-3 ReAct action, v1 deterministic stub).

Called when the (stub) LLM decides a discrepancy needs a correction task sent to the
store manager. v1 is a deterministic stub: it writes the task as a JSON line to a log
file instead of a real Slack/email dispatch.

The stub LLM passes "pending" placeholders for store_id/sku (it only sees the prompt
summary, not full state), so this tool resolves the next undispatched discrepancy from
state["discrepancies"] when the args are placeholders — see _resolve_target().

framework.* / shared.* imports only — no agenticstar, no live external I/O.
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from shared.utils.audit_logger import emit_trace_event

_DEFAULT_DISPATCH_LOG_PATH: str = "/tmp/ret_c3_010_dispatch.log"
_PLACEHOLDER: str = "pending"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_target(store_id: str, sku: str, state: dict[str, Any]) -> tuple[str, str]:
    """Resolve the real (store_id, sku) when the stub LLM passes placeholders.

    v1 stub limitation: the deterministic policy can't read full state, so it sends
    "pending". Pick the next discrepancy that has not been dispatched yet.
    # production: the real LLM passes exact args extracted from state context.
    """
    if store_id != _PLACEHOLDER and sku != _PLACEHOLDER:
        return store_id, sku

    dispatched = state.get("dispatched_tasks", [])
    dispatched_keys = {(t.get("store_id"), t.get("sku")) for t in dispatched}
    for disc in state.get("discrepancies", []):
        key = (disc.get("store_id"), disc.get("sku"))
        if key not in dispatched_keys:
            return disc.get("store_id"), disc.get("sku")
    return store_id, sku


def dispatch_correction(
    store_id: str,
    sku: str,
    correction: str,
    deadline: str,
    priority: str,
    state: dict[str, Any],
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Dispatch a correction task to the store manager (stub — logs to file).

    Args:
        store_id:   Target store identifier.
        sku:        Product SKU requiring correction.
        correction: Human-readable correction instruction.
        deadline:   ISO-8601 string — when the fix must be complete.
        priority:   "HIGH" | "MEDIUM" | "LOW".
        state:      Current agent state (for session context; appends dispatched_tasks).
        config:     Injectable config — can override log path for tests
                    (config["dispatch_log_path"]).

    Returns:
        dict with keys:
          task_id: str       — generated task reference (uuid4 short)
          store_id: str
          sku: str
          dispatched_at: str — ISO-8601 timestamp
          status: str        — "DISPATCHED" (stub always succeeds)
          tool: str          — "dispatch_correction"
    """
    config = config or {}
    store_id, sku = _resolve_target(store_id, sku, state)

    task_id = str(uuid.uuid4())[:8]
    dispatched_at = _now_iso()
    log_path = config.get("dispatch_log_path", _DEFAULT_DISPATCH_LOG_PATH)

    # Required message structure:
    # Store ID / SKU / required correction / deadline / priority level.
    task_record = {
        "task_id": task_id,
        "store_id": store_id,
        "sku": sku,
        "correction": correction,
        "deadline": deadline,
        "priority": priority,
        "dispatched_at": dispatched_at,
    }

    # production wires the real Slack webhook / SMTP here — v1 appends a JSON line.
    try:
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(task_record) + "\n")
    except OSError:
        # Stub must never hard-fail the loop on a log-write error.
        pass

    state.setdefault("dispatched_tasks", []).append(task_record)

    emit_trace_event(
        "dispatch_correction_called",
        {"store_id": store_id, "sku": sku, "priority": priority},
        state,
    )

    return {
        "task_id": task_id,
        "store_id": store_id,
        "sku": sku,
        "dispatched_at": dispatched_at,
        "status": "DISPATCHED",
        "tool": "dispatch_correction",
    }
