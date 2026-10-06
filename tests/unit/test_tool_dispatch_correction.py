# RET-C3-010 — Unit Tests: dispatch_correction tool (Cat-3 ReAct action)
#
# Asserted against the MERGED develop implementation (8c901de9):
#   src/tools/dispatch_correction.py — deterministic v1 stub.
#     - signature: (store_id, sku, correction, deadline, priority, state, config=None)
#     - task_id = str(uuid4())[:8]  (non-empty string)
#     - appends a task_record onto state["dispatched_tasks"]
#     - writes the record as a JSON line to config["dispatch_log_path"]
#       (default /tmp/ret_c3_010_dispatch.log)
#     - returns {task_id, store_id, sku, dispatched_at, status: "DISPATCHED", tool}
#     - _resolve_target() substitutes the next undispatched discrepancy when
#       store_id/sku are the "pending" placeholder
#     - emits S-4 audit via the bare free function emit_trace_event — muted below.
#
# Audit free function is muted at the tool module via an autouse fixture
# (NEVER stub shared.* in sys.modules — the CI wheel ships a real shared package).
#
# NOTE (patch-target fix): src/tools/__init__.py re-exports dispatch_correction
# (`from src.tools.dispatch_correction import dispatch_correction`), so the dotted
# string "src.tools.dispatch_correction" resolves to the FUNCTION (the re-export
# shadows the submodule) and monkeypatch.setattr on it raises AttributeError.
# We import the real MODULE via importlib and patch emit_trace_event on it.

import importlib
import json
import os

import pytest

from src.tools.dispatch_correction import dispatch_correction

_TOOL_MODULE = importlib.import_module("src.tools.dispatch_correction")


@pytest.fixture(autouse=True)
def _mute_audit(monkeypatch):
    """Silence the S-4 audit free function at the dispatch_correction module."""
    monkeypatch.setattr(_TOOL_MODULE, "emit_trace_event", lambda *a, **k: None)


def _dispatch(state, config, store_id="S001", sku="SKU-001"):
    return dispatch_correction(
        store_id=store_id,
        sku=sku,
        correction="Update shelf price to approved price",
        deadline="2026-06-26T12:00:00Z",
        priority="HIGH",
        state=state,
        config=config,
    )


class TestDispatchCorrection:
    """Unit tests for the deterministic dispatch_correction stub."""

    def test_dispatch_returns_task_id(self, tmp_path):
        """Returns dict with task_id (non-empty string)."""
        config = {"dispatch_log_path": str(tmp_path / "dispatch.log")}
        out = _dispatch({}, config)
        assert isinstance(out["task_id"], str)
        assert out["task_id"]  # non-empty
        assert out["tool"] == "dispatch_correction"

    def test_dispatched_tasks_appended(self, tmp_path):
        """After call, state["dispatched_tasks"] has one record."""
        config = {"dispatch_log_path": str(tmp_path / "dispatch.log")}
        state = {}
        out = _dispatch(state, config)
        assert len(state["dispatched_tasks"]) == 1
        record = state["dispatched_tasks"][0]
        assert record["task_id"] == out["task_id"]
        assert record["store_id"] == "S001"
        assert record["sku"] == "SKU-001"
        assert record["priority"] == "HIGH"

    def test_status_dispatched(self, tmp_path):
        """status == "DISPATCHED"."""
        config = {"dispatch_log_path": str(tmp_path / "dispatch.log")}
        out = _dispatch({}, config)
        assert out["status"] == "DISPATCHED"

    def test_log_written_to_path(self, tmp_path):
        """File written at config["dispatch_log_path"] containing the task record."""
        log_path = tmp_path / "dispatch.log"
        config = {"dispatch_log_path": str(log_path)}
        out = _dispatch({}, config)
        assert os.path.exists(log_path)
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        logged = json.loads(lines[0])
        assert logged["task_id"] == out["task_id"]
        assert logged["store_id"] == "S001"

    def test_placeholder_resolves_next_undispatched_discrepancy(self, tmp_path):
        """ "pending" store_id/sku resolves to the next undispatched discrepancy in state."""
        config = {"dispatch_log_path": str(tmp_path / "dispatch.log")}
        state = {
            "discrepancies": [
                {"store_id": "S009", "sku": "SKU-009", "delta": -50, "severity": "MEDIUM"},
            ],
            "dispatched_tasks": [],
        }
        out = _dispatch(state, config, store_id="pending", sku="pending")
        assert out["store_id"] == "S009"
        assert out["sku"] == "SKU-009"

    def test_audit_payload_is_masked(self, tmp_path, monkeypatch):
        """emit_trace_event receives a non-credential payload (audit-mask check)."""
        captured = []
        monkeypatch.setattr(
            _TOOL_MODULE,
            "emit_trace_event",
            lambda *a, **k: captured.append(a),
        )
        config = {"dispatch_log_path": str(tmp_path / "dispatch.log")}
        _dispatch({}, config)
        assert captured, "emit_trace_event should have been called"
        # emit_trace_event(event_name, payload_dict, state) — payload is call.args[1]
        payload = captured[0][1]
        assert isinstance(payload, dict)
        joined = " ".join(str(k).lower() for k in payload.keys())
        for tok in ("token", "secret", "password", "credential"):
            assert tok not in joined
