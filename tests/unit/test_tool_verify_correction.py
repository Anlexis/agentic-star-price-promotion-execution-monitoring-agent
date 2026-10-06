# RET-C3-010 — Unit Tests: verify_correction tool (Cat-3 ReAct action)
#
# Asserted against the MERGED develop implementation (8c901de9):
#   src/tools/verify_correction.py — deterministic v1 stub.
#     - signature: (task_id, store_id, sku, state, config=None)
#     - resolved = task_id in config["resolved_task_ids"]  (default [])
#     - appends the result dict onto state["verification_results"]
#     - returns {task_id, store_id, sku, resolved, checked_at, tool}
#     - _resolve_target() substitutes the next unverified dispatched task when
#       task_id is the "pending" placeholder
#     - emits S-4 audit via the bare free function emit_trace_event — muted below.
#
# Audit free function is muted at the tool module via an autouse fixture
# (NEVER stub shared.* in sys.modules — the CI wheel ships a real shared package).
#
# NOTE (patch-target fix): src/tools/__init__.py re-exports verify_correction
# (`from src.tools.verify_correction import verify_correction`), so the dotted
# string "src.tools.verify_correction" resolves to the FUNCTION (the re-export
# shadows the submodule) and monkeypatch.setattr on it raises AttributeError.
# We import the real MODULE via importlib and patch emit_trace_event on it.

import importlib

import pytest

from src.tools.verify_correction import verify_correction

_TOOL_MODULE = importlib.import_module("src.tools.verify_correction")


@pytest.fixture(autouse=True)
def _mute_audit(monkeypatch):
    """Silence the S-4 audit free function at the verify_correction module."""
    monkeypatch.setattr(_TOOL_MODULE, "emit_trace_event", lambda *a, **k: None)


class TestVerifyCorrection:
    """Unit tests for the deterministic verify_correction stub."""

    def test_resolved_when_in_config(self):
        """task_id in config["resolved_task_ids"] -> resolved: True."""
        state = {}
        out = verify_correction(
            task_id="abc12345",
            store_id="S001",
            sku="SKU-001",
            state=state,
            config={"resolved_task_ids": ["abc12345"]},
        )
        assert out["resolved"] is True
        assert out["task_id"] == "abc12345"
        assert out["tool"] == "verify_correction"

    def test_not_resolved_by_default(self):
        """task_id NOT in resolved list -> resolved: False."""
        state = {}
        out = verify_correction(
            task_id="abc12345",
            store_id="S001",
            sku="SKU-001",
            state=state,
            config={"resolved_task_ids": ["other-id"]},
        )
        assert out["resolved"] is False

    def test_not_resolved_when_config_omitted(self):
        """No config -> resolved_task_ids defaults to [] -> resolved: False."""
        out = verify_correction(
            task_id="abc12345",
            store_id="S001",
            sku="SKU-001",
            state={},
        )
        assert out["resolved"] is False

    def test_verification_results_appended(self):
        """state["verification_results"] has the result dict after call."""
        state = {}
        out = verify_correction(
            task_id="abc12345",
            store_id="S001",
            sku="SKU-001",
            state=state,
            config={"resolved_task_ids": ["abc12345"]},
        )
        assert len(state["verification_results"]) == 1
        assert state["verification_results"][0] == out

    def test_placeholder_resolves_next_unverified_task(self):
        """ "pending" task_id resolves to the next unverified dispatched task."""
        state = {
            "dispatched_tasks": [
                {"task_id": "task-001", "store_id": "S001", "sku": "SKU-001"},
            ],
            "verification_results": [],
        }
        out = verify_correction(
            task_id="pending",
            store_id="pending",
            sku="pending",
            state=state,
            config={"resolved_task_ids": ["task-001"]},
        )
        assert out["task_id"] == "task-001"
        assert out["store_id"] == "S001"
        assert out["resolved"] is True

    def test_audit_payload_is_masked(self, monkeypatch):
        """emit_trace_event receives a non-credential payload (audit-mask check)."""
        captured = []
        monkeypatch.setattr(
            _TOOL_MODULE,
            "emit_trace_event",
            lambda *a, **k: captured.append(a),
        )
        verify_correction(
            task_id="abc12345",
            store_id="S001",
            sku="SKU-001",
            state={},
            config={"resolved_task_ids": ["abc12345"]},
        )
        assert captured, "emit_trace_event should have been called"
        # emit_trace_event(event_name, payload_dict, state) — payload is call.args[1]
        payload = captured[0][1]
        assert isinstance(payload, dict)
        joined = " ".join(str(k).lower() for k in payload.keys())
        for tok in ("token", "secret", "password", "credential"):
            assert tok not in joined
