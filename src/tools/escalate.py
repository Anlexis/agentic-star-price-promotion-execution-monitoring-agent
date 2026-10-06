"""RET-C3-010 — escalate tool (Cat-3 ReAct action, v1 deterministic stub).

Called when verify_correction returns resolved: False (unresolved at the 4h deadline),
or on the final sweep before promotion launch. Notifies the Area Manager and appends a
景品表示法 (price-display-law) risk flag when the item is still unresolved close to launch.

The stub LLM passes "pending" placeholders, so this tool resolves the next unresolved,
not-yet-escalated discrepancy from state when args are placeholders.

framework.* / shared.* imports only — no agenticstar, no live external I/O.
"""

import json
from datetime import datetime, timezone
from typing import Any

from shared.utils.audit_logger import emit_trace_event

_DEFAULT_ESCALATION_LOG_PATH: str = "/tmp/ret_c3_010_escalation.log"
_PLACEHOLDER: str = "pending"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_target(store_id: str, sku: str, state: dict[str, Any]) -> tuple[str, str]:
    """Resolve the real (store_id, sku) when the stub LLM passes placeholders.

    Pick the next discrepancy that is not verified-resolved and not yet escalated.
    # production: the real LLM passes the exact store_id/sku being escalated.
    """
    if store_id != _PLACEHOLDER and sku != _PLACEHOLDER:
        return store_id, sku

    resolved_keys = {
        (v.get("store_id"), v.get("sku")) for v in state.get("verification_results", []) if v.get("resolved")
    }
    escalated_keys = {(e.get("store_id"), e.get("sku")) for e in state.get("escalations", [])}
    for disc in state.get("discrepancies", []):
        key = (disc.get("store_id"), disc.get("sku"))
        if key not in resolved_keys and key not in escalated_keys:
            return disc.get("store_id"), disc.get("sku")
    return store_id, sku


def escalate(
    store_id: str,
    sku: str,
    reason: str,
    keihyo_risk: bool,
    state: dict[str, Any],
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Escalate an unresolved discrepancy to the Area Manager (stub).

    Args:
        store_id:    Target store.
        sku:         SKU being escalated.
        reason:      Human-readable reason for escalation.
        keihyo_risk: True if the unresolved item risks a 景品表示法 violation.
        state:       Current agent state (appends escalations + keihyo_risk_flags).
        config:      Injectable config — override config["escalation_log_path"] for tests.

    Returns:
        dict with keys:
          store_id: str
          sku: str
          escalated_at: str — ISO-8601 timestamp
          keihyo_risk: bool
          tool: str         — "escalate"
          status: str       — "ESCALATED"
    """
    config = config or {}
    store_id, sku = _resolve_target(store_id, sku, state)

    escalated_at = _now_iso()
    log_path = config.get("escalation_log_path", _DEFAULT_ESCALATION_LOG_PATH)

    escalation_record = {
        "store_id": store_id,
        "sku": sku,
        "area_manager": config.get("area_manager", "area_manager"),
        "reason": reason,
        "keihyo_risk": keihyo_risk,
        "escalated_at": escalated_at,
    }

    state.setdefault("escalations", []).append(escalation_record)

    if keihyo_risk:
        state.setdefault("keihyo_risk_flags", []).append(
            {"store_id": store_id, "sku": sku, "escalated_at": escalated_at}
        )

    # production wires the real Area Manager notification (Slack/email) here.
    try:
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(escalation_record) + "\n")
    except OSError:
        # Stub must never hard-fail the loop on a log-write error.
        pass

    emit_trace_event(
        "escalate_called",
        {"store_id": store_id, "sku": sku, "keihyo_risk": keihyo_risk},
        state,
    )

    return {
        "store_id": store_id,
        "sku": sku,
        "escalated_at": escalated_at,
        "keihyo_risk": keihyo_risk,
        "tool": "escalate",
        "status": "ESCALATED",
    }
