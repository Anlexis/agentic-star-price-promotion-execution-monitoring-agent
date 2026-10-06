"""RET-C3-010 — Autonomous Multi-Store Price & Promo Correction Agent (Cat 3).

L1-direct: inherits AutonomousBaseGraph (framework.graph.autonomous_base_graph) per the
2026-05-18 policy (no L2 ReActAgent inheritance). Fixed framework-owned pipeline:

    START → initialize → think ⇄ act → finalize → END

All edges are wired by AutonomousBaseGraph.add_edges() — this template does NOT override
add_edges() and does NOT register the initialize/think/act/finalize slots (Case 1: omit
register_nodes() entirely and the framework auto-wires all four).

Loop termination is framework-owned (_route_after_think): tool_calls empty / status
SUCCESS / iterations >= max_iterations / status ERROR all route to finalize.

v1 uses a deterministic stub LLM (PriceCheckStubLLM). _validate_config() hard-fails at
compile() without config["budget_usd"] and without a config["llm"] exposing .complete();
__init__ defaults both, so the agent can also be built by the registry from
config/config.yaml or by the standalone adapter with no config at all.

The compliance report is assembled and gated in get_output(). A failed run, or a
report tripping the credential screen, returns a closed-set reason instead — never
node-authored text, and never a report-shaped object that a caller could mistake
for a clean result.

── Wheel-confirmed Cat-3 state/tool contract (verified against the framework wheel) ──
The framework invokes tools as ``tool_fn(**tool_args)`` (ToolActNode.execute) — a tool
receives ONLY its declared kwargs, NEVER the graph ``state``, and ToolActNode merges only
``{"tool_calls": [], "tool_results": <results>}`` back into state. AutonomousState's
``tool_results`` carries an ``operator.add`` reducer, so it ACCUMULATES across iterations;
it is the only domain channel that survives the loop. Domain keys on State(AutonomousState)
(discrepancies, dispatched_tasks, …) are NOT auto-propagated — a node would have to RETURN
them and they have no reducer. Therefore:

  * build_think_prompt() RECONSTRUCTS the running domain picture from state["tool_results"]
    + state["input_context"]["price_records"], and embeds it as a single STATE_JSON line so
    the stub can pass a state-shaped dict to each tool's `state` arg (the tools resolve their
    target from that dict — their signatures are unchanged).
  * assemble_output() likewise reconstructs the final report from state["tool_results"].

framework.* / shared.* imports only — no agenticstar.
"""

import json
import re
from typing import Any

from framework.graph.autonomous_base_graph import AutonomousBaseGraph
from framework.schemas.agent_status import AgentStatus
from framework.security.credential_detector import detect_credentials_in_value
from shared.utils.audit_logger import emit_trace_event
from src.schemas.state import State
from src.tools import TOOL_REGISTRY

# production: replace PriceCheckStubLLM with a real BaseLLM client here.
from src.services.stub_llm import PriceCheckStubLLM

_MAX_ITERATIONS: int = 20
_DEFAULT_BUDGET_USD: float = 2.50

# ── Output-boundary contract ──────────────────────────────────────────────────
# Closed-set reasons. The caller-visible error carries ONLY values chosen from
# this module — never a node-authored string, never an exception message, never
# anything read from error_log. error_log stays as the internal audit channel.
_REASON_RUN_FAILED: str = "compliance_run_failed"
_REASON_OUTPUT_WITHHELD: str = "output_withheld_by_policy"

# Local credential patterns kept as a UNION on top of the framework detector.
# The framework's detector describes credential FORMATS (Bearer / sk- / AKIA /
# JWT / db URIs) and matches none of the shapes below, so delegating to it alone
# would make this gate NARROWER, not tighter. Wider is safe; narrower is a bypass.
_LOCAL_CREDENTIAL_PATTERNS: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    # Forge personal-access tokens — not a format the framework detector carries.
    ("forge_access_token", re.compile(r"\bgl[a-z]{2,6}-[A-Za-z0-9_-]{8,}")),
    # key=value assignments; the framework matches formats, not assignments.
    (
        "credential_assignment",
        re.compile(
            r"(?i)\b(?:password|passwd|pwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token)" r"\s*[:=]\s*\S{6,}"
        ),
    ),
    # Long opaque value bound to a credential-ish name.
    (
        "named_opaque_secret",
        re.compile(r"(?i)\b\w*(?:key|secret|token|password)\w*\s*[:=]\s*[\"\']?[A-Za-z0-9+/=]{24,}"),
    ),
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
)

# The framework detector scans VALUES ONLY, never keys — consistent by
# construction. A credential parked in a KEY NAME therefore passes it, so the
# key screen below is ours.
_CREDENTIAL_KEY_TOKENS: tuple[str, ...] = (
    "token",
    "secret",
    "password",
    "passwd",
    "credential",
    "api_key",
    "apikey",
)


def _screen_value(value: Any) -> list[str]:
    """Return the names of every credential rule `value` trips (framework + local).

    Walks nested mappings and sequences: a credential riding inside
    report["discrepancies"][0]["sku"] must be caught, not only a top-level string.
    """
    findings: list[str] = []
    if isinstance(value, dict):
        for key, sub in value.items():
            if any(tok in str(key).lower() for tok in _CREDENTIAL_KEY_TOKENS):
                findings.append("credential_shaped_key")
            findings.extend(_screen_value(sub))
        return findings
    if isinstance(value, (list, tuple, set)):
        for sub in value:
            findings.extend(_screen_value(sub))
        return findings
    if not isinstance(value, str):
        return findings
    if detect_credentials_in_value(value):
        findings.append("framework_detector")
    for name, pattern in _LOCAL_CREDENTIAL_PATTERNS:
        if pattern.search(value):
            findings.append(name)
    return findings


def reconstruct_domain(state: Any) -> dict[str, Any]:
    """Rebuild the running domain picture from the framework-accumulated tool_results.

    The framework merges each tool's return into state["tool_results"] (operator.add
    reducer) as {"tool": <name>, "result": <return dict>, "args": <args>}. This walks
    those entries and rebuilds the lists the stub policy + report need:

      price_records:        from state["input_context"] (the caller's input)
      detect_ran:           True once a detect_discrepancy result has been recorded
      discrepancies:        the discrepancies list from the latest detect result
      dispatched_tasks:     every dispatch_correction result (task records)
      verification_results: every verify_correction result
      escalations:          every escalate result
      keihyo_risk_flags:    escalations whose keihyo_risk is True

    Returns a plain dict (JSON-serialisable) — both build_think_prompt and assemble_output
    consume it.
    """
    input_context = state.get("input_context", {}) or {}
    price_records = input_context.get("price_records", []) or []
    tool_results = state.get("tool_results", []) or []

    detect_ran = False
    discrepancies: list[dict[str, Any]] = []
    dispatched_tasks: list[dict[str, Any]] = []
    verification_results: list[dict[str, Any]] = []
    escalations: list[dict[str, Any]] = []
    keihyo_risk_flags: list[dict[str, Any]] = []

    for entry in tool_results:
        tool = entry.get("tool")
        result = entry.get("result", {}) or {}
        if tool == "detect_discrepancy":
            detect_ran = True
            discrepancies = result.get("discrepancies", [])
        elif tool == "dispatch_correction":
            dispatched_tasks.append(result)
        elif tool == "verify_correction":
            verification_results.append(result)
        elif tool == "escalate":
            escalations.append(result)
            if result.get("keihyo_risk"):
                keihyo_risk_flags.append(
                    {
                        "store_id": result.get("store_id"),
                        "sku": result.get("sku"),
                        "escalated_at": result.get("escalated_at"),
                    }
                )

    return {
        "price_records": price_records,
        "detect_ran": detect_ran,
        "discrepancies": discrepancies,
        "dispatched_tasks": dispatched_tasks,
        "verification_results": verification_results,
        "escalations": escalations,
        "keihyo_risk_flags": keihyo_risk_flags,
    }


def _unresolved_count(domain: dict[str, Any]) -> int:
    """Discrepancies not verified-resolved and not yet escalated."""
    discrepancies = domain.get("discrepancies", [])
    verified = domain.get("verification_results", [])
    escalated = domain.get("escalations", [])
    return len(
        [
            d
            for d in discrepancies
            if not any(
                v.get("sku") == d.get("sku") and v.get("store_id") == d.get("store_id") and v.get("resolved")
                for v in verified
            )
            and not any(e.get("sku") == d.get("sku") and e.get("store_id") == d.get("store_id") for e in escalated)
        ]
    )


class PricePromoCorrectionAgent(AutonomousBaseGraph):
    """Autonomous Cat-3 agent: detect price discrepancy → dispatch → verify → escalate.

    Construction (v1 stub LLM is the default; pass "llm" to override):
        agent = PricePromoCorrectionAgent()
        agent.compile()
        result = agent.invoke(
            "Run compliance check for promo cycle 2026-06-28",
            input_context={"price_records": [
                {"store_id": "S001", "sku": "SKU-001", "approved_price": 498, "actual_price": 548},
            ]},
        )
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """Fill the construction-time defaults the runtime manifest cannot carry.

        AgentRegistry builds the agent as ``Graph(config=<config/config.yaml>)``
        and the standalone adapter builds it with no config at all. Neither can
        supply ``llm``: it is a live object, not a YAML scalar. The framework's
        _validate_config() hard-fails at compile() without it, so an agent that
        does not default it here cannot be constructed by either entry point.
        An explicitly supplied value always wins.
        """
        config = dict(config or {})
        config.setdefault("llm", PriceCheckStubLLM())
        config.setdefault("budget_usd", _DEFAULT_BUDGET_USD)
        config.setdefault("max_iterations", _MAX_ITERATIONS)
        super().__init__(config)

    # ── Required: identity / iteration ceiling ────────────────────────────────

    @property
    def name(self) -> str:
        return "ret_c3_010"

    @property
    def state_schema(self) -> type:
        """Override the framework default (AutonomousState) with the domain subclass."""
        return State

    @property
    def max_iterations(self) -> int:
        """Iteration ceiling for the think-act loop; read from config, fall back to default.

        Must be a positive int ≤ 1000 (framework hard cap); enforced by _validate_config().
        """
        return int(self.config.get("max_iterations", _MAX_ITERATIONS))

    # ── Required: abstract methods (AutonomousBaseGraph contract) ─────────────

    def get_tools(self) -> list[Any]:
        """Return the 4 domain tools; bound to the LLM via llm.bind_tools() at register_nodes()."""
        return list(TOOL_REGISTRY.values())

    def build_think_prompt(self, state: Any) -> str:
        """Build the prompt passed to ThinkNode on each iteration.

        Deterministic — no datetime.now(). The running domain picture is reconstructed
        from state["tool_results"] (the framework-accumulated channel — domain state keys
        do NOT survive the loop; wheel-confirmed) and embedded both as human-readable
        counts AND as a single STATE_JSON line the stub parses to (a) route on the counts
        and (b) build the `state`-shaped dict it passes to each tool's `state` arg (the
        framework does not pass state to tools).
        """
        domain = reconstruct_domain(state)
        iterations = state.get("iterations", 0)

        price_records = domain["price_records"]
        discrepancies = domain["discrepancies"]
        dispatched = domain["dispatched_tasks"]
        verified = domain["verification_results"]
        escalated = domain["escalations"]
        unresolved = _unresolved_count(domain)

        # The stub reads STATE_JSON to (1) decide the next tool from the counts above and
        # (2) reconstruct the `state` dict the tools resolve their target from. detect_ran
        # distinguishes "iteration 0, detect not yet run" from "detect ran, found nothing".
        state_json = json.dumps(
            {
                "detect_ran": domain["detect_ran"],
                "price_records": price_records,
                "discrepancies": discrepancies,
                "dispatched_tasks": dispatched,
                "verification_results": verified,
                "escalations": escalated,
            }
        )

        return (
            f"[RET-C3-010] Price & Promo Compliance Agent — iteration {iterations}\n"
            f"Task: Detect discrepancies, dispatch corrections, verify, escalate if unresolved.\n"
            f"Tools available: detect_discrepancy, dispatch_correction, verify_correction, escalate\n\n"
            f"Current state:\n"
            f"  detect ran: {domain['detect_ran']}\n"
            f"  price_records loaded: {len(price_records)}\n"
            f"  discrepancies found: {len(discrepancies)}\n"
            f"  dispatched: {len(dispatched)}\n"
            f"  verified: {len(verified)}\n"
            f"  escalated: {len(escalated)}\n"
            f"  unresolved (not verified OK, not escalated): {unresolved}\n\n"
            f"STATE_JSON: {state_json}\n\n"
            "If all discrepancies are resolved or escalated, output your final answer (no tool calls)."
        )

    def assemble_output(self, state: Any) -> dict[str, Any]:
        """Shape the final compliance report dict from terminal state.

        Returns the domain report dict DIRECTLY (compliance_report, keihyo_risk_flags,
        escalations, ...) — NOT wrapped under a "final_output" key.

        Wiring (wheel-confirmed): the framework auto-wires a plain FinalizeNode() (Case 1 —
        register_nodes() omitted) which does NOT call this method, so it is never invoked by
        the framework itself. get_output() (below) calls it on the terminal state after
        BaseGraph.invoke() finishes and places the result under the envelope's "output" key.

        The report is reconstructed from state["tool_results"] (domain state keys do NOT
        survive the framework loop — wheel-confirmed). cost_usd is added automatically by
        _enrich_output() — do NOT include it here.
        """
        domain = reconstruct_domain(state)
        discrepancies = domain["discrepancies"]
        verified = domain["verification_results"]
        escalations = domain["escalations"]
        keihyo_risk_flags = domain["keihyo_risk_flags"]
        dispatched_tasks = domain["dispatched_tasks"]
        resolved_count = len([v for v in verified if v.get("resolved")])

        compliance_report = {
            "discrepancies_total": len(discrepancies),
            "dispatched_total": len(dispatched_tasks),
            "resolved_total": resolved_count,
            "escalated_total": len(escalations),
            "keihyo_risk_total": len(keihyo_risk_flags),
            "iterations": state.get("iterations", 0),
        }

        return {
            "compliance_report": compliance_report,
            "keihyo_risk_flags": keihyo_risk_flags,
            "escalations": escalations,
            "dispatched_tasks": dispatched_tasks,
            "verification_results": verified,
            "discrepancies": discrepancies,
            "step_count": state.get("iterations", 0),
            "iterations": state.get("iterations", 0),
        }

    def get_output(self, state: Any) -> dict[str, Any]:
        """Assemble the compliance report, gate it, and surface it on the envelope.

        BaseGraph.invoke() calls this on the terminal state. The framework's
        auto-wired plain FinalizeNode() never calls assemble_output(), so this is
        the only place the domain report reaches a caller — and therefore the only
        place an output gate can stand. The framework's per-node output-gate hook
        is not available here: it is defined on the node classes, and this
        template registers no nodes of its own.

        Base envelope for a Cat-3 graph is
        ``{"output": final_output or result, "status", "trace_id",
        "correlation_id", "node_history", "artifacts", "thoughts"}``. The
        ``final_output or result`` fallback is closed here by always assigning
        ``output`` — on the contained paths too, so a refusal cannot fall through
        onto whatever survived in state.
        """
        base: dict[str, Any] = super().get_output(state)

        # A failed run must not publish a report-shaped object. Before this, an
        # ERROR invoke still returned a fully-formed all-zero compliance_report,
        # indistinguishable from a clean run with no discrepancies.
        if str(state.get("status", "")).lower() != AgentStatus.SUCCESS.value.lower():
            return self._contain(base, _REASON_RUN_FAILED)

        report = self.assemble_output(state)
        findings = _screen_value(report)
        if findings:
            emit_trace_event(
                "output_gate_blocked",
                {"reason": _REASON_OUTPUT_WITHHELD, "violations": len(findings)},
                state,
            )
            return self._contain(base, _REASON_OUTPUT_WITHHELD)

        base["output"] = report
        return base

    def _contain(self, base: dict[str, Any], reason: str) -> dict[str, Any]:
        """Return the envelope carrying NO released text — closed-set labels only.

        `reason` is always a constant declared in this module. Nothing is read
        from ``error_log``, ``thoughts`` or any node-authored string: those carry
        upstream text and, on the credential path, a framework traceback with
        source paths. ``error_log`` remains the internal audit channel.

        The envelope stays TRUTHY — a falsy ``output`` would re-open the
        framework's ``final_output or result`` fallback.
        """
        base["output"] = {"reason": reason}
        base["status"] = AgentStatus.ERROR.value
        # Output-bearing companions on the same envelope.
        base["thoughts"] = []
        base["artifacts"] = []
        return base
