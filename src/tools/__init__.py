"""RET-C3-010 — domain tool registry for the Cat-3 ReAct loop.

get_tools() in src/graph/graph.py returns list(TOOL_REGISTRY.values()); the
framework binds these to the (stub) LLM at register_nodes() time, and ToolActNode
invokes each by name via tool_fn(**args).
"""

from typing import Any

from src.tools.detect_discrepancy import detect_discrepancy
from src.tools.dispatch_correction import dispatch_correction
from src.tools.verify_correction import verify_correction
from src.tools.escalate import escalate

TOOL_REGISTRY: dict[str, Any] = {
    "detect_discrepancy": detect_discrepancy,
    "dispatch_correction": dispatch_correction,
    "verify_correction": verify_correction,
    "escalate": escalate,
}

__all__ = [
    "TOOL_REGISTRY",
    "detect_discrepancy",
    "dispatch_correction",
    "verify_correction",
    "escalate",
]
