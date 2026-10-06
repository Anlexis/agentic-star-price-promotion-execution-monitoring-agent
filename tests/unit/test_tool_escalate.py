# RET-C3-010 — Unit Tests: escalate tool (Cat-3 ReAct action)
#
# Asserted against the MERGED develop implementation (8c901de9):
#   src/tools/escalate.py — deterministic v1 stub.
#     - signature: (store_id, sku, reason, keihyo_risk, state, config=None)
#     - appends an escalation_record onto state["escalations"]
#     - when keihyo_risk is True, appends {store_id, sku, escalated_at} onto
#       state["keihyo_risk_flags"]; when False, that list stays empty
#     - writes the record as a JSON line to config["escalation_log_path"]
#       (default /tmp/ret_c3_010_escalation.log)
#     - returns {store_id, sku, escalated_at, keihyo_risk, tool, status: "ESCALATED"}
#     - _resolve_target() substitutes the next unresolved/unescalated discrepancy
#       when store_id/sku are the "pending" placeholder
#     - emits S-4 audit via the bare free function emit_trace_event — muted below.
#
# Audit free function is muted at the tool module via an autouse fixture
# (NEVER stub shared.* in sys.modules — the CI wheel ships a real shared package).
#
# NOTE (patch-target fix): src/tools/__init__.py re-exports escalate
# (`from src.tools.escalate import escalate`), so the dotted string
# "src.tools.escalate" resolves to the FUNCTION (the re-export shadows the
# submodule) and monkeypatch.setattr on it raises AttributeError. We import the
# real MODULE via importlib and patch emit_trace_event on it.

import importlib

import pytest

from src.tools.escalate import escalate

_TOOL_MODULE = importlib.import_module("src.tools.escalate")


@pytest.fixture(autouse=True)
def _mute_audit(monkeypatch):
    """Silence the S-4 audit free function at the escalate module."""
    monkeypatch.setattr(_TOOL_MODULE, "emit_trace_event", lambda *a, **k: None)


def _escalate(state, config, keihyo_risk, store_id="S001", sku="SKU-001"):
    return escalate(
        store_id=store_id,
        sku=sku,
        reason="Correction unresolved after verification deadline",
        keihyo_risk=keihyo_risk,
        state=state,
        config=config,
    )


class TestEscalate:
    """Unit tests for the deterministic escalate stub."""

    def test_escalation_appended(self, tmp_path):
        """state["escalations"] has a record after the call."""
        config = {"escalation_log_path": str(tmp_path / "escalation.log")}
        state = {}
        out = _escalate(state, config, keihyo_risk=False)
        assert len(state["escalations"]) == 1
        record = state["escalations"][0]
        assert record["store_id"] == "S001"
        assert record["sku"] == "SKU-001"
        assert out["store_id"] == "S001"

    def test_keihyo_flag_set(self, tmp_path):
        """keihyo_risk=True -> state["keihyo_risk_flags"] non-empty."""
        config = {"escalation_log_path": str(tmp_path / "escalation.log")}
        state = {}
        _escalate(state, config, keihyo_risk=True)
        assert len(state["keihyo_risk_flags"]) == 1
        flag = state["keihyo_risk_flags"][0]
        assert flag["store_id"] == "S001"
        assert flag["sku"] == "SKU-001"

    def test_keihyo_flag_not_set(self, tmp_path):
        """keihyo_risk=False -> state["keihyo_risk_flags"] empty / absent."""
        config = {"escalation_log_path": str(tmp_path / "escalation.log")}
        state = {}
        _escalate(state, config, keihyo_risk=False)
        # The tool only touches keihyo_risk_flags when keihyo_risk is True.
        assert state.get("keihyo_risk_flags", []) == []

    def test_status_escalated(self, tmp_path):
        """Return status == "ESCALATED"."""
        config = {"escalation_log_path": str(tmp_path / "escalation.log")}
        out = _escalate({}, config, keihyo_risk=False)
        assert out["status"] == "ESCALATED"
        assert out["tool"] == "escalate"

    def test_placeholder_resolves_next_unresolved_discrepancy(self, tmp_path):
        """ "pending" store_id/sku resolves the next unresolved, unescalated discrepancy."""
        config = {"escalation_log_path": str(tmp_path / "escalation.log")}
        state = {
            "discrepancies": [
                {"store_id": "S007", "sku": "SKU-007", "delta": -80, "severity": "HIGH"},
            ],
            "verification_results": [],
            "escalations": [],
        }
        out = _escalate(state, config, keihyo_risk=True, store_id="pending", sku="pending")
        assert out["store_id"] == "S007"
        assert out["sku"] == "SKU-007"

    def test_audit_payload_is_masked(self, tmp_path, monkeypatch):
        """emit_trace_event receives a non-credential payload (audit-mask check)."""
        captured = []
        monkeypatch.setattr(
            _TOOL_MODULE,
            "emit_trace_event",
            lambda *a, **k: captured.append(a),
        )
        config = {"escalation_log_path": str(tmp_path / "escalation.log")}
        _escalate({}, config, keihyo_risk=True)
        assert captured, "emit_trace_event should have been called"
        # emit_trace_event(event_name, payload_dict, state) — payload is call.args[1]
        payload = captured[0][1]
        assert isinstance(payload, dict)
        joined = " ".join(str(k).lower() for k in payload.keys())
        for tok in ("token", "secret", "password", "credential"):
            assert tok not in joined
