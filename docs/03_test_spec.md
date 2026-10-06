# Test Specification

## Test Strategy

- Test types: Unit / Integration (through the HTTP entry point) / Proof-of-Boundary
- The suite runs against the real framework wheel. A stub pass is not a pass.
- Integration tests drive the ASGI application directly, so a defect in the entry point —
  a bad import, a missing trust level, a caller contract that cannot carry the caller's
  data — is visible to the suite rather than only at deploy time.

### Where the tests live

| File | Tests | Covers |
|------|-------|--------|
| `tests/unit/test_graph.py` | 13 | Identity, iteration ceiling, think prompt, report assembly, output gate |
| `tests/unit/test_stub_llm.py` | 11 | The deterministic policy's routing decisions |
| `tests/unit/test_tool_detect_discrepancy.py` | 9 | Discrepancy detection and severity |
| `tests/unit/test_tool_dispatch_correction.py` | 6 | Correction dispatch and target resolution |
| `tests/unit/test_tool_verify_correction.py` | 6 | Verification and target resolution |
| `tests/unit/test_tool_escalate.py` | 6 | Escalation and the price-display-law flag |
| `tests/unit/test_framework_compliance_tc06_tc07.py` | 2 | TC-06 / TC-07 |
| `tests/integration/test_invoke_contract.py` | 25 | The HTTP contract end to end |
| `tests/proof_of_boundary/*` | 22 | The boundaries below |

## Framework Compliance Tests (Mandatory)

| TC-ID | Test | Expected Result | Result |
|-------|------|----------------|--------|
| TC-01 | State contract: flat TypedDict | Type check pass, no Pydantic/dataclass | Pass — `test_state_safety.py` |
| TC-02 | SecurityViolationError fires on invalid input | Error raised | n/a — this template raises no SecurityViolationError; the caller contract refuses at the adapter with `400` (`TestNumericContract`, `TestIdentifierContract`) |
| TC-03 | No JWT/Credential in State | CI `gate-credential-scan`: 0 violations (S-5 enforcement moved to CI by an internal ticket) | Pass — 0 violations |
| TC-04 | InvocationContext via configurable only | Direct access raises error | Pass — the adapter passes `ctx`; State carries no context object |
| TC-05 | S-4: no duplicate lifecycle events in `execute()` | `node_start` / `node_complete` / `node_error` absent from `execute()` body | 0 duplicates |
| TC-06 | S-2: `_security_gate_input()` not overridden (`FunctionNode` subclass) | `TypeError` raised at class definition if overridden (`@final` enforced by framework) | 0 overrides |
| TC-07 | S-3: `_security_gate_output()` not overridden (`FunctionNode` subclass) | `TypeError` raised at class definition if overridden (`@final` enforced by framework) | 0 overrides |
| TC-08 | `required_trust_level` enforced | Insufficient trust → refused | Pass — `test_pb_invoke_order.py`; and at the entry point, `TestAuthentication` (unauthenticated, wrong token, and an empty configured token all refused) |
| TC-09 | S-2: `_extra_security_gate_input()` non-trivial when domain checks needed | Domain-specific input checks execute correctly (e.g. PII scan on additional fields, consent validation, business rules) | n/a — this template registers no nodes; the caller contract is enforced at the adapter, before `invoke()` |
| TC-10 | S-3: `_extra_security_gate_output()` non-trivial when domain checks needed | Domain-specific output checks execute correctly (e.g. nested credential scan, PII re-check, content filtering, preservation verification) | n/a as a node hook — the equivalent gate runs in `get_output()` and is covered by PB-S3-01..03 |
| TC-11 | S-4: at least one domain `emit_trace_event()` inside each `execute()` | Domain event emitted on every invocation path | ≥1 per node; every tool emits one, and the output gate emits one when it withholds |

## Proof-of-Boundary Tests (Mandatory)

| PB-ID | Boundary | Test | Expected Result | Result |
|-------|----------|------|----------------|--------|
| PB-1 | BaseNode → EventEmitter | `emit_trace_event()` fires on every invocation path | No silent failures | Pass |
| PB-2 | State serialization | Post-invoke State is primitives only | No Pydantic/dataclass | Pass |
| PB-3 | Level 2 → External service | Real external service connection | Data retrieved | n/a — this version makes no external call; the tools are deterministic and the reasoning step is a stub |
| PB-4 | Import isolation | No Level 0 imports | AST scan: 0 violations | Pass |
| PB-5 | Checkpoint safety | No JWT/Pydantic in checkpoint | Inspection pass | Pass |
| PB-6 | Invoke execution order | `__call__()`: S-1 trust gate → S-4 `node_start` → S-2 `_security_gate_input` → `execute()` → S-3 `_security_gate_output` → S-4 `node_complete` | Order verified | Pass |
| PB-7 | HITL interrupt propagation | Interrupt crosses the subgraph boundary | n/a — this template does not opt into cross-boundary propagation; ships as a skip stub | Skipped |
| PB-SCOPE | Out-of-scope input | A run with nothing to act on terminates gracefully | Terminates SUCCESS, does not loop to the ceiling | Pass |
| PB-LOOP | Loop termination | The think-act loop always terminates | Reaches finalize within the ceiling | Pass |
| PB-S3-01 | Output gate fires | A credential reaching the report via a tool result is withheld | Report withheld, fixed reason returned | Pass |
| PB-S3-02 | Output gate does not over-reach | A clean report is returned verbatim | Report unchanged | Pass |
| PB-S3-03 | Error envelope | A failed or withheld run publishes closed-set labels only | No released text, no traceback, no source paths, not mistakable for a clean result | Pass |

## Business Logic Tests

| TC-ID | Test | Input | Expected Result | Result |
|-------|------|-------|----------------|--------|
| BL-01 | Discrepancy detection | Approved 498 vs actual 548 | One discrepancy, `delta -50`, severity MEDIUM | Pass |
| BL-02 | Severity threshold | `abs(delta) > 50` | HIGH, else MEDIUM | Pass |
| BL-03 | Compliant record | Approved equals actual | No discrepancy | Pass |
| BL-04 | Output depends on input | 1 record vs 5 records | `discrepancies_total` 1 vs 5 | Pass |
| BL-05 | Full cycle | One discrepancy | Detected, dispatched, verified, escalated; price-display-law flag raised | Pass |
| BL-06 | Unresolved escalation | Verification reports not resolved | Escalated with the risk flag | Pass |
| BL-07 | Non-finite price | `NaN` / `Infinity` / `-Infinity` | Refused, field named, `must_be_finite` | Pass |
| BL-08 | Non-numeric price | String, boolean, null | Refused, `must_be_number` | Pass |
| BL-09 | Out-of-range price | `1e18`, negative | Refused, `out_of_range` | Pass |
| BL-10 | Hostile identifier | SKU containing a newline and a forged numbered step | Refused, `must_be_inert_identifier` | Pass |
| BL-11 | Ordinary identifiers | `SKU-001`, `sku_48210`, a bare barcode | Accepted — the screen does not fire on real domain values | Pass |
| BL-12 | Credential on the context channel | A bearer-shaped value | Refused, field named, value never echoed | Pass |
| BL-13 | Ordinary text on the same field | A plain note | Accepted | Pass |
| BL-14 | Structural caps | 501 records; unknown and missing record fields; oversize request text | Refused | Pass |
| BL-15 | Deploy payload | `deploy/invoke_payload.json` | Equals the test fixture, and is accepted by the live entry point | Pass |

## Test Execution Summary

- Total tests: 113
- Pass: 111 / Fail: 0 / Skip: 2
- Both skips are PB-7's two cases: `config/config.yaml` does not set `hitl.enabled`,
  so there is no cross-boundary interrupt behaviour to assert.
