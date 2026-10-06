# RET-C3-010 — Unit Tests: PriceCheckStubLLM (deterministic v1 stub LLM)
#
# Asserted against the source-review fixes (Gaps A & B) + the wheel-confirmed
# Cat-3 contract:
#   src/services/stub_llm.py — deterministic BaseLLM stub.
#     complete(messages) parses the build_think_prompt() summary lines + the embedded
#     STATE_JSON blob and emits the next scripted tool_call following the policy:
#       0. detect ran (detect_ran True) + found nothing (discrepancies == 0, dispatched == 0)
#          -> [] (terminate, graceful SUCCESS)
#       1. detect not yet run (detect_ran False)  -> detect_discrepancy
#       2. dispatched < discrepancies             -> dispatch_correction
#       3. verified  < dispatched                 -> verify_correction
#       4. unresolved > 0                         -> escalate
#       5. all resolved/escalated                 -> [] (terminate)
#     Each non-terminal tool call carries a reconstructed `state` dict in its args — the
#     framework invokes tools as tool_fn(**tool_args) and never passes the graph state, so
#     the stub relays it; the tools resolve their target from that dict.
#     bind_tools(tools) returns self (BaseLLM contract).
#     stream(messages) yields the full content in one chunk.
#     Response shape: {"content", "tool_calls", "model", "usage"}.
#
# detect_ran discriminator (wheel-confirmed): the running domain picture lives in
# state["tool_results"] (the only channel that survives the loop); build_think_prompt
# reconstructs it and emits `detect ran: <bool>`. detect_ran is False on iteration 0
# (before detect) and True only AFTER a detect_discrepancy result is recorded — a true
# "detect has run" signal, replacing the earlier fragile price_records>0 proxy.
#
# The stub LLM imports only shared.services.llm.base_llm.BaseLLM — wrapped in an
# ImportError -> skip so a bare local checkout (no SDK wheel) still collects green.

import json

import pytest

try:
    from src.services.stub_llm import PriceCheckStubLLM

    _STUB_IMPORTABLE = True
except ImportError:  # pragma: no cover — SDK wheel absent (bare local checkout)
    _STUB_IMPORTABLE = False

pytestmark = pytest.mark.skipif(
    not _STUB_IMPORTABLE,
    reason="BaseLLM (shared.services.llm) not installed — CI installs the SDK wheel",
)


def _prompt(*, detect_ran=False, price_records=0, discrepancies=0, dispatched=0, verified=0, escalated=0, unresolved=0):
    """Reconstruct the summary block + STATE_JSON line build_think_prompt() emits.

    The stub's step-0 terminate branch keys on `detect ran: <bool>` (True only after detect
    has run — its result is recorded in the accumulated tool_results). Pass detect_ran=True
    with discrepancies=0/dispatched=0 to exercise step-0. STATE_JSON carries list stand-ins
    of the right lengths so the relayed `state` dict the stub passes to tools is well-formed.
    """

    def _stub_list(n, **fields):
        return [dict(fields) for _ in range(n)]

    state_json = json.dumps(
        {
            "detect_ran": detect_ran,
            "price_records": _stub_list(price_records, store_id="S", sku="K", approved_price=100, actual_price=80),
            "discrepancies": _stub_list(discrepancies, store_id="S", sku="K", delta=-50),
            "dispatched_tasks": _stub_list(dispatched, task_id="t", store_id="S", sku="K"),
            "verification_results": _stub_list(verified, task_id="t", store_id="S", sku="K", resolved=False),
            "escalations": _stub_list(escalated, store_id="S", sku="K"),
        }
    )
    return (
        "[RET-C3-010] Price & Promo Compliance Agent — iteration 0\n"
        "Current state:\n"
        f"  detect ran: {detect_ran}\n"
        f"  price_records loaded: {price_records}\n"
        f"  discrepancies found: {discrepancies}\n"
        f"  dispatched: {dispatched}\n"
        f"  verified: {verified}\n"
        f"  escalated: {escalated}\n"
        f"  unresolved (not verified OK, not escalated): {unresolved}\n"
        f"STATE_JSON: {state_json}\n"
    )


def _names(tool_calls):
    return [tc["name"] for tc in tool_calls]


class TestStubLLMResponseShape:
    """The canonical BaseLLM response shape is honoured."""

    def test_complete_returns_canonical_keys(self):
        llm = PriceCheckStubLLM()
        resp = llm.complete(_prompt(detect_ran=False, discrepancies=0))
        assert set(resp.keys()) == {"content", "tool_calls", "model", "usage"}
        assert resp["model"] == "stub/deterministic-v1"
        assert isinstance(resp["tool_calls"], list)

    def test_complete_accepts_message_list(self):
        """complete() accepts a [{role, content}] list (last message used)."""
        llm = PriceCheckStubLLM()
        messages = [{"role": "user", "content": _prompt(detect_ran=False, discrepancies=0)}]
        resp = llm.complete(messages)
        assert _names(resp["tool_calls"]) == ["detect_discrepancy"]


class TestStubLLMPolicy:
    """Deterministic policy: the right tool call per state summary."""

    def test_step1_detect_not_run_calls_detect(self):
        """detect_ran False (detect not yet run) -> detect_discrepancy."""
        llm = PriceCheckStubLLM()
        resp = llm.complete(_prompt(detect_ran=False, discrepancies=0))
        assert _names(resp["tool_calls"]) == ["detect_discrepancy"]
        assert resp["content"] == ""  # non-empty tool_calls -> no final content

    def test_step0_detect_ran_found_nothing_terminates(self):
        """detect ran (detect_ran True) but found zero discrepancies -> terminate.

        Gap-B fix (wheel-confirmed): once detect_discrepancy has run (its result is recorded
        in the accumulated tool_results, so build_think_prompt emits `detect ran: True`) and
        found nothing (discrepancies == 0, dispatched == 0), complete() returns empty
        tool_calls so the loop routes to finalize (status SUCCESS) instead of looping detect
        forever to ERROR.
        """
        llm = PriceCheckStubLLM()
        resp = llm.complete(_prompt(detect_ran=True, price_records=3, discrepancies=0, dispatched=0))
        assert resp["tool_calls"] == []
        assert resp["content"]  # non-empty final answer

    def test_step2_undispatched_calls_dispatch(self):
        """discrepancies exist, some undispatched -> dispatch_correction."""
        llm = PriceCheckStubLLM()
        resp = llm.complete(_prompt(detect_ran=True, discrepancies=2, dispatched=0))
        assert _names(resp["tool_calls"]) == ["dispatch_correction"]

    def test_step3_unverified_calls_verify(self):
        """all dispatched, some unverified -> verify_correction."""
        llm = PriceCheckStubLLM()
        resp = llm.complete(_prompt(detect_ran=True, discrepancies=2, dispatched=2, verified=0))
        assert _names(resp["tool_calls"]) == ["verify_correction"]

    def test_step4_unresolved_calls_escalate(self):
        """unresolved items remain -> escalate."""
        llm = PriceCheckStubLLM()
        resp = llm.complete(_prompt(detect_ran=True, discrepancies=2, dispatched=2, verified=2, unresolved=1))
        assert _names(resp["tool_calls"]) == ["escalate"]

    def test_step5_all_done_terminates(self):
        """all resolved/escalated -> empty tool_calls + final content (terminate)."""
        llm = PriceCheckStubLLM()
        resp = llm.complete(_prompt(detect_ran=True, discrepancies=2, dispatched=2, verified=2, unresolved=0))
        assert resp["tool_calls"] == []
        assert resp["content"]  # non-empty final answer signals completion

    def test_tool_call_args_carry_state(self):
        """Each non-terminal tool call relays a `state` dict in its args (framework never

        passes the graph state to tools — the stub reconstructs it from STATE_JSON)."""
        llm = PriceCheckStubLLM()
        resp = llm.complete(_prompt(detect_ran=False, discrepancies=0))
        args = resp["tool_calls"][0]["args"]
        assert "state" in args and isinstance(args["state"], dict)
        # detect reads price_records from the relayed state
        assert "price_records" in args["state"]


class TestStubLLMContract:
    """bind_tools / stream BaseLLM contract."""

    def test_bind_tools_returns_self(self):
        """bind_tools(tools) MUST return self (BaseLLM contract)."""
        llm = PriceCheckStubLLM()
        sentinel = [lambda: None]
        assert llm.bind_tools(sentinel) is llm

    def test_stream_yields_content(self):
        """stream(messages) yields the completion content."""
        llm = PriceCheckStubLLM()
        # terminal state -> non-empty content
        chunks = list(llm.stream(_prompt(detect_ran=True, discrepancies=2, dispatched=2, verified=2, unresolved=0)))
        assert chunks
        assert "".join(chunks).strip()
