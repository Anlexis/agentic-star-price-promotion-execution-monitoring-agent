"""Input validation / enrichment reference node.

NOT part of this agent's runtime pipeline. RET-C3-010 is an autonomous
think-act loop whose four slots (initialize, think, act, finalize) are wired by
the framework base graph; this template registers no nodes of its own, so this
class is never instantiated by the graph. Its domain work lives in ``src/tools/``.

It is kept as a worked reference for the node contract — declare a trust level,
return only the fields the node changes, emit a domain audit event — and is
exercised by the boundary test that proves the framework's call order for every
node under ``src/nodes/``.

Node contract:
  - Extend FunctionNode; implement execute(state) -> dict
  - Return ONLY the fields this node changes (never full state)
  - Return AgentStatus enum constants — never plain strings
  - Read input_context via state.get("input_context", {}) — read-only
  - Never import from mediator/, api/, or other agents
"""

from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event


class PreProcessNode(FunctionNode):
    """Validate and enrich incoming input before main processing."""

    # Matches the manifest's required_trust_level. An implicit ANONYMOUS
    # inheritance is refused by the framework at class-definition time.
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        user_input = state.get("user_input", "")
        input_context = state.get("input_context", {})  # read-only

        if not user_input or not user_input.strip():
            emit_trace_event("pre_process_rejected", {"reason": "empty_user_input"}, state)
            return {
                "status": AgentStatus.ERROR,
                "error_log": ["PreProcessNode: user_input is empty or missing"],
            }

        emit_trace_event(
            "pre_process_validated",
            {"input_length": len(user_input.strip())},
            state,
        )
        return {
            "validated_input": user_input.strip(),
            "enriched_context": {
                "source": "ret_c3_010",
                "channel": input_context.get("channel", "unknown"),
            },
            "status": AgentStatus.SUCCESS,
        }
