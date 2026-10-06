# RET-C3-010 — Unit Tests: PricePromoCorrectionAgent (Cat-3 AutonomousBaseGraph)
#
# Asserted against the source-review fixes (Gaps A & B) + the wheel-confirmed
# Cat-3 state/tool contract:
#   src/graph/graph.py — PricePromoCorrectionAgent(AutonomousBaseGraph).
#     - max_iterations: int  reads config["max_iterations"], default 20
#     - state_schema: type    returns the domain State subclass
#     - get_tools() -> list   returns list(TOOL_REGISTRY.values()) == 4 callables
#     - build_think_prompt(state) -> str  reconstructs the domain picture from
#       state["tool_results"] (+ input_context) and emits the counts + a STATE_JSON line
#     - assemble_output(state) -> dict  reconstructs the report from state["tool_results"]
#       and returns the domain report dict DIRECTLY (compliance_report, keihyo_risk_flags,
#       escalations, ...) — NOT wrapped under "final_output". It is invoked from
#       get_output(), not by the framework FinalizeNode (a plain instance that never calls
#       it — wheel-confirmed).
#     - get_output(state) -> dict  assembles the report, runs the output gate over it,
#       and places it under the envelope "output" key so
#       result["output"]["compliance_report"] is reachable. A failed run, or a report
#       tripping the credential screen, yields a closed-set reason instead.
#
# ── Wheel-confirmed state/tool contract ──────────────────────────────────────────
# The framework invokes tools as tool_fn(**tool_args) (ToolActNode) — tools never receive
# the graph state, and ToolActNode merges only {tool_calls, tool_results} back. Autonomous
# State's tool_results carries an operator.add reducer (it ACCUMULATES); it is the only
# domain channel that survives the loop. Domain keys (discrepancies, dispatched_tasks, …)
# are NOT auto-propagated. So assemble_output()/build_think_prompt() reconstruct from
# state["tool_results"]; these unit tests seed tool_results entries accordingly.
#
# This is a Cat-3 ReAct template (AutonomousBaseGraph), NOT a nested Cat-2 graph.
# Loop termination is framework-owned (_route_after_think): empty tool_calls /
# status SUCCESS / iterations >= max_iterations / status ERROR all route to finalize.
#
# Framework-dependent construction (AutonomousBaseGraph.__init__, .compile(),
# .invoke()) is wrapped in ImportError -> pytest.skip: the SDK wheel may not be
# importable in the bare unit stage; CI installs it. The stub LLM is passed at
# construction because _validate_config() hard-fails without config["llm"] +
# config["budget_usd"].
#
# Cat-3 build/invoke contract (verified against the framework wheel source —
# framework.graph.base_graph.BaseGraph / AutonomousBaseGraph, and the merged
# src/api/server.py adapter):
#   * compile() compiles IN-PLACE and returns None (it sets self._compiled).
#     It does NOT return a runnable, so `agent.compile().invoke(...)` would hit
#     'NoneType' object has no attribute 'invoke'.
#   * invoke() is a method ON THE AGENT:
#       invoke(user_input: str, session_id="", ctx=None, input_context=None) -> dict
#     It takes the task as a positional string and the domain payload via
#     input_context (surfaced into state["input_context"]); it returns the
#     agent's output dict.
#   So the real pattern is: construct -> agent.compile() -> agent.invoke(text, input_context=...).
#
# ── invoke() result-envelope contract (verified against the released wheel; CI
#    trace) ──────────────────────────────────────────────────────────────────────
# AutonomousBaseGraph.get_output() returns a FIXED-KEY envelope:
#   {output, status, trace_id, correlation_id, node_history, artifacts, thoughts, cost}.
# RET-C3-010 OVERRIDES get_output() (Gap A) to set output = assemble_output(state) —
# the framework's plain FinalizeNode never calls assemble_output(), so the override is
# how the domain report reaches the caller. compliance_report therefore lands at
# result["output"]["compliance_report"]. TestLoopTermination asserts this end-to-end;
# TestAssembleOutput asserts the assemble_output() return shape directly. _surfaced()
# below probes the envelope tolerantly so the assertion is robust to the holder key.

import pytest

from src.schemas.state import State
from src.tools import TOOL_REGISTRY


def _build_agent(max_iterations=20, budget_usd=2.50):
    """Construct the agent with the deterministic stub LLM, or skip if the wheel is absent."""
    try:
        from src.graph.graph import PricePromoCorrectionAgent
        from src.services.stub_llm import PriceCheckStubLLM
    except ImportError as exc:  # pragma: no cover — SDK wheel absent
        pytest.skip(f"Framework not installed in CI unit stage: {exc}")

    return PricePromoCorrectionAgent(
        config={
            "llm": PriceCheckStubLLM(),
            "budget_usd": budget_usd,
            "max_iterations": max_iterations,
        }
    )


def _tool_result(tool, result):
    """Build one accumulated tool_results entry as the framework records it."""
    return {"tool": tool, "result": result, "args": {}}


def _surfaced(result):
    """Return the invoke()-envelope holder that carries the assemble_output() payload.

    After the Gap-A fix, get_output() sets result["output"] = assemble_output(state),
    which is the domain report dict directly (compliance_report at the top of that
    dict). Probe "output" then "final_output" (tolerant across wheel versions) and
    return the first dict containing compliance_report; fall back to result itself.
    """
    for key in ("output", "final_output"):
        holder = result.get(key) if isinstance(result, dict) else None
        if isinstance(holder, dict) and "compliance_report" in holder:
            return holder
    if isinstance(result, dict) and "compliance_report" in result:
        return result
    return result


class TestAgentConfig:
    """Identity / iteration ceiling / state schema."""

    def test_max_iterations_from_config(self):
        """config["max_iterations"] = 5 -> agent.max_iterations == 5."""
        agent = _build_agent(max_iterations=5)
        assert agent.max_iterations == 5

    def test_max_iterations_default(self):
        """No config override -> agent.max_iterations == 20 (module default)."""
        try:
            from src.graph.graph import PricePromoCorrectionAgent
            from src.services.stub_llm import PriceCheckStubLLM
        except ImportError as exc:  # pragma: no cover
            pytest.skip(f"Framework not installed in CI unit stage: {exc}")

        # Omit max_iterations -> falls back to the module default of 20.
        agent = PricePromoCorrectionAgent(config={"llm": PriceCheckStubLLM(), "budget_usd": 2.50})
        assert agent.max_iterations == 20

    def test_state_schema_is_state(self):
        """agent.state_schema is the domain State subclass."""
        agent = _build_agent()
        assert agent.state_schema is State


class TestAgentTools:
    """The Cat-3 tool registry is exactly the 4 domain tools."""

    def test_get_tools_returns_all_four(self):
        """get_tools() returns a list of 4 callables."""
        agent = _build_agent()
        tools = agent.get_tools()
        assert isinstance(tools, list)
        assert len(tools) == 4
        assert all(callable(t) for t in tools)

    def test_get_tools_matches_registry(self):
        """get_tools() returns exactly the TOOL_REGISTRY values."""
        agent = _build_agent()
        assert agent.get_tools() == list(TOOL_REGISTRY.values())


class TestAssembleOutput:
    """Terminal-state output shaping (assemble_output called DIRECTLY).

    NOTE: this is where the compliance_report contract is actually verified —
    assemble_output(state) is invoked directly here. After the Gap-A fix the method returns
    the domain report dict DIRECTLY (compliance_report at the top level, NOT under
    "final_output"); get_output() is what places that dict under the invoke() envelope's
    "output" key. Wheel-confirmed: the report is reconstructed from state["tool_results"]
    (domain state keys do not survive the loop), so the terminal state is seeded with the
    accumulated detect/dispatch/verify/escalate result entries.
    """

    def test_assemble_output_keys(self):
        """assemble_output(state) returns compliance_report, keihyo_risk_flags, escalations directly."""
        agent = _build_agent()
        state = {
            "tool_results": [
                _tool_result(
                    "detect_discrepancy",
                    {
                        "discrepancies": [{"store_id": "S001", "sku": "SKU-001", "delta": -50}],
                        "checked_count": 1,
                    },
                ),
                _tool_result(
                    "dispatch_correction",
                    {
                        "task_id": "t1",
                        "store_id": "S001",
                        "sku": "SKU-001",
                        "status": "DISPATCHED",
                    },
                ),
                _tool_result(
                    "verify_correction",
                    {
                        "task_id": "t1",
                        "store_id": "S001",
                        "sku": "SKU-001",
                        "resolved": True,
                    },
                ),
            ],
            "iterations": 4,
        }
        out = agent.assemble_output(state)
        # Gap-A fix: payload is returned DIRECTLY (not nested under final_output).
        for key in ("compliance_report", "keihyo_risk_flags", "escalations"):
            assert key in out
        assert "final_output" not in out
        # compliance_report is a rolled-up dict reflecting the terminal counts.
        assert out["compliance_report"]["discrepancies_total"] == 1
        assert out["compliance_report"]["resolved_total"] == 1

    def test_assemble_output_empty_state_safe(self):
        """assemble_output tolerates an empty terminal state (no KeyError)."""
        agent = _build_agent()
        out = agent.assemble_output({})
        assert out["compliance_report"]["discrepancies_total"] == 0
        assert out["keihyo_risk_flags"] == []
        assert out["escalations"] == []


class TestBuildThinkPrompt:
    """The think prompt carries the discrepancy summary the stub LLM parses.

    The prompt counts are reconstructed from state["tool_results"] (wheel-confirmed), so the
    state is seeded with a detect result entry rather than a top-level discrepancies key.
    """

    def test_build_think_prompt_includes_discrepancies(self):
        """Prompt string contains the discrepancy data / counts (from tool_results)."""
        agent = _build_agent()
        state = {
            "input_context": {"price_records": [{"store_id": "S001", "sku": "SKU-001"}]},
            "tool_results": [
                _tool_result(
                    "detect_discrepancy",
                    {
                        "discrepancies": [{"store_id": "S001", "sku": "SKU-001", "delta": -50, "severity": "MEDIUM"}],
                        "checked_count": 1,
                    },
                ),
            ],
        }
        prompt = agent.build_think_prompt(state)
        assert isinstance(prompt, str)
        assert "discrepancies found: 1" in prompt
        assert "detect ran: True" in prompt
        assert "detect_discrepancy" in prompt
        assert "STATE_JSON:" in prompt


class TestOutputGate:
    """The output gate runs inside get_output(), which is what BaseGraph.invoke calls."""

    @staticmethod
    def _state(tool_results, status="success"):
        return {
            "status": status,
            "tool_results": tool_results,
            "input_context": {"price_records": []},
            "iterations": 2,
        }

    def test_credential_value_is_withheld(self):
        """A credential arriving via a tool result is withheld from the caller."""
        agent = _build_agent()
        fake = "Bearer " + "abcdefghij" * 3
        out = agent.get_output(
            self._state(
                [
                    {"tool": "escalate", "args": {}, "result": {"store_id": "S001", "sku": "SKU-001", "note": fake}},
                ]
            )
        )
        assert out["output"] == {"reason": "output_withheld_by_policy"}
        assert out["status"] == "error"
        assert fake not in str(out)

    def test_credential_shaped_key_is_withheld(self):
        """Key names are screened here — the framework detector scans values only."""
        agent = _build_agent()
        out = agent.get_output(
            self._state(
                [
                    {
                        "tool": "escalate",
                        "args": {},
                        "result": {"store_id": "S001", "sku": "SKU-001", "api_token": "x"},
                    },
                ]
            )
        )
        assert out["output"] == {"reason": "output_withheld_by_policy"}
        assert "api_token" not in str(out)

    def test_clean_report_passes_through(self):
        """The gate does not swallow a clean report."""
        agent = _build_agent()
        out = agent.get_output(
            self._state(
                [
                    {
                        "tool": "detect_discrepancy",
                        "args": {},
                        "result": {
                            "discrepancies": [
                                {
                                    "store_id": "S001",
                                    "sku": "SKU-001",
                                    "approved_price": 498,
                                    "actual_price": 548,
                                    "delta": -50,
                                    "severity": "MEDIUM",
                                }
                            ],
                            "checked_count": 1,
                        },
                    },
                ]
            )
        )
        assert out["status"] == "success"
        assert out["output"]["compliance_report"]["discrepancies_total"] == 1

    def test_error_status_is_contained(self):
        """A failed run publishes a closed-set reason, never a report-shaped object."""
        agent = _build_agent()
        out = agent.get_output(self._state([], status="error"))
        assert out["output"] == {"reason": "compliance_run_failed"}
        assert "compliance_report" not in out["output"]


class TestLoopTermination:
    """The headline Cat-3 test: the think->act loop terminates (no infinite loop)."""

    def test_invoke_terminates_within_max_iterations(self):
        """End-to-end .invoke() reaches finalize and returns the envelope (no hang).

        Real Cat-3 build/invoke: compile() is in-place (returns None); invoke() is
        on the agent and takes a positional task string + input_context payload.
        Asserts the real invoke() contract: the run returns the fixed-key envelope
        with a valid AgentStatus and node_history reaching FinalizeNode, and (after the
        Gap-A get_output() override) surfaces compliance_report at
        result["output"]["compliance_report"].
        """
        agent = _build_agent(max_iterations=20)
        try:
            agent.compile()
            result = agent.invoke(
                "Run compliance check for promo cycle 2026-06-28",
                input_context={
                    "price_records": [
                        {
                            "store_id": "S001",
                            "sku": "SKU-001",
                            "approved_price": 498,
                            "actual_price": 548,
                        }
                    ],
                },
            )
        except ImportError as exc:  # pragma: no cover — SDK wheel absent
            pytest.skip(f"Framework not installed in CI unit stage: {exc}")

        assert isinstance(result, dict)
        # The loop returned (it did not hang) and reported a status.
        assert "status" in result
        try:
            from framework.schemas.agent_status import AgentStatus

            assert result["status"] in {AgentStatus.SUCCESS, AgentStatus.ERROR}
        except ImportError:  # pragma: no cover — SDK wheel absent
            pass
        # node_history must show the loop reached FinalizeNode (termination, not a hang).
        node_history = result.get("node_history", [])
        assert any(
            "Finalize" in str(n) for n in node_history
        ), f"loop did not reach finalize; node_history={node_history}"
        # Gap-A fix: compliance_report is now surfaced via invoke() under result["output"]
        # (get_output() override sets output = assemble_output(state)). Probe tolerantly.
        surfaced = _surfaced(result)
        assert "compliance_report" in surfaced, (
            f"compliance_report not surfaced in invoke() result; keys={sorted(result.keys())}, "
            f"output={result.get('output')}"
        )
        # The detect->dispatch->verify->escalate run found the one seeded discrepancy.
        assert surfaced["compliance_report"]["discrepancies_total"] == 1
