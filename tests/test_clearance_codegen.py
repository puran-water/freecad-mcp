"""Tier-2 tests for the extracted envelope codegen.

Two jobs:
1. The refactor is behaviour-neutral — generated source still compiles and
   still contains the geometry calls each kind depends on.
2. Contract drift between this module and the block library is caught
   mechanically, so a block can never ship an envelope the gate cannot build.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from freecad_mcp.clearance_tools import (  # noqa: E402
    DEFAULT_ENVELOPE_RGBA,
    ENVELOPE_KINDS,
    ENVELOPE_REQUIRED_PARAMS,
    envelope_shape_code,
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


@pytest.mark.parametrize("kind", ENVELOPE_KINDS)
def test_generated_source_compiles(kind):
    code = envelope_shape_code("DOC", "TEST", kind, VALID_PARAMS[kind])
    compile(code, f"<{kind}>", "exec")


@pytest.mark.parametrize("kind", ENVELOPE_KINDS)
def test_generated_source_names_the_ghost(kind):
    code = envelope_shape_code("DOC", "MyEnv", kind, VALID_PARAMS[kind])
    assert "'GH_' + 'MyEnv'" in code or '"GH_" + \'MyEnv\'' in code or "GH_" in code
    assert 'FreeCAD.getDocument("DOC")' in code


def test_prefix_is_overridable_for_block_proxies():
    code = envelope_shape_code("DOC", "P1_body", "box", VALID_PARAMS["box"], prefix="PX_")
    assert "'PX_'" in code


@pytest.mark.parametrize("kind,marker", [
    ("box", "Part.makeBox"),
    ("cylinder", "Part.makeCylinder"),
    ("swing_arc", "shape.rotate"),
    ("nec_110_26", "36.0"),
    ("egress", "28.0"),
])
def test_geometry_call_preserved_per_kind(kind, marker):
    code = envelope_shape_code("DOC", "T", kind, VALID_PARAMS[kind])
    assert marker in code


def test_colour_is_applied():
    code = envelope_shape_code("DOC", "T", "box", VALID_PARAMS["box"], rgba=(0.9, 0.1, 0.2))
    assert "(0.9, 0.1, 0.2)" in code


def test_default_colour_used_when_unspecified():
    code = envelope_shape_code("DOC", "T", "box", VALID_PARAMS["box"])
    assert str(DEFAULT_ENVELOPE_RGBA[0]) in code


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
