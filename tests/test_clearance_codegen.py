"""Typed clearance validation and delegation to the shared headless worker."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from freecad_mcp.clearance_tools import (  # noqa: E402
    ENVELOPE_KINDS,
    ENVELOPE_REQUIRED_PARAMS,
    register_clearance_tools,
    validate_envelope,
)

VALID_PARAMS: dict[str, dict] = {
    "box": {"x": 0.0, "y": 0.0, "z": 0.0, "dx": 100.0, "dy": 200.0, "dz": 300.0},
    "cylinder": {"p1": [0.0, 0.0, 0.0], "axis": [0.0, 0.0, 1.0], "radius": 50.0, "length": 400.0},
    "swing_arc": {"hinge": [0.0, 0.0, 0.0], "radius": 731.5, "start_deg": 270.0,
                  "sweep_deg": 90.0, "z0": 200.0, "z1": 1625.0},
    "nec_110_26": {"face_x": 0.0, "face_y": 0.0, "width": 914.0, "facing": [0, -1], "condition": 2},
    "egress": {"x0": 0.0, "y0": 0.0, "length": 5000.0, "axis": [1, 0]},
}


@pytest.fixture
def delegated_tools(monkeypatch):
    from freecad_mcp import design_tools

    registered, calls = {}, []

    class MCP:
        def tool(self, **_annotations):
            def capture(fn):
                registered[fn.__name__] = fn
                return fn
            return capture

    def worker(module, arguments):
        calls.append((module, arguments))
        return "worker result"

    monkeypatch.setattr(design_tools, "_run_cad_module", worker)
    register_clearance_tools(MCP(), None, lambda *args: None)
    assert set(registered) == {"clearance_declare", "clearance_gate"}
    return registered, calls


@pytest.mark.parametrize("kind", ENVELOPE_KINDS)
def test_declaration_passes_a_typed_envelope_to_the_shared_worker(delegated_tools, kind):
    registered, calls = delegated_tools
    response = registered["clearance_declare"](
        None, "compatibility label", "service", kind, VALID_PARAMS[kind],
        bundle_path="review.bundle.json", state_path="clearance.json", basis_note="fixture travel",
    )
    assert response[0].text == "worker result"
    assert len(calls) == 1
    module, args = calls[0]
    assert module == "engineering_utils.cad.clearance"
    assert args[:-1] == [
        "declare", "--state", "clearance.json", "--bundle", "review.bundle.json", "--envelope",
    ]
    envelope = json.loads(args[-1])
    assert envelope["kind"] == kind and envelope["name"] == "service"
    assert envelope["params"] == VALID_PARAMS[kind]
    assert envelope["basis"] == "fixture travel"


def test_gate_preserves_prefixes_and_converts_the_volume_unit(delegated_tools):
    registered, calls = delegated_tools
    response = registered["clearance_gate"](
        None, "compatibility label", ["PUMP", "PANEL"], min_volume_in3=2,
        max_faces=150, state_path="clearance.json",
    )
    assert response[0].text == "worker result"
    assert calls == [("engineering_utils.cad.clearance", [
        "gate", "--state", "clearance.json", "--min-volume-mm3", "32774.128",
        "--max-faces", "150", "--prefix", "PUMP", "--prefix", "PANEL",
    ])]


@pytest.mark.parametrize("kwargs,message", [
    ({}, "requires state_path"),
    ({"state_path": "clearance.json", "allow_pairs": [["door", "PUMP"]]}, "does not permit"),
])
def test_unbounded_or_unbound_gate_requests_never_reach_the_worker(delegated_tools, kwargs, message):
    registered, calls = delegated_tools
    response = registered["clearance_gate"](None, "compatibility label", ["PUMP"], **kwargs)
    assert message in response[0].text
    assert calls == []


# --- validation ---------------------------------------------------------


@pytest.mark.parametrize("kind", ENVELOPE_KINDS)
def test_valid_params_pass_validation(kind):
    assert validate_envelope(kind, VALID_PARAMS[kind]) is None


def test_unknown_kind_rejected():
    assert "Invalid kind" in (validate_envelope("sphere", {}) or "")


@pytest.mark.parametrize("kind", ENVELOPE_KINDS)
def test_missing_param_rejected(kind):
    params = dict(VALID_PARAMS[kind])
    dropped = ENVELOPE_REQUIRED_PARAMS[kind][0]
    params.pop(dropped)
    msg = validate_envelope(kind, params)
    assert msg and dropped in msg


def test_bad_nec_condition_rejected():
    params = dict(VALID_PARAMS["nec_110_26"], condition=4)
    assert "condition must be" in (validate_envelope("nec_110_26", params) or "")


# --- contract drift with the block library ------------------------------


def test_block_library_envelope_contract_matches_this_module():
    """Blocks must not be able to declare an envelope this module cannot build."""

    block_model = pytest.importorskip(
        "engineering_utils.cad.block_model",
        reason="engineering-utils not importable in this venv; drift check runs in the lib CI",
    )
    assert set(block_model.ENVELOPE_KINDS) == set(ENVELOPE_KINDS)
    assert block_model.ENVELOPE_REQUIRED_PARAMS == ENVELOPE_REQUIRED_PARAMS
