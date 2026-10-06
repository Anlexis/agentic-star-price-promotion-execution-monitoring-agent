# Price & Promotion Execution Monitoring Agent

AI agent for monitoring price and promotion execution across stores, built with Agentic Star.

> **Category**: Cat 3 (Autonomous think-act-observe loop)
> **Industry**: Retail
> **Template ID**: RET-C3-010

## Overview

An autonomous agent that checks whether approved price and promotion changes have
actually been applied in each store. Given an approved price list and the prices
observed at the shelf or till, it works through the run itself: it detects where the
two disagree, raises a correction task for the store responsible, re-checks whether
the correction landed, and escalates anything still unresolved — flagging items whose
mis-priced state carries price-display-law risk if it survives to the promotion
launch. It returns a compliance report for the cycle: how many discrepancies were
found, dispatched, resolved, escalated, and flagged.

Unlike a fixed pipeline, the number of steps is not known in advance. The agent keeps
deciding what to do next until nothing is left to act on, bounded by an iteration
ceiling and a cost ceiling.

This is an agent template built with the **AGENTIC STAR** development platform and the
**AgentCore Framework**. It is intended to be taken as a starting point: fork it, adapt it to
your own data and policies, and run it inside your own AGENTIC STAR deployment.

## Requirements

**This template does not run standalone.** It requires:

| Requirement | Notes |
|---|---|
| **AGENTIC STAR platform** | The agent connects to the platform at start-up. Without it, start-up fails immediately (see *Behaviour without the platform* below). Deployment guides and API documentation: [AGENTIC STAR Developers](https://developers.fd.agenticstar.tm.softbank.jp/) |
| **AgentCore Framework** (`agenticstar-agentcore`) | Installed from PyPI as a dependency. |
| Python | >=3.11 |

```bash
pip install -e .
```

### Behaviour without the platform

The framework is designed to run on AGENTIC STAR. The test suite and an in-process
invoke work standalone — this version reasons with a deterministic stub in place of a
language model, so a run needs no model credential and produces the same result every
time. A production deployment is a different matter: it depends on platform facilities
that exist only there — agent-registry loading via `config/agent.yaml`, gateway
authentication, and secret provisioning. Deploying elsewhere is unsupported.

## Quick Start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests/ -v
```

Tests run without a platform connection. Running the agent in production does not.

## Calling it

`POST /invoke` takes the request text and the records to check. The bearer token must
match `INVOKE_AUTH_TOKEN`; without it the caller is anonymous and is refused, because
the agent declares a minimum trust level.

```json
{
  "input": "Run the price and promotion compliance check for the current promo cycle.",
  "input_context": {
    "price_records": [
      {"store_id": "S001", "sku": "SKU-001", "approved_price": 498, "actual_price": 548}
    ]
  }
}
```

Every field is validated before the agent runs: prices must be finite numbers within
range, and `store_id` and `sku` are restricted to a plain identifier alphabet because
they are rendered into the report. A rejected request names the field and never echoes
the value back. Context keys the contract does not declare are dropped.

The response carries the report under `output.compliance_report`. A run that fails, or
whose report trips the output screen, returns a fixed reason code instead — never
partial results, and never an empty report that could be mistaken for a clean store.

## Project Structure

```
src/graph/    the agent: the loop, the report assembly and the output gate
src/tools/    the actions it can take — detect, dispatch, verify, escalate
src/services/ the reasoning stub this version ships with
src/schemas/  the state carried through the loop
src/api/      the HTTP entry point
src/nodes/    reference nodes for the node contract (not wired into this agent)
tests/        unit, integration and boundary tests
config/       agent manifest and runtime configuration
docs/         design and operational documentation
```

See `docs/` for the design and test specification.

## Customising

1. Adjust `config/` for your own environment and policies — the iteration ceiling,
   the cost ceiling, and the trust level callers must reach.
2. Replace the deterministic stub in `src/services/` with a real model client. The
   tools and the loop do not change.
3. Point the tools in `src/tools/` at your own systems: the shelf-price source, the
   channel correction tasks are sent on, and the escalation target.
4. Re-run the test suite.

## License

MIT — see [LICENSE](LICENSE).

## Status of this repository

This template is published **as is**, by its individual author, under the MIT license. It carries
**no warranty and no support commitment**, and no organisation stands behind its behaviour or
fitness for any purpose. Issues and pull requests may or may not receive a response; that is at
the sole discretion of the repository owner.
