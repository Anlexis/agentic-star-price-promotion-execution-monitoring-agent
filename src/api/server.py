"""Standalone HTTP entry point for the agent.

Entry points are adapters only — no business logic here. For platform-level
routing, the gateway calls agent.invoke() directly.

This adapter owns four things the graph cannot own:

  1. **Authentication and trust.** The declared required_trust_level is
     VERIFIED_EXTERNAL. Nothing upstream sets ``request.state.trust_level`` on
     the standalone path, so a caller would otherwise be ANONYMOUS and every
     node would refuse before running. A bearer token matching
     ``INVOKE_AUTH_TOKEN`` (or the staging runner credential) raises the caller to
     VERIFIED_EXTERNAL; anything else stays ANONYMOUS and is rejected here with
     401 rather than as an opaque refusal deeper in.

  2. **The caller-data contract.** The agent computes its report from
     ``input_context["price_records"]``. Those records are caller data: every
     field is validated against explicit bounds, numbers must be finite, and the
     identifier fields that render into the report are locked to an inert
     pattern. Rejections name the FIELD, never the value.

  3. **A credential pre-screen.** ``input_context`` is returned verbatim by the
     framework's first node into its own result, where the mandatory output gate
     scans it — so a credential-shaped value anywhere in it kills the run at node
     one with a traceback the caller cannot act on. The request cannot succeed
     either way, so it is refused here, naming the field.

  4. **Secret-provider namespace.** It must match the manifest's ``namespace``.
"""

import os
import re
import secrets as _secrets
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from framework.secrets.context import bound_secrets
from framework.security.credential_detector import detect_credentials_in_value
from shared.secrets import factory as secrets_factory
from src.graph.graph import PricePromoCorrectionAgent

# ── Caller-data bounds ────────────────────────────────────────────────────────
# Structural caps: without them a caller sets the size of our work and of the
# rendered report.
_MAX_PRICE_RECORDS: int = 500
_MAX_USER_INPUT_CHARS: int = 4_000
_MAX_CONTEXT_KEYS: int = 8

# store_id / sku render into the compliance report, so they are locked to an
# inert identifier alphabet. Free text here is caller-controlled output
# injection: a newline in a SKU is enough to forge a numbered step in a report.
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

# Shelf prices. Bounded on both sides; bool is rejected explicitly because
# `isinstance(True, int)` is True in Python and `True` would otherwise be a price.
_MIN_PRICE: float = 0.0
_MAX_PRICE: float = 100_000_000.0

_ALLOWED_RECORD_FIELDS: frozenset[str] = frozenset({"store_id", "sku", "approved_price", "actual_price"})

app = FastAPI(title="Agent")

agent: PricePromoCorrectionAgent = PricePromoCorrectionAgent()
agent.compile()
# namespace MUST equal the manifest's `namespace` (lower-case industry).
agent.provision_secrets(secrets_factory(namespace="ret", agent_name="ret_c3_010"))


class InvokeRequest(BaseModel):
    input: str = Field(min_length=1, max_length=_MAX_USER_INPUT_CHARS)
    session_id: str = ""
    # The caller's price records travel here. Declared explicitly so unknown keys
    # can be DROPPED rather than ignored — an ignored key still reaches the
    # framework's first node and detonates the run.
    input_context: dict[str, Any] = Field(default_factory=dict)


def _reject(field: str, problem: str) -> "HTTPException":
    """400 naming the FIELD and a closed-set problem — never the rejected value.

    400, not 422: pydantic owns 422 and answers there with a list of error
    objects, so reusing it would make client handling ambiguous.
    """
    return HTTPException(status_code=400, detail={"field": field, "problem": problem})


def _finite_price(value: Any, field: str) -> float:
    """Parse a caller price: finite, in range, not a bool.

    NaN and +/-Infinity parse cleanly through float() and arrive intact via raw
    JSON, and every comparison against NaN is False — so an unchecked non-finite
    price fails OPEN on exactly the comparison this agent exists to make.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _reject(field, "must_be_number")
    numeric = float(value)
    if numeric != numeric or numeric in (float("inf"), float("-inf")):
        raise _reject(field, "must_be_finite")
    if not (_MIN_PRICE <= numeric <= _MAX_PRICE):
        raise _reject(field, "out_of_range")
    return numeric


def _identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER_RE.match(value):
        raise _reject(field, "must_be_inert_identifier")
    return value


def _screen_credentials(context: dict[str, Any]) -> None:
    """Refuse a credential-shaped value on the context channel, naming the field.

    Iterating top-level fields is exactly equivalent to scanning the whole dict:
    the framework defines detect_credentials_in_value(dict) as the union over its
    values. That identity is what lets this name the offending field without
    widening or narrowing the framework's block set.

    Field NAMES are caller data too, so a name is only echoed when it is itself
    inert and trips no pattern.
    """
    for index, (key, value) in enumerate(context.items()):
        if detect_credentials_in_value(value):
            safe = (
                f"input_context.{key}"
                if _IDENTIFIER_RE.match(str(key)) and not detect_credentials_in_value(str(key))
                else f"input_context field #{index}"
            )
            raise _reject(safe, "credential_shaped_value")


def _validate_price_records(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise _reject("input_context.price_records", "must_be_list")
    if len(raw) > _MAX_PRICE_RECORDS:
        raise _reject("input_context.price_records", "too_many_entries")

    records: list[dict[str, Any]] = []
    for index, entry in enumerate(raw):
        where = f"input_context.price_records[{index}]"
        if not isinstance(entry, dict):
            raise _reject(where, "must_be_object")
        unknown = set(entry) - _ALLOWED_RECORD_FIELDS
        if unknown:
            raise _reject(where, "unknown_fields")
        missing = _ALLOWED_RECORD_FIELDS - set(entry)
        if missing:
            raise _reject(where, "missing_fields")
        records.append(
            {
                "store_id": _identifier(entry["store_id"], f"{where}.store_id"),
                "sku": _identifier(entry["sku"], f"{where}.sku"),
                "approved_price": _finite_price(entry["approved_price"], f"{where}.approved_price"),
                "actual_price": _finite_price(entry["actual_price"], f"{where}.actual_price"),
            }
        )
    return records


def _build_context(raw: dict[str, Any]) -> dict[str, Any]:
    """Return a context carrying ONLY validated, declared keys.

    Unknown keys are DROPPED, not ignored. A validator that merely ignores an
    undeclared key leaves it in state, where the framework's first node returns it
    verbatim into its own result and the mandatory output gate scans it — so an
    undeclared key is not harmless, it is the failure mode.
    """
    if not isinstance(raw, dict):
        raise _reject("input_context", "must_be_object")
    if len(raw) > _MAX_CONTEXT_KEYS:
        raise _reject("input_context", "too_many_keys")
    _screen_credentials(raw)
    return {"price_records": _validate_price_records(raw.get("price_records", []))}


def _authenticate(request: Request) -> TrustLevel:
    """Resolve the caller's trust level from the bearer token.

    Two credentials are accepted because the staging harness presents its own
    runner token for entries it drives directly, while ordinary callers present
    INVOKE_AUTH_TOKEN. Comparison is constant-time. Both unset means no caller
    can be raised above ANONYMOUS, which the manifest's declared level refuses —
    that is the intended closed default, not an outage.
    """
    header = request.headers.get("authorization", "")
    scheme, _, presented = header.partition(" ")
    if scheme.lower() != "bearer" or not presented:
        return TrustLevel.ANONYMOUS
    for expected in (os.environ.get("INVOKE_AUTH_TOKEN"), os.environ.get("STG_INTERNAL_RUNNER_TOKEN")):
        if expected and _secrets.compare_digest(presented, expected):
            return TrustLevel.VERIFIED_EXTERNAL
    return TrustLevel.ANONYMOUS


@app.post("/invoke")
async def invoke(req: InvokeRequest, request: Request) -> dict[str, Any]:
    trust_level = getattr(request.state, "trust_level", None) or _authenticate(request)
    if trust_level == TrustLevel.ANONYMOUS:
        raise HTTPException(status_code=401, detail={"problem": "unauthenticated"})

    input_context = _build_context(req.input_context)

    with bound_secrets(agent._secrets_provider):
        ctx = InvocationContext(
            session_id=req.session_id or str(uuid4()),
            caller_trust_level=trust_level,
            caller_id=getattr(request.state, "caller_id", ""),
        )
        envelope: dict[str, Any] = agent.invoke(req.input, ctx=ctx, input_context=input_context)
        return envelope


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "agent": "ret_c3_010"}
