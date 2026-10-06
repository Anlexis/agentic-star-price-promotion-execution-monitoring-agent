# RET-C3-010 — Proof-of-Boundary: tool-registry boundary + out-of-scope handling
#
# A Cat-3 agent must only be able to call its declared domain tools (no file I/O,
# shell exec, or live network clients). An out-of-scope request (nothing actionable)
# must not crash — the loop must bound out and still assemble a (zero-discrepancy)
# compliance report.
#
# Asserted against the MERGED develop implementation + the source-review fixes (
# Gaps A & B) AND the released wheel agenticstar-agentcore==1.0.0 (CI diagnostic,
# verified against the framework wheel).
#
#   PB-SCOPE-01  get_tools() contains exactly the 4 domain tools — no extras.
#   PB-SCOPE-02  No live external I/O client imported at any tool module level
#                (static AST scan: no requests / httpx / boto3 / smtplib).
#   PB-SCOPE-03  Out-of-scope input (unknown store group / SKU, prices already in
#                agreement → no discrepancy → nothing actionable) terminates
#                gracefully (status SUCCESS) and still assembles a zero-discrepancy
#                compliance_report.
#
# ── PB-SCOPE-03 behavior (after source-review fix, Gap B) ──
# The v1 deterministic stub LLM (src/services/stub_llm.py) has a step-0 terminate branch:
# once detect_discrepancy has run and found nothing, complete() returns empty tool_calls so
# the framework routes to finalize and ends with status SUCCESS. So an out-of-scope request
# (prices in agreement → detect finds 0) detects once, then terminates naturally — well
# below max_iterations — instead of re-issuing detect forever until max_iterations → ERROR.
# We therefore assert SUCCESS here.
#
# Gap-B mechanism (wheel-confirmed): the framework invokes
# tools as tool_fn(**tool_args) and never passes the graph state; ToolActNode merges only
# {tool_calls, tool_results} back, and AutonomousState's tool_results has an operator.add
# reducer — it is the ONLY domain channel that survives the loop. build_think_prompt()
# reconstructs the running picture from state["tool_results"] and emits a `detect ran: <bool>`
# flag (False at iteration 0, True only after a detect result is recorded). The stub's step-0
# guard fires on `detect ran: True AND discrepancies == 0 AND dispatched == 0` and terminates.
# The happy-path is unaffected: when detect finds discrepancies>0 the guard does not fire and
# the loop proceeds to dispatch/verify/escalate.
#
# Cat-3 build/invoke contract (verified against framework.graph.base_graph source
# and the merged src/api/server.py): compile() compiles IN-PLACE and returns None;
# invoke() is a method ON THE AGENT — invoke(user_input: str, ..., input_context=None).
# So: construct -> agent.compile() -> agent.invoke(text, input_context=...).
#
# Result-shape contract (released wheel agenticstar-agentcore==1.0.0): RET-C3-010
# OVERRIDES get_output() (Gap A) to set output = assemble_output(state) — the framework's
# plain FinalizeNode never calls assemble_output(), so the override is what surfaces the
# report. compliance_report lives at result["output"]["compliance_report"]. _report()
# probes every plausible holder so the assertion is robust to the output shape.

import ast
import os

import pytest

from src.tools import TOOL_REGISTRY

_EXPECTED_TOOL_NAMES = {
    "detect_discrepancy",
    "dispatch_correction",
    "verify_correction",
    "escalate",
}

_FORBIDDEN_IO_MODULES = {"requests", "httpx", "boto3", "smtplib", "urllib3", "socket"}

_TOOLS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "src", "tools")


def _tool_module_files() -> list[str]:
    """Return the 4 domain tool module paths (exclude __init__.py)."""
    files = []
    for name in os.listdir(_TOOLS_DIR):
        if name.endswith(".py") and name != "__init__.py":
            files.append(os.path.join(_TOOLS_DIR, name))
    return files


def _module_level_imports(filepath: str) -> set[str]:
    """Collect top-level imported module roots from a Python file via AST."""
    with open(filepath, "r") as f:
        tree = ast.parse(f.read(), filename=filepath)

    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                roots.add(node.module.split(".")[0])
    return roots


def _report(result):
    """Return the dict holder that actually carries the assemble_output() payload.

    After the Gap-A fix RET-C3-010 overrides get_output() to set output = assemble_output(state),
    the domain report dict directly. So compliance_report lives at result["output"]. Probe
    "output" first, then "final_output" (tolerant of the holder key across wheel versions), then
    the top level; return the first dict that contains compliance_report, falling back to result.
    """
    for key in ("output", "final_output"):
        holder = result.get(key)
        if isinstance(holder, dict) and "compliance_report" in holder:
            return holder
    if isinstance(result, dict) and "compliance_report" in result:
        return result
    return result


class TestPbScope01ExactlyFourTools:
    """PB-SCOPE-01: the registry / get_tools() expose exactly the 4 domain tools."""

    def test_registry_has_exactly_four_named_tools(self):
        """TOOL_REGISTRY exposes exactly the 4 expected tool names (no extras)."""
        names = {fn.__name__ for fn in TOOL_REGISTRY.values()}
        assert names == _EXPECTED_TOOL_NAMES
        assert len(TOOL_REGISTRY) == 4

    def test_get_tools_returns_exactly_four(self):
        """agent.get_tools() returns exactly 4 callables matching the 4 tool names."""
        try:
            from src.graph.graph import PricePromoCorrectionAgent
            from src.services.stub_llm import PriceCheckStubLLM
        except ImportError as exc:  # pragma: no cover — SDK wheel absent
            pytest.skip(f"Framework not installed in CI: {exc}")

        agent = PricePromoCorrectionAgent(config={"llm": PriceCheckStubLLM(), "budget_usd": 2.50})
        tools = agent.get_tools()
        assert len(tools) == 4
        assert {t.__name__ for t in tools} == _EXPECTED_TOOL_NAMES


class TestPbScope02NoLiveExternalIO:
    """PB-SCOPE-02: tool stubs must not pull in live network / mail clients."""

    def test_no_forbidden_io_module_imported(self):
        """No tool module imports requests / httpx / boto3 / smtplib at module level."""
        offenders = {}
        for filepath in _tool_module_files():
            roots = _module_level_imports(filepath)
            bad = roots & _FORBIDDEN_IO_MODULES
            if bad:
                offenders[os.path.basename(filepath)] = sorted(bad)
        assert offenders == {}, f"live-I/O imports found in tool stubs: {offenders}"


class TestPbScope03OutOfScopeGracefulSuccess:
    """PB-SCOPE-03: an out-of-scope request terminates gracefully (status SUCCESS).

    After the source-review fix (Gap B) the stub LLM detects once, finds nothing,
    and emits empty tool_calls (step-0 terminate-on-empty), so the loop routes to finalize and
    ends with status SUCCESS — well below max_iterations — instead of looping detect forever to
    ERROR. The zero-discrepancy compliance_report is assembled and surfaced via invoke() (Gap A
    get_output() override).
    """

    def test_unknown_store_group_terminates_with_success_report(self):
        """Unknown store group / SKU, nothing actionable -> detect once, then terminate
        gracefully (status SUCCESS) with a zero-discrepancy report. NEVER hangs."""
        try:
            from src.graph.graph import PricePromoCorrectionAgent
            from src.services.stub_llm import PriceCheckStubLLM
            from framework.schemas.agent_status import AgentStatus
        except ImportError as exc:  # pragma: no cover — SDK wheel absent
            pytest.skip(f"Framework not installed in CI: {exc}")

        agent = PricePromoCorrectionAgent(config={"llm": PriceCheckStubLLM(), "budget_usd": 2.50, "max_iterations": 20})
        try:
            agent.compile()
            result = agent.invoke(
                "Run compliance check for unknown group",
                input_context={
                    # Out-of-scope: unrecognised store group / SKU, prices already
                    # in agreement -> no discrepancy -> nothing to dispatch.
                    "price_records": [
                        {
                            "store_id": "ZZ-UNKNOWN-GROUP",
                            "sku": "SKU-DOES-NOT-EXIST",
                            "approved_price": 500,
                            "actual_price": 500,
                        }
                    ],
                },
            )
        except ImportError as exc:  # pragma: no cover
            pytest.skip(f"Framework not installed in CI: {exc}")

        assert isinstance(result, dict)
        # Gap-B fix: detect runs once and finds nothing -> step-0 terminate -> finalize
        # routes to SUCCESS (natural termination, well below max_iterations). AgentStatus
        # is a str-enum, so AgentStatus.SUCCESS == "success" whether the framework returns
        # the enum member or the bare string.
        if "status" in result:
            assert result["status"] == AgentStatus.SUCCESS
        # The run terminated gracefully and assembled the zero-discrepancy report,
        # surfaced via invoke() (Gap A). It is now on the SUCCESS path (finalize ran),
        # so the report IS present.
        report_holder = _report(result)
        assert "compliance_report" in report_holder, (
            f"zero-discrepancy compliance_report not surfaced; keys={sorted(result.keys())}, "
            f"output={result.get('output')}"
        )
        assert report_holder["compliance_report"]["discrepancies_total"] == 0
