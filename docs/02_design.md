# Template Design Specification

## Position in AgentCore Architecture

- **Agent Class**: `PricePromoCorrectionAgent` (`src/graph/graph.py`)
- **L1 Base**: AutonomousBaseGraph — direct framework inheritance
- **Three-Layer Separation**:
  - State: flat TypedDict composition (no Pydantic — msgpack incompatible)
  - Node: the four loop slots are framework-owned; this template registers none of its own
  - Graph: composition — the template supplies tools, the think prompt and the report

## Architecture Overview

### Node Configuration

This is an autonomous loop, not a fixed pipeline. `register_nodes()` is deliberately
**not** overridden, so the base graph auto-wires all four slots and this template owns
none of them.

| Node | Responsibility | Input State | Output State | Inherits/Overrides |
|------|---------------|-------------|--------------|-------------------|
| initialize | Seed the run; surface `input_context` | `user_input`, `input_context` | `input_context` | AutonomousInitializeNode (default) |
| think | Ask the model what to do next | `tool_results`, `iterations` | `tool_calls`, `thoughts`, `cost_usd` | ThinkNode (default) |
| act | Run the chosen tool | `tool_calls` | `tool_results` (accumulating) | ToolActNode (default) |
| finalize | Close the run | `status` | `status` | FinalizeNode (default) |

The nodes under `src/nodes/` are reference implementations of the node contract and are
**not** wired into this agent. Domain behaviour lives in `src/tools/`.

### Data Flow

```
START → initialize → think ⇄ act → finalize → END
```

The loop ends when the model returns no tool calls, the status reaches SUCCESS or ERROR,
or `max_iterations` is hit — whichever comes first. Termination is framework-owned.

**The only channel that survives the loop is `tool_results`**, which carries an
accumulating reducer. Domain keys declared on the state class are not propagated between
iterations, so the running picture and the final report are both **reconstructed from
`tool_results`** (`reconstruct_domain()`). A tool that wrote to state instead of returning
its result would be invisible to the next iteration.

### Tools

| Tool | When the model calls it | Returns |
|------|------------------------|---------|
| `detect_discrepancy` | First, to compare approved against observed prices | the discrepancy list + count checked |
| `dispatch_correction` | For each discrepancy not yet dispatched | the task record |
| `verify_correction` | For each dispatched task not yet verified | whether the fix landed |
| `escalate` | For anything unresolved | the escalation, with the price-display-law risk flag |

### State Definition

Domain fields extend `AutonomousState`. They document the domain shape; the report is
reconstructed from `tool_results` rather than read from them (see above).

| Field | Type | Purpose | Required |
|-------|------|---------|----------|
| `price_records` | `list[dict]` | Approved-vs-observed records supplied by the caller | yes |
| `discrepancies` | `list[dict]` | Where approved and observed prices disagree | no |
| `dispatched_tasks` | `list[dict]` | Correction tasks raised with stores | no |
| `verification_results` | `list[dict]` | Whether each correction landed | no |
| `escalations` | `list[dict]` | Items escalated to the area manager | no |
| `keihyo_risk_flags` | `list[dict]` | Escalations carrying price-display-law risk | no |
| `compliance_report` | `dict` | Final counts for the cycle | no |
| `step_count` | `int` | Domain mirror of `iterations` | no |

**State Constraints (mandatory):**
- Flat TypedDict only (primitives + JSON-serializable types)
- No JWT, API keys, credentials in State (checkpoint DB leakage)
- InvocationContext via `config["configurable"]` only (not in State)
- No Pydantic models, dataclass, arbitrary Python objects (msgpack incompatible)

## Input and output boundaries

**Input.** `input_context.price_records` is caller data. The adapter validates every field
before the agent runs: prices must be finite, non-bool and within range; `store_id` and
`sku` are restricted to `[A-Za-z0-9_-]{1,32}` because they are rendered into the report,
and free text there would let a caller forge a line in it. Record counts and request size
are capped. Context keys the contract does not declare are dropped rather than ignored —
an ignored key still reaches the first node, whose result the mandatory output gate scans.
Rejections name the field and never echo the value.

**Output.** The report is assembled and gated in `get_output()`, which is what
`BaseGraph.invoke()` calls. The per-node output-gate hook is defined on the node classes,
so a template that registers no nodes cannot use it — placing the gate there would leave
it uncalled.

The stated output invariant is: **the report carries no credential-shaped content, and a
run that fails or is withheld publishes a closed-set reason and nothing else.** The screen
is the union of the framework's own credential detector and local patterns for shapes it
does not carry, plus a key-name screen the detector cannot perform because it scans values
only.

> **No monetary rounding grid applies here.** This agent renders no monetary aggregate —
> the report is counts, and the per-item prices it carries are exact shelf prices whose
> whole purpose is an exact comparison. Rounding them would erase the discrepancies the
> agent exists to find. The invariant enforced at the boundary is the one above.

## Framework Utilization

### Shared Components Used
- [x] InvocationContext (correlation_id, session_id, permissions, credential handle)
      — built by the adapter, carrying the authenticated caller's trust level
- [ ] SecurityViolationError — not raised by this template
- [ ] S-2: `_extra_security_gate_input()` — domain-specific input check hook
      (runs after the default PII scan; implement any domain checks needed — e.g. PII scan
      on additional fields, consent flag validation, input size limits, business rule gates;
      omit if the default framework scan on `user_input` / `validated_input` / `llm_response`
      is sufficient; **MUST NOT override `_security_gate_input()`** — `TypeError` at class definition)
      — not used: this template registers no nodes, and the caller contract is enforced at
      the adapter, before `invoke()`
- [ ] S-3: `_extra_security_gate_output()` — domain-specific output check hook
      (runs after the default credential scan; implement any domain checks needed — e.g.
      credential scan on nested fields, PII re-check on LLM output, content filtering,
      preservation verification; omit if the default scan on result string values is sufficient;
      **MUST NOT override `_security_gate_output()`** — `TypeError` at class definition)
      — not available to this template: the hook belongs to the node classes. The equivalent
      gate runs in `get_output()`, over the assembled report.
- [x] S-4: `emit_trace_event()` — at least one domain-specific event inside each `execute()`
      (**mandatory**; do NOT emit `node_start` / `node_complete` / `node_error` —
      `BaseNode.__call__()` emits these automatically; duplicates corrupt audit trail)
      — every tool emits one, and the output gate emits one when it withholds a report.
      Payloads carry outcome signals and counts only, never message content.

> **S-2/S-3 gate behaviour by node type (ADR-017):**
> - `FunctionNode` subclass → framework `@final` gate always runs automatically;
>   extend via `_extra_security_gate_input()` / `_extra_security_gate_output()` only
> - `GraphNode` / `RemoteAgentNode` → deliberate no-op (upstream or remote node's gate already applied)
> - Custom `BaseNode` subclass → must implement `_security_gate_input()` and
>   `_security_gate_output()` directly (`@abstractmethod` — omission raises `TypeError` at instantiation)

### Composition Pattern

- **Pattern**: Standalone
- **Composition target**: n/a — the agent is deployed as its own endpoint
- **Error propagation strategy**: handle. A failed run is converted at the output boundary
  into a closed-set reason; internal detail stays on the audit channel.

## Import Isolation Confirmation
- [x] Template does not import agenticstar-platform SDK (Level 0)
- [x] Import targets: framework/ and shared/ only (no agents/base/ required)

## Design Decision Record

| Decision | Option A | Option B | Chosen | Rationale |
|----------|----------|----------|--------|-----------|
| L1 base type | AgentBaseGraph | AutonomousBaseGraph | AutonomousBaseGraph | The number of steps is decided during the run, not in advance; a fixed pipeline cannot express "keep going until nothing is unresolved". |
| Composition pattern | Standalone | GraphNode subgraph | Standalone | The agent is the unit of work and is deployed on its own endpoint. |
| Reasoning backend | live model client | deterministic stub | deterministic stub | This version ships a stub so a run needs no model credential and is reproducible. The loop, the tools and the report are unchanged when a real client replaces it. |
| Output gate placement | per-node hook | `get_output()` | `get_output()` | The per-node hook is defined on the node classes; this template registers no nodes, so a gate declared under that name would never be called. |
| Report source | domain state fields | `tool_results` | `tool_results` | It is the only channel with a reducer, so it is the only one that survives an iteration. |
