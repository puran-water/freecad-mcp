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

from typing import Any, Callable

import structlog
from fastmcp import FastMCP, Context
from mcp.types import TextContent

logger = structlog.get_logger("FreeCADMCPserver.clearance")

NEC_DEPTH_BY_CONDITION = {1: 36.0, 2: 42.0, 3: 48.0}  # inches
EGRESS_MIN_WIDTH_IN = 28.0
EGRESS_HEIGHT_IN = 78.0


def register_clearance_tools(
    mcp: FastMCP,
    get_freecad_connection: Callable,
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
    ) -> list[TextContent]:
        """Declare a typed clearance envelope as a ghost solid ``GH_<name>``.

        Replaces any existing envelope of the same name. See module docstring
        for kinds and their params. Units follow the document (mm).
        """
        kinds = ("box", "cylinder", "swing_arc", "nec_110_26", "egress")
        if kind not in kinds:
            return [TextContent(type="text", text=f"Invalid kind '{kind}'. Options: {', '.join(kinds)}")]
        required = {
            "box": ("x", "y", "z", "dx", "dy", "dz"),
            "cylinder": ("p1", "axis", "radius", "length"),
            "swing_arc": ("hinge", "radius", "start_deg", "sweep_deg", "z0", "z1"),
            "nec_110_26": ("face_x", "face_y", "width", "facing", "condition"),
            "egress": ("x0", "y0", "length", "axis"),
        }[kind]
        missing = [k for k in required if k not in params]
        if missing:
            return [TextContent(type="text", text=f"kind '{kind}' missing params: {missing}")]
        if kind == "nec_110_26" and params["condition"] not in (1, 2, 3):
            return [TextContent(type="text", text="nec_110_26 condition must be 1, 2 or 3")]

        rgba = color or [0.15, 0.45, 0.85]
        code = f'''
import FreeCAD, Part, math
doc = FreeCAD.getDocument("{doc_name}")
if doc is None:
    raise ValueError("Document '{doc_name}' not found")
V = FreeCAD.Vector
IN = 25.4
kind = {kind!r}
p = {params!r}
gh_name = "GH_" + {name!r}
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
        freecad = get_freecad_connection()
        try:
            res = freecad.execute_code(code)
            if res.get("success"):
                return [TextContent(type="text", text=res.get("message", "envelope declared"))]
            return [TextContent(type="text", text=f"Failed to declare envelope: {res.get('error', 'Unknown error')}")]
        except Exception as e:
            logger.error("clearance_declare_failed", name=name, error=str(e))
            return [TextContent(type="text", text=f"Failed to declare envelope: {e}")]

    @mcp.tool()
    def clearance_gate(
        ctx: Context,
        doc_name: str,
        solid_prefixes: list[str],
        allow_pairs: list[list[str]] | None = None,
        min_volume_in3: float = 1.0,
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
        code = f'''
import FreeCAD
doc = FreeCAD.getDocument("{doc_name}")
if doc is None:
    raise ValueError("Document '{doc_name}' not found")
IN = 25.4
prefixes = tuple({solid_prefixes!r})
allow = {allow_pairs or []!r}
minv = {min_volume_in3} * 16387.064

envs = {{}}
solids = {{}}
for o in doc.Objects:
    if not hasattr(o, "Shape") or o.Shape is None or o.Shape.Volume <= 0:
        continue
    if o.Name.startswith("GH_"):
        envs[o.Name[3:]] = o.Shape
    elif o.Name.startswith(prefixes):
        solids[o.Name] = o.Shape

report = []
fails = 0
for ename, esh in sorted(envs.items()):
    rows_fail = []
    rows_info = []
    for sname, ssh in sorted(solids.items()):
        try:
            c = esh.common(ssh)
        except Exception:
            continue
        if c.Volume < minv:
            continue
        bb = c.BoundBox
        row = f"    {{sname}}: {{c.Volume/16387.064:.0f}} in3 [x {{bb.XMin/IN:.0f}}..{{bb.XMax/IN:.0f}}, y {{bb.YMin/IN:.0f}}..{{bb.YMax/IN:.0f}}, z {{bb.ZMin/IN:.0f}}..{{bb.ZMax/IN:.0f}}]"
        allowed = any(ename == a[0] and sname.startswith(a[1]) for a in allow)
        (rows_info if allowed else rows_fail).append(row)
    status = "FAIL" if rows_fail else "PASS"
    if rows_fail:
        fails += 1
    report.append(f"{{status}}  {{ename}}")
    report.extend(rows_fail)
    for r in rows_info:
        report.append(r.replace("    ", "    [allowed] ", 1))
print(f"==== CLEARANCE GATE: {{len(envs)}} envelopes vs {{len(solids)}} solids ====")
print("\\n".join(report))
print(f"==== VERDICT: {{'PASS' if fails == 0 else f'FAIL ({{fails}} envelope(s))'}} ====")
'''
        freecad = get_freecad_connection()
        try:
            res = freecad.execute_code(code)
            if res.get("success"):
                return [TextContent(type="text", text=res.get("message", ""))]
            return [TextContent(type="text", text=f"Gate failed to run: {res.get('error', 'Unknown error')}")]
        except Exception as e:
            logger.error("clearance_gate_failed", error=str(e))
            return [TextContent(type="text", text=f"Gate failed to run: {e}")]

    logger.info("clearance_tools_registered")
