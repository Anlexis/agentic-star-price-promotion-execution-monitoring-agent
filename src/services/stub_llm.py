"""RET-C3-010 — Deterministic stub LLM for v1 (no live LLM required).

AutonomousBaseGraph hardwires ThinkNode -> llm.complete() every iteration; there is no
deterministic-policy mode in the framework itself. The correct v1 pattern (per the
framework wheel-source review) is therefore a stub BaseLLM that plays a deterministic
role: it parses the think-prompt summary and emits scripted tool_calls in the
detect -> dispatch -> verify -> escalate sequence.

── Wheel-confirmed contract (verified against the framework wheel) ──
The framework calls tools as ``tool_fn(**tool_args)`` (ToolActNode) — the tool gets ONLY
its declared kwargs, and its return value is appended to the framework-accumulated
``state["tool_results"]``. The graph ``state`` is NEVER passed to a tool. So the stub must
pass a state-shaped dict in the `state` arg, reconstructed from the prompt's STATE_JSON
line (build_think_prompt embeds it from the accumulated tool_results + input_context). The
tools then resolve their target (the "pending" placeholders) from that passed-in dict —
their signatures are unchanged.

Policy (keyed on the prompt's counts + the embedded `detect_ran` flag):
  0. detect already ran but found nothing (discrepancies == 0, dispatched == 0)
     -> emit empty tool_calls (terminate, graceful SUCCESS)
  1. detect has not run yet                   -> call detect_discrepancy
  2. discrepancies exist, some undispatched   -> call dispatch_correction
  3. dispatched tasks exist, some unverified  -> call verify_correction
  4. unresolved items remain                  -> call escalate
  5. all resolved or escalated                -> emit empty tool_calls (terminate loop)

# production: replace PriceCheckStubLLM with a real BaseLLM client from
#   shared.services.llm (AnthropicClient / BedrockClient / OpenAIClient). A real LLM reads
#   the same prompt and emits the same tool_calls with concrete args — no state-dict relay.

framework.* / shared.* imports only — no agenticstar.
"""

import json
from typing import Any, Iterator

from shared.services.llm.base_llm import BaseLLM


class PriceCheckStubLLM(BaseLLM):
    """Deterministic stub BaseLLM for RET-C3-010 v1.

    Parses summary context + the STATE_JSON blob from the think prompt and returns scripted
    tool_calls following the detect -> dispatch -> verify -> escalate sequence. Each tool
    call's args include a reconstructed `state` dict (the framework does not pass state to
    tools), from which the tools resolve their target. bind_tools() stores the tools for
    name resolution but is not needed for routing.

    Returns the canonical BaseLLM response shape:
        {"content": str, "tool_calls": list, "model": str, "usage": dict}
    """

    def __init__(self) -> None:
        self._tools: list[Any] = []

    def bind_tools(self, tools: list[Any]) -> "PriceCheckStubLLM":
        self._tools = tools
        return self  # MUST return self (BaseLLM contract)

    def complete(self, messages: Any) -> dict[str, Any]:
        """Deterministic policy: inspect prompt context, emit the next tool call."""
        prompt = (
            messages
            if isinstance(messages, str)
            else (messages[-1].get("content", "") if isinstance(messages, list) and messages else "")
        )

        ctx = self._parse_prompt_context(prompt)
        domain = self._parse_state_json(prompt)
        tool_calls = self._decide_next_action(ctx, domain)

        content = "" if tool_calls else ("All discrepancies resolved or escalated. Compliance run complete.")
        return {
            "content": content,
            "tool_calls": tool_calls,
            "model": "stub/deterministic-v1",
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "model": "stub/deterministic-v1",
            },
        }

    def stream(self, messages: Any) -> Iterator[str]:
        """Stub stream — yields the full content in one chunk."""
        yield self.complete(messages).get("content", "")

    # ── Private helpers ───────────────────────────────────────────────────────

    def _parse_prompt_context(self, prompt: str) -> dict[str, Any]:
        """Extract key numeric context + the detect_ran flag from build_think_prompt()."""
        ctx: dict[str, Any] = {}
        for raw in prompt.splitlines():
            line = raw.strip()
            if line.startswith("detect ran:"):
                ctx["detect_ran"] = line.split(":", 1)[-1].strip().lower() == "true"
            elif "unresolved" in line and "not verified" in line:
                ctx["unresolved"] = _safe_int(line)
            elif "price_records loaded:" in line:
                ctx["price_records"] = _safe_int(line)
            elif "discrepancies found:" in line:
                ctx["discrepancies"] = _safe_int(line)
            elif line.startswith("dispatched:"):
                ctx["dispatched"] = _safe_int(line)
            elif line.startswith("verified:"):
                ctx["verified"] = _safe_int(line)
            elif line.startswith("escalated:"):
                ctx["escalated"] = _safe_int(line)
        return ctx

    def _parse_state_json(self, prompt: str) -> dict[str, Any]:
        """Extract the embedded STATE_JSON blob (the reconstructed domain state).

        Returns a state-shaped dict suitable for passing to a tool's `state` arg. Empty
        dict if the line is absent/malformed (the policy then falls back to step-1 detect).
        """
        for raw in prompt.splitlines():
            line = raw.strip()
            if line.startswith("STATE_JSON:"):
                blob = line[len("STATE_JSON:") :].strip()
                try:
                    parsed: dict[str, Any] = json.loads(blob)
                    return parsed
                except (ValueError, TypeError):
                    return {}
        return {}

    def _decide_next_action(self, ctx: dict[str, Any], domain: dict[str, Any]) -> list[dict[str, Any]]:
        """Emit the next tool call based on the current state summary.

        Returns an empty list when the task is complete. Each non-terminal call carries a
        reconstructed `state` dict in its args (the framework does not pass state to tools);
        the tool resolves its real target from that dict.
        # production: a real LLM emits the same tool calls with concrete args (no state relay).
        """
        detect_ran = ctx.get("detect_ran", bool(domain.get("detect_ran")))
        discrepancies = ctx.get("discrepancies", 0)
        dispatched = ctx.get("dispatched", 0)
        verified = ctx.get("verified", 0)
        unresolved = ctx.get("unresolved", 0)

        # The `state` dict the tools resolve their target from (rebuilt from STATE_JSON).
        tool_state = {
            "price_records": domain.get("price_records", []),
            "discrepancies": domain.get("discrepancies", []),
            "dispatched_tasks": domain.get("dispatched_tasks", []),
            "verification_results": domain.get("verification_results", []),
            "escalations": domain.get("escalations", []),
        }

        # Step 0: detect has run (detect_ran True) and found nothing to act on — zero
        # discrepancies, nothing dispatched. Nothing to dispatch / verify / escalate, so emit
        # empty tool_calls and let the framework route to finalize (status SUCCESS). Without
        # this the unconditional step-1 below would re-issue detect_discrepancy until
        # max_iterations -> ERROR. Out-of-scope / no-discrepancy input terminates gracefully.
        #
        # Discriminator (wheel-confirmed): the running domain picture lives in
        # state["tool_results"] (the only channel that survives the loop). build_think_prompt
        # reconstructs it and emits `detect ran: <bool>`. detect_ran is False on iteration 0
        # (before detect) and True only AFTER detect_discrepancy's result is recorded. So it
        # is a true "detect has run" signal. Happy-path unaffected: when detect finds M>0
        # discrepancies the guard does not fire (discrepancies != 0) and the loop proceeds.
        if detect_ran and discrepancies == 0 and dispatched == 0:
            return []

        # Step 1: nothing detected yet -> detect first. Pass the caller's records explicitly
        # via the `state` arg (detect reads state["price_records"] / input_context).
        if not detect_ran:
            return [{"name": "detect_discrepancy", "args": {"state": tool_state}}]

        # Step 2: discrepancies exist, some not dispatched -> dispatch next. store_id/sku are
        # "pending" placeholders; dispatch_correction resolves the real target from state.
        if dispatched < discrepancies:
            return [
                {
                    "name": "dispatch_correction",
                    "args": {
                        "store_id": "pending",
                        "sku": "pending",
                        "correction": "Update shelf price to approved price",
                        "deadline": "2026-06-26T12:00:00Z",
                        "priority": "HIGH",
                        "state": tool_state,
                    },
                }
            ]

        # Step 3: all dispatched, some not yet verified -> verify next.
        if verified < dispatched:
            return [
                {
                    "name": "verify_correction",
                    "args": {
                        "task_id": "pending",
                        "store_id": "pending",
                        "sku": "pending",
                        "state": tool_state,
                    },
                }
            ]

        # Step 4: unresolved items remain -> escalate.
        if unresolved > 0:
            return [
                {
                    "name": "escalate",
                    "args": {
                        "store_id": "pending",
                        "sku": "pending",
                        "reason": "Correction unresolved after verification deadline",
                        "keihyo_risk": True,
                        "state": tool_state,
                    },
                }
            ]

        # Step 5: all resolved or escalated -> terminate the loop.
        return []


def _safe_int(line: str) -> int:
    """Parse the trailing integer from a 'label: N' summary line; 0 on failure."""
    try:
        return int(line.split(":")[-1].strip())
    except (ValueError, IndexError):
        return 0
