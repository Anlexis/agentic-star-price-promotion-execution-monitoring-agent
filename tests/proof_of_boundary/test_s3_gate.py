# RET-C3-010 — Proof-of-Boundary: the mandatory output gate
#
# A Cat-3 loop surfaces a freely-assembled report, so the output gate is the last
# line between an assembled value and the caller. In this template the report is
# assembled in PricePromoCorrectionAgent.get_output(), which is where the gate
# runs — the framework's per-node output-gate hook is defined on the node classes,
# and this template registers no nodes of its own, so a gate declared on the graph
# under that name would never be called by anything.
#
#   PB-S3-01  the gate fires on a full invoke — a credential reaching the report
#             through the tool-result channel is withheld from the caller.
#   PB-S3-02  the gate does not swallow valid output — a clean report is returned
#             verbatim.
#   PB-S3-03  a withheld or failed run publishes closed-set labels only: no
#             released text, no traceback, no source paths, and nothing that could
#             be mistaken for a clean compliance result.
#
# Faults are injected on the DATA path (a seeded tool result), never by mutating
# the gate itself. Construction / compile / invoke are framework-dependent, so they
# are wrapped in ImportError -> pytest.skip. The deterministic stub LLM is the
# default, so no model credential is needed.

import pytest

_CREDENTIAL_TOKENS = ("token", "secret", "password", "credential")

# Assembled at runtime. A credential-shaped literal committed in a fixture is the
# exact defect the blocking whole-tree credential gate exists to catch.
_FAKE_BEARER = "Bearer " + "abcdefghij" * 3


def _build_agent(max_iterations=20):
    try:
        from src.graph.graph import PricePromoCorrectionAgent
    except ImportError as exc:  # pragma: no cover — SDK wheel absent
        pytest.skip(f"Framework not installed in CI: {exc}")
    return PricePromoCorrectionAgent(config={"max_iterations": max_iterations})


def _success_state(tool_results):
    """A terminal SUCCESS state carrying the given accumulated tool results."""
    from framework.schemas.agent_status import AgentStatus

    return {
        "status": AgentStatus.SUCCESS.value,
        "tool_results": tool_results,
        "input_context": {"price_records": []},
        "iterations": 3,
        "node_history": ["AutonomousInitializeNode", "ThinkNode", "FinalizeNode"],
    }


def _assert_no_credential_keys(obj):
    """Recursively assert no dict key in obj contains a credential token."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            low = str(key).lower()
            assert not any(tok in low for tok in _CREDENTIAL_TOKENS), f"credential-shaped key reached the caller: {key}"
            _assert_no_credential_keys(value)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            _assert_no_credential_keys(item)


def _walk_strings(obj):
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield str(key)
            yield from _walk_strings(value)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            yield from _walk_strings(item)
    elif isinstance(obj, str):
        yield obj


class TestPbS3GateFiresOnOutput:
    """PB-S3-01: the gate runs on the assembled report of a real run."""

    def test_no_credential_key_in_invoke_output(self):
        """A full think->act->finalize run returns no credential-shaped key."""
        agent = _build_agent()
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
        except ImportError as exc:  # pragma: no cover
            pytest.skip(f"Framework not installed in CI: {exc}")

        assert isinstance(result, dict)
        _assert_no_credential_keys(result)

    def test_credential_value_in_tool_result_is_withheld(self):
        """A credential VALUE riding a tool result never reaches the caller.

        This is the live channel: the report is reconstructed from tool_results, so
        anything a tool returns is assembled into it. The fault is injected in the
        tool result, not in the gate.
        """
        agent = _build_agent()
        state = _success_state(
            [
                {
                    "tool": "escalate",
                    "args": {},
                    "result": {
                        "store_id": "S001",
                        "sku": "SKU-001",
                        "keihyo_risk": True,
                        "escalated_at": "2026-06-28T00:00:00+00:00",
                        "upstream_note": _FAKE_BEARER,
                    },
                },
            ]
        )
        out = agent.get_output(state)

        assert _FAKE_BEARER not in "\n".join(_walk_strings(out))
        assert out["output"] == {"reason": "output_withheld_by_policy"}
        assert out["status"] == "error"

    def test_credential_shaped_key_in_tool_result_is_withheld(self):
        """The framework detector scans values only, so the KEY screen is ours."""
        agent = _build_agent()
        state = _success_state(
            [
                {
                    "tool": "escalate",
                    "args": {},
                    "result": {
                        "store_id": "S001",
                        "sku": "SKU-001",
                        "keihyo_risk": False,
                        "session_token": "irrelevant-value",
                    },
                },
            ]
        )
        out = agent.get_output(state)

        _assert_no_credential_keys(out)
        assert out["output"] == {"reason": "output_withheld_by_policy"}
        assert out["status"] == "error"


class TestPbS3DoesNotSwallowValidOutput:
    """PB-S3-02: a clean report passes through untouched."""

    def test_clean_report_unchanged(self):
        """No credential content -> the assembled report is returned verbatim."""
        agent = _build_agent()
        state = _success_state(
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
                            },
                        ],
                        "checked_count": 1,
                    },
                },
                {
                    "tool": "escalate",
                    "args": {},
                    "result": {
                        "store_id": "S001",
                        "sku": "SKU-001",
                        "keihyo_risk": True,
                        "escalated_at": "2026-06-28T00:00:00+00:00",
                    },
                },
            ]
        )
        out = agent.get_output(state)

        assert out["status"] == "success"
        report = out["output"]["compliance_report"]
        assert report["discrepancies_total"] == 1
        assert report["escalated_total"] == 1
        assert report["keihyo_risk_total"] == 1


class TestPbS3ErrorEnvelopeIsClosedSet:
    """PB-S3-03: a failed or withheld run publishes closed-set labels only."""

    _SENTINEL = "upstream said {'customer':'A. Tanaka','ref':'CASE-4471'}"

    def test_failed_run_publishes_no_node_authored_text(self):
        """error_log content — including a framework traceback — is never projected."""
        agent = _build_agent()
        state = {
            "status": "error",
            "error_log": [
                f"boom: {self._SENTINEL}",
                'Traceback (most recent call last):\n  File "/srv/app/framework/nodes/base_node.py", line 197',
            ],
            "tool_results": [],
            "input_context": {"price_records": []},
            "thoughts": ["internal reasoning that must not ship"],
            "artifacts": [{"blob": "internal"}],
        }
        out = agent.get_output(state)
        blob = "\n".join(_walk_strings(out))

        assert self._SENTINEL not in blob
        assert "Traceback" not in blob
        assert "/srv/app" not in blob
        assert "internal reasoning that must not ship" not in blob
        assert out["output"] == {"reason": "compliance_run_failed"}

    def test_failed_run_is_not_mistakable_for_a_clean_result(self):
        """A failed run must NOT publish an all-zero, report-shaped object.

        Before the gate moved here, an ERROR invoke still returned a fully-formed
        compliance_report of zeroes — indistinguishable, field for field, from a
        genuine clean run over a compliant store.
        """
        agent = _build_agent()
        out = agent.get_output(
            {
                "status": "error",
                "tool_results": [],
                "input_context": {"price_records": []},
                "error_log": ["x"],
            }
        )
        assert "compliance_report" not in out["output"]

    def test_contained_output_stays_truthy(self):
        """A falsy output would re-open the framework's `final_output or result` fallback."""
        agent = _build_agent()
        out = agent.get_output(
            {
                "status": "error",
                "tool_results": [],
                "input_context": {"price_records": []},
                "final_output": {"leaked": "inner answer"},
                "result": {"leaked": "inner answer"},
            }
        )
        assert out["output"]
        assert "leaked" not in "\n".join(_walk_strings(out))

    @pytest.mark.parametrize("reason", ["compliance_run_failed", "output_withheld_by_policy"])
    def test_every_reason_is_a_module_constant(self, reason):
        """Every caller-visible reason is declared in the module, not composed."""
        import src.graph.graph as graph_module

        declared = {value for name, value in vars(graph_module).items() if name.startswith("_REASON_")}
        assert reason in declared
