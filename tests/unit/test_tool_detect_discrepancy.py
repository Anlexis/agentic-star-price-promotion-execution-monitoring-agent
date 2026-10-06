# RET-C3-010 — Unit Tests: detect_discrepancy tool (Cat-3 ReAct action)
#
# Asserted against the MERGED develop implementation (8c901de9):
#   src/tools/detect_discrepancy.py — deterministic v1 stub.
#     - reads config["price_records"] or state["price_records"]
#     - delta = approved_price - actual_price; skips delta == 0
#     - severity HIGH when abs(delta) > 50 (module const _HIGH_SEVERITY_DELTA),
#       else MEDIUM
#     - writes the list onto state["discrepancies"]
#     - returns {discrepancies, checked_count, tool}
#     - emits S-4 audit via the bare free function emit_trace_event
#       (from shared.utils.audit_logger) — muted at the TOOL module below.
#
# Audit free function is muted at the tool module via an autouse fixture
# (NEVER stub shared.* in sys.modules — the CI wheel ships a real shared package).
#
# NOTE (patch-target fix): src/tools/__init__.py re-exports detect_discrepancy
# (`from src.tools.detect_discrepancy import detect_discrepancy`), so the dotted
# string "src.tools.detect_discrepancy" resolves to the FUNCTION (the re-export
# shadows the submodule) and monkeypatch.setattr on it raises AttributeError.
# We import the real MODULE via importlib and patch emit_trace_event on it.

import importlib

import pytest

from src.tools.detect_discrepancy import detect_discrepancy

_TOOL_MODULE = importlib.import_module("src.tools.detect_discrepancy")


@pytest.fixture(autouse=True)
def _mute_audit(monkeypatch):
    """Silence the S-4 audit free function at the detect_discrepancy module."""
    monkeypatch.setattr(_TOOL_MODULE, "emit_trace_event", lambda *a, **k: None)


def _record(store_id="S001", sku="SKU-001", approved=498, actual=548):
    return {
        "store_id": store_id,
        "sku": sku,
        "approved_price": approved,
        "actual_price": actual,
    }


class TestDetectDiscrepancy:
    """Unit tests for the deterministic detect_discrepancy stub."""

    def test_detects_price_mismatch(self):
        """Records with approved != actual -> discrepancies list non-empty."""
        state = {"price_records": [_record(approved=498, actual=548)]}
        out = detect_discrepancy(state)
        assert out["tool"] == "detect_discrepancy"
        assert len(out["discrepancies"]) == 1
        disc = out["discrepancies"][0]
        assert disc["store_id"] == "S001"
        assert disc["sku"] == "SKU-001"
        # delta = approved - actual = 498 - 548 = -50
        assert disc["delta"] == -50
        # state mirror is written
        assert state["discrepancies"] == out["discrepancies"]

    def test_no_discrepancy_on_match(self):
        """Records with approved == actual -> discrepancies: []."""
        state = {"price_records": [_record(approved=500, actual=500)]}
        out = detect_discrepancy(state)
        assert out["discrepancies"] == []
        assert out["checked_count"] == 1
        assert state["discrepancies"] == []

    def test_severity_high_on_large_delta(self):
        """abs(delta) > 50 -> severity HIGH."""
        # delta = 600 - 500 = 100 -> abs(100) > 50 -> HIGH
        state = {"price_records": [_record(approved=600, actual=500)]}
        out = detect_discrepancy(state)
        assert out["discrepancies"][0]["severity"] == "HIGH"

    def test_severity_medium_on_small_delta(self):
        """abs(delta) <= 50 -> severity MEDIUM."""
        # delta = 530 - 500 = 30 -> abs(30) <= 50 -> MEDIUM
        state = {"price_records": [_record(approved=530, actual=500)]}
        out = detect_discrepancy(state)
        assert out["discrepancies"][0]["severity"] == "MEDIUM"

    def test_severity_boundary_exactly_50_is_medium(self):
        """abs(delta) == 50 is NOT > 50 -> MEDIUM (boundary on _HIGH_SEVERITY_DELTA)."""
        # delta = 550 - 500 = 50 -> abs(50) is not > 50 -> MEDIUM
        state = {"price_records": [_record(approved=550, actual=500)]}
        out = detect_discrepancy(state)
        assert out["discrepancies"][0]["severity"] == "MEDIUM"

    def test_empty_price_records(self):
        """price_records: [] -> discrepancies: [], checked_count: 0."""
        state = {"price_records": []}
        out = detect_discrepancy(state)
        assert out["discrepancies"] == []
        assert out["checked_count"] == 0
        assert state["discrepancies"] == []

    def test_config_price_records_override_state(self):
        """config["price_records"] takes precedence over state["price_records"]."""
        state = {"price_records": [_record(approved=500, actual=500)]}  # would be no-op
        config = {"price_records": [_record(approved=600, actual=500)]}  # HIGH delta
        out = detect_discrepancy(state, config)
        assert out["checked_count"] == 1
        assert len(out["discrepancies"]) == 1
        assert out["discrepancies"][0]["severity"] == "HIGH"

    def test_record_missing_prices_is_skipped(self):
        """A record with a missing approved/actual price is skipped (not counted as discrepancy)."""
        state = {
            "price_records": [
                {"store_id": "S001", "sku": "SKU-001", "approved_price": None, "actual_price": 548},
                _record(approved=600, actual=500),
            ]
        }
        out = detect_discrepancy(state)
        # only the second (valid) record yields a discrepancy
        assert len(out["discrepancies"]) == 1
        # but both records were evaluated
        assert out["checked_count"] == 2

    def test_audit_payload_is_masked(self, monkeypatch):
        """emit_trace_event receives a non-credential payload (audit-mask check)."""
        captured = []
        monkeypatch.setattr(
            _TOOL_MODULE,
            "emit_trace_event",
            lambda *a, **k: captured.append(a),
        )
        state = {"price_records": [_record(approved=600, actual=500)]}
        detect_discrepancy(state)
        assert captured, "emit_trace_event should have been called"
        # emit_trace_event(event_name, payload_dict, state) — payload is call.args[1]
        payload = captured[0][1]
        assert isinstance(payload, dict)
        joined = " ".join(str(k).lower() for k in payload.keys())
        for tok in ("token", "secret", "password", "credential"):
            assert tok not in joined
