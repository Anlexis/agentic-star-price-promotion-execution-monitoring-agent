"""Output formatting reference node.

NOT part of this agent's runtime pipeline, and in particular **not** this
agent's output boundary. RET-C3-010 assembles its compliance report in
``PricePromoCorrectionAgent.get_output()``, which is where the output gate runs;
see ``src/graph/graph.py``. This class is never instantiated by the graph.

It is kept as a worked reference for the node contract and is exercised by the
boundary test that proves the framework's call order for every node under
``src/nodes/``.
"""

from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event


class PostProcessNode(FunctionNode):
    """Format and finalize the output."""

    # Matches the manifest's required_trust_level. An implicit ANONYMOUS
    # inheritance is refused by the framework at class-definition time.
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        result = state.get("result", "")

        emit_trace_event(
            "post_process_formatted",
            {"has_result": bool(result)},
            state,
        )
        return {
            "formatted_output": result,
            "status": AgentStatus.SUCCESS,
        }
