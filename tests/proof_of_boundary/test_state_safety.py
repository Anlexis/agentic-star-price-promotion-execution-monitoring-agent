# PB-2 + PB-5: State Safety Verification
# Verifies that State contains only msgpack-safe types (no Pydantic, dataclass, JWT)
#
# RET-C3-010 extension (#8): the scaffold version only AST-scans src/schemas/state.py
# for credential field names + prohibited annotations. This file ADDS an explicit
# assertion that the new Cat-3 domain fields introduced for the autonomous loop
# (dispatched_tasks, escalations, verification_results, keihyo_risk_flags, etc.)
# carry no credential-like names. The original scaffold test is preserved verbatim
# below — no regression.

import ast
import os
import re
import pytest


CREDENTIAL_FIELD_PATTERNS = re.compile(
    r"(jwt|token|api_key|secret|password|credential|connection_string)", re.IGNORECASE
)

PROHIBITED_TYPE_ANNOTATIONS = [
    "BaseModel",
    "InvocationContext",
]


def _state_file_path() -> str:
    return os.path.join(os.path.dirname(__file__), "..", "..", "src", "schemas", "state.py")


def _scan_state_file(filepath: str) -> list[str]:
    """Scan a state definition file for safety violations."""
    with open(filepath, "r") as f:
        source = f.read()
        tree = ast.parse(source, filename=filepath)

    violations = []

    for node in ast.walk(tree):
        # Check class definitions that look like State
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    field_name = item.target.id

                    # Check for credential-like field names
                    if CREDENTIAL_FIELD_PATTERNS.search(field_name):
                        violations.append(f"{filepath}:{item.lineno} — Credential-like field name: {field_name}")

                    # Check for prohibited type annotations
                    if item.annotation:
                        annotation_str = ast.dump(item.annotation)
                        for prohibited in PROHIBITED_TYPE_ANNOTATIONS:
                            if prohibited in annotation_str:
                                violations.append(f"{filepath}:{item.lineno} — Prohibited type in State: {prohibited}")

    return violations


def _annotated_field_names(filepath: str) -> list[str]:
    """Collect every annotated field name declared on a class in the file."""
    with open(filepath, "r") as f:
        tree = ast.parse(f.read(), filename=filepath)

    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    names.append(item.target.id)
    return names


class TestStateSafety:
    """PB-2/PB-5: State must be msgpack-safe with no credentials."""

    def test_state_file_safety(self):
        """State definition must not contain credential fields or prohibited types."""
        state_file = _state_file_path()
        if not os.path.exists(state_file):
            pytest.skip("src/schemas/state.py not found")

        violations = _scan_state_file(state_file)

        assert violations == [], "State safety violations found:\n" + "\n".join(violations)


class TestCat3StateFieldsNoCredentials:
    """RET-C3-010 (#8): the new Cat-3 loop fields must not carry credential-like names."""

    def test_cat3_domain_fields_present_and_clean(self):
        """The expected Cat-3 domain fields exist and none look credential-shaped."""
        state_file = _state_file_path()
        if not os.path.exists(state_file):
            pytest.skip("src/schemas/state.py not found")

        names = _annotated_field_names(state_file)

        # The autonomous-loop domain fields introduced for RET-C3-010.
        expected_cat3_fields = {
            "price_records",
            "discrepancies",
            "dispatched_tasks",
            "verification_results",
            "escalations",
            "keihyo_risk_flags",
        }
        assert expected_cat3_fields.issubset(
            set(names)
        ), f"missing Cat-3 domain fields: {expected_cat3_fields - set(names)}"

        # No declared field name may match a credential pattern.
        flagged = [n for n in names if CREDENTIAL_FIELD_PATTERNS.search(n)]
        assert flagged == [], f"credential-like Cat-3 state fields: {flagged}"
