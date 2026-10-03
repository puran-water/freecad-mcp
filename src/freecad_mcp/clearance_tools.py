"""Typed clearance declarations and delegation to the shared boolean gate.

The shared CAD compiler owns envelope geometry and clearance evaluation.
Declarations bind a held review bundle, geometry hashes and a supplied basis;
this adapter supplies no separate geometry generator.

Envelope kinds:
- ``box``:        {x, y, z, dx, dy, dz}
- ``cylinder``:   {p1: [x,y,z], axis: [x,y,z], radius, length}   (e.g. vendor
                  full-cylinder door-swing envelopes)
- ``swing_arc``:  {hinge: [x,y,z], radius, start_deg, sweep_deg, z0, z1}
                  vertical-axis door/cover swing sector (pump covers, panel
                  doors) — start_deg measured from +X, CCW positive
- ``nec_110_26``: {face_x, face_y, width, facing: [fx,fy], condition: 1|2|3,
                  z0} — working-space depth 36/42/48" by condition
- ``egress``:     {x0, y0, z0, length, axis: [ax,ay], width, height}
                  continuous corridor (typed compiler defaults 28" wide, 90" high)

Envelope dimensions are in millimetres. Tool argument names retained for
compatibility do not select a document or a geometry backend.
"""

from typing import Any, Callable

from fastmcp import FastMCP, Context
from mcp.types import TextContent

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

try:  # pragma: no cover - depends on the installed mcp
    from mcp.types import ToolAnnotations
except ImportError:  # older mcp in the geometry runtime
    ToolAnnotations = None


def _ann(**hints):
    """Tool annotations, when the installed mcp knows about them.

    The pinned build123d runtime imports this module only to read its envelope
    contract, never to serve tools, and carries an older mcp without
    ToolAnnotations. Annotations describe the SERVED surface, so losing them in
    an import that serves nothing costs nothing — breaking that import costs a
    cross-runtime contract test.
    """
    return ToolAnnotations(**hints) if ToolAnnotations is not None else None


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


def register_clearance_tools(
    mcp: FastMCP,
    _unused_connection: Callable | None,   # was the FreeCAD RPC handle
    add_screenshot_if_available: Callable,
) -> None:
    """Register clearance tools with the MCP server."""

    @mcp.tool(annotations=_ann(readOnlyHint=False, destructiveHint=False, idempotentHint=True))
    def clearance_declare(
        ctx: Context,
        doc_name: str,
        name: str,
        kind: str,
        params: dict[str, Any],
        color: list[float] | None = None,
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

        if not bundle_path or not state_path or not basis_note:
            return [TextContent(type="text",text="Headless declaration requires bundle_path, state_path and basis_note.")]
        if color is not None:
            return [TextContent(type="text",text="Headless gate declarations carry geometry and basis; GUI ghost colors are unsupported.")]
        from engineering_utils.cad.block_model import ServiceEnvelope
        from freecad_mcp.design_tools import _run_cad_module
        envelope=ServiceEnvelope(name=name,kind=kind,params=params,basis=basis_note)
        result=_run_cad_module("engineering_utils.cad.clearance",["declare","--state",state_path,
            "--bundle",bundle_path,"--envelope",envelope.model_dump_json()])
        return [TextContent(type="text",text=result)]

    @mcp.tool(annotations=_ann(readOnlyHint=True, destructiveHint=False, idempotentHint=True))
    def clearance_gate(
        ctx: Context,
        doc_name: str,
        solid_prefixes: list[str],
        allow_pairs: list[list[str]] | None = None,
        min_volume_in3: float = 1.0,
        max_faces: int = 2000,
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
        if not state_path:
            return [TextContent(type="text",text="Headless gate requires state_path from clearance_declare.")]
        if allow_pairs:
            return [TextContent(type="text",text="Headless clearance does not permit unbounded prefix allowances; use bounded canonical mating declarations in the shared clash gate.")]
        from freecad_mcp.design_tools import _run_cad_module
        arguments=["gate","--state",state_path,"--min-volume-mm3",str(min_volume_in3*16387.064),
                   "--max-faces",str(max_faces)]
        for prefix in solid_prefixes:arguments.extend(["--prefix",prefix])
        return [TextContent(type="text",text=_run_cad_module("engineering_utils.cad.clearance",arguments))]
