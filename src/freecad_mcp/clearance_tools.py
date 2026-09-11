"""Typed clearance envelopes + boolean fit gate.

Encodes the code/OSHA/service-access rule basis as declarative envelope
generators (ghost solids named ``GH_<name>``), and runs a one-call boolean
gate of every envelope against every real solid. Replaces the ad-hoc inline
gate scripts used on the RecycleWorks container GA (three rewrites, and an
ad-hoc box missed the pump cover swing-arc class entirely).

Envelope kinds:
- ``box``:        {x, y, z, dx, dy, dz}
- ``cylinder``:   {p1: [x,y,z], axis: [x,y,z], radius, length}   (e.g. vendor
                  full-cylinder door-swing envelopes)
- ``swing_arc``:  {hinge: [x,y,z], radius, start_deg, sweep_deg, z0, z1}
                  vertical-axis door/cover swing sector (pump covers, panel
                  doors) — start_deg measured from +X, CCW positive
- ``nec_110_26``: {face_x, face_y, width, facing: [fx,fy], condition: 1|2|3,
                  z0, height} — working-space depth 36/42/48" by condition
- ``egress``:     {x0, y0, z0, length, axis: [ax,ay], width, height}
                  continuous corridor (defaults 28" wide, 78" high)

All dimensions in the document's model units (mm when following the
RecycleWorks convention of inches x 25.4 at call sites).
"""

from collections.abc import Sequence
from typing import Any, Callable, Literal
import json

import structlog
from fastmcp import FastMCP, Context
from mcp.types import TextContent

logger = structlog.get_logger("FreeCADMCPserver.clearance")

NEC_DEPTH_BY_CONDITION = {1: 36.0, 2: 42.0, 3: 48.0}  # inches
EGRESS_MIN_WIDTH_IN = 28.0
EGRESS_HEIGHT_IN = 78.0

ENVELOPE_KINDS: tuple[str, ...] = ("box", "cylinder", "swing_arc", "nec_110_26", "egress")

# Single source of truth for envelope parameter contracts. The block library
# (``engineering_utils.cad.block_model.ENVELOPE_REQUIRED_PARAMS``) mirrors this
# table and a drift test binds the two — blocks that ship their own service
# envelopes must produce exactly what this module can build.
ENVELOPE_REQUIRED_PARAMS: dict[str, tuple[str, ...]] = {
    "box": ("x", "y", "z", "dx", "dy", "dz"),
    "cylinder": ("p1", "axis", "radius", "length"),
    "swing_arc": ("hinge", "radius", "start_deg", "sweep_deg", "z0", "z1"),
    "nec_110_26": ("face_x", "face_y", "width", "facing", "condition"),
    "egress": ("x0", "y0", "length", "axis"),
}

DEFAULT_ENVELOPE_RGBA = (0.15, 0.45, 0.85)


def validate_envelope(kind: str, params: dict[str, Any]) -> str | None:
    """Return an error message if this envelope is malformed, else ``None``."""

    if kind not in ENVELOPE_KINDS:
        return f"Invalid kind '{kind}'. Options: {', '.join(ENVELOPE_KINDS)}"
    missing = [k for k in ENVELOPE_REQUIRED_PARAMS[kind] if k not in params]
    if missing:
        return f"kind '{kind}' missing params: {missing}"
    if kind == "nec_110_26" and params["condition"] not in (1, 2, 3):
        return "nec_110_26 condition must be 1, 2 or 3"
    return None


def envelope_shape_code(
    doc_name: str,
    name: str,
    kind: str,
    params: dict[str, Any],
    rgba: Sequence[float] = DEFAULT_ENVELOPE_RGBA,
    prefix: str = "GH_",
) -> str:
    """Generate the FreeCAD source that builds one ghost envelope solid.

    Extracted so that hand-declared site features (walls, aisles) and
    block-provided service envelopes emit byte-identical geometry — the gate
    must not be able to tell where an envelope came from.
    """

    return f'''
import FreeCAD, Part, math
doc = FreeCAD.getDocument("{doc_name}")
if doc is None:
    raise ValueError("Document '{doc_name}' not found")
V = FreeCAD.Vector
IN = 25.4
kind = {kind!r}
p = {params!r}
gh_name = {prefix!r} + {name!r}
old = doc.getObject(gh_name)
if old:
    doc.removeObject(gh_name)

if kind == "box":
    shape = Part.makeBox(p["dx"], p["dy"], p["dz"], V(p["x"], p["y"], p["z"]))
elif kind == "cylinder":
    a = p["axis"]
    shape = Part.makeCylinder(p["radius"], p["length"], V(*p["p1"]), V(a[0], a[1], a[2]))
elif kind == "swing_arc":
    h = p["hinge"]
    shape = Part.makeCylinder(p["radius"], p["z1"] - p["z0"], V(h[0], h[1], p["z0"]), V(0, 0, 1), p["sweep_deg"])
    shape.rotate(V(h[0], h[1], 0), V(0, 0, 1), p["start_deg"])
elif kind == "nec_110_26":
    depth = {{1: 36.0, 2: 42.0, 3: 48.0}}[p["condition"]] * IN
    width = max(p["width"], 30.0 * IN)
    fx, fy = p["facing"][0], p["facing"][1]
    z0 = p.get("z0", 0.0)
    height = p.get("height", 78.0 * IN)
    if abs(fy) > abs(fx):
        x0 = p["face_x"]
        y0 = p["face_y"] if fy > 0 else p["face_y"] - depth
        shape = Part.makeBox(width, depth, height, V(x0, y0, z0))
    else:
        y0 = p["face_y"]
        x0 = p["face_x"] if fx > 0 else p["face_x"] - depth
        shape = Part.makeBox(depth, width, height, V(x0, y0, z0))
elif kind == "egress":
    width = p.get("width", 28.0 * IN)
    height = p.get("height", 78.0 * IN)
    z0 = p.get("z0", 0.0)
    ax, ay = p["axis"][0], p["axis"][1]
    if abs(ax) >= abs(ay):
        shape = Part.makeBox(p["length"], width, height, V(p["x0"], p["y0"], z0))
    else:
        shape = Part.makeBox(width, p["length"], height, V(p["x0"], p["y0"], z0))

o = doc.addObject("Part::Feature", gh_name)
o.Shape = shape
o.ViewObject.ShapeColor = ({rgba[0]}, {rgba[1]}, {rgba[2]})
o.ViewObject.Transparency = 82
doc.recompute()
bb = o.Shape.BoundBox
print(f"{{gh_name}} [{kind}]: x {{bb.XMin/IN:.1f}}..{{bb.XMax/IN:.1f}}, y {{bb.YMin/IN:.1f}}..{{bb.YMax/IN:.1f}}, z {{bb.ZMin/IN:.1f}}..{{bb.ZMax/IN:.1f}} in")
'''


def register_clearance_tools(
    mcp: FastMCP,
    _unused_connection: Callable | None,   # was the FreeCAD RPC handle
    add_screenshot_if_available: Callable,
) -> None:
    """Register clearance tools with the MCP server."""

    @mcp.tool()
    def clearance_declare(
        ctx: Context,
        doc_name: str,
        name: str,
        kind: str,
        params: dict[str, Any],
        color: list[float] | None = None,
        backend: Literal["build123d"] = "build123d",
        bundle_path: str | None = None,
        state_path: str | None = None,
        basis_note: str | None = None,
    ) -> list[TextContent]:
        """Declare a typed clearance envelope as a ghost solid ``GH_<name>``.

        Replaces any existing envelope of the same name. See module docstring
        for kinds and their params. Units follow the document (mm).
        """
        error = validate_envelope(kind, params)
        if error:
            return [TextContent(type="text", text=error)]

        if backend=="build123d":
            if not bundle_path or not state_path or not basis_note:
                return [TextContent(type="text",text="Headless declaration requires bundle_path, state_path and basis_note.")]
            if color is not None:
                return [TextContent(type="text",text="Headless gate declarations carry geometry and basis; GUI ghost colors are unsupported.")]
            from engineering_utils.cad.block_model import ServiceEnvelope
            from freecad_mcp.design_tools import _run_cad_module
            envelope=ServiceEnvelope(name=name,kind=kind,params=params,basis=basis_note)
            result=_run_cad_module("engineering_utils.cad.clearance",["declare","--state",state_path,
                "--bundle",bundle_path,"--envelope",envelope.model_dump_json()],backend=backend)
            return [TextContent(type="text",text=result)]


    @mcp.tool()
    def clearance_gate(
        ctx: Context,
        doc_name: str,
        solid_prefixes: list[str],
        allow_pairs: list[list[str]] | None = None,
        min_volume_in3: float = 1.0,
        max_faces: int = 2000,
        backend: Literal["build123d"] = "build123d",
        state_path: str | None = None,
    ) -> list[TextContent]:
        """Boolean-check every ``GH_*`` envelope against every real solid.

        Args:
            doc_name: FreeCAD document
            solid_prefixes: object-name prefixes that count as real solids
                            (e.g. ["CSW_", "PUMP", "PANEL", "CTR_", "LIN_"])
            allow_pairs: [envelope_name_without_GH, solid_prefix] pairs whose
                         overlap is expected/transient (reported as INFO, not
                         FAIL) — e.g. a hose-extraction zone sharing the aisle
            min_volume_in3: ignore intersections smaller than this

        Returns a structured PASS/FAIL report with interference volumes and
        locations — the automated form of the RecycleWorks FINDINGS gate table.
        """
        if not solid_prefixes:
            return [TextContent(type="text", text="solid_prefixes must be non-empty")]
        if backend=="build123d":
            if not state_path:
                return [TextContent(type="text",text="Headless gate requires state_path from clearance_declare.")]
            if allow_pairs:
                return [TextContent(type="text",text="Headless clearance does not permit unbounded prefix allowances; use bounded canonical mating declarations in the shared clash gate.")]
            from freecad_mcp.design_tools import _run_cad_module
            arguments=["gate","--state",state_path,"--backend",backend,"--min-volume-mm3",str(min_volume_in3*16387.064),
                       "--max-faces",str(max_faces)]
            for prefix in solid_prefixes:arguments.extend(["--prefix",prefix])
            return [TextContent(type="text",text=_run_cad_module("engineering_utils.cad.clearance",arguments,backend=backend))]
