"""Smoke tests for the TechDraw typed-arm tools: every generated FreeCAD
script must at least be syntactically valid Python, and validation paths must
reject bad input without generating code."""

import asyncio
import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


class FakeConn:
    def __init__(self):
        self.codes = []

    def execute_code(self, code):
        compile(code, "<generated>", "exec")  # the actual assertion
        self.codes.append(code)
        return {"success": True, "message": "ok"}

    def get_active_screenshot(self, *a, **k):
        return None


def _make_tools():
    from fastmcp import FastMCP
    import freecad_mcp.techdraw_tools as tt

    mcp = FastMCP("test")
    conn = FakeConn()
    tt.register_techdraw_tools(mcp, lambda: conn, lambda resp, shot, inc: resp)

    result = mcp.list_tools()  # async in fastmcp >=2, sync in 1.x
    tools = asyncio.run(result) if asyncio.iscoroutine(result) else result
    if isinstance(tools, dict):
        fns = {name: t.fn for name, t in tools.items()}
    else:
        fns = {t.name: t.fn for t in tools}
    return conn, fns


def test_plan_sheet_with_status_stamp_generates_valid_code():
    conn, fns = _make_tools()
    res = fns["create_techdraw_plan_sheet"](
        ctx=None,
        doc_name="Doc",
        drawing_number="100-GA-001",
        status="NOT FOR CONSTRUCTION",
        drawn_by="pe-cad",
        export_pdf_path="/tmp/x.pdf",
    )
    assert conn.codes, "no code was generated"
    assert "StatusStamp" in conn.codes[-1]
    assert "NOT FOR CONSTRUCTION" in conn.codes[-1]
    assert "ok" in res[0].text


def test_add_orthographic_and_section_views_generate_valid_code():
    conn, fns = _make_tools()
    res = fns["add_techdraw_view"](
        ctx=None,
        doc_name="Doc",
        page_name="P",
        view_name="FrontView",
        view_type="orthographic",
        direction="Front",
        scale=0.05,
    )
    assert "ok" in res[0].text

    res = fns["add_techdraw_view"](
        ctx=None,
        doc_name="Doc",
        page_name="P",
        view_name="SectionAA",
        view_type="section",
        base_view_name="TopView",
        section_normal=[1.0, 0.0, 0.0],
        section_origin=[1200.0, 0.0, 0.0],
        caption="SECTION A-A",
    )
    assert "ok" in res[0].text
    assert len(conn.codes) == 2
    assert "DrawViewSection" in conn.codes[-1]


def test_add_view_rejects_bad_input_without_codegen():
    conn, fns = _make_tools()
    res = fns["add_techdraw_view"](
        ctx=None, doc_name="D", page_name="P", view_name="V",
        view_type="section",  # missing normal/origin/base
    )
    assert "require" in res[0].text.lower()
    res = fns["add_techdraw_view"](
        ctx=None, doc_name="D", page_name="P", view_name="V",
        view_type="orthographic", direction="Oblique",
    )
    assert "Invalid direction" in res[0].text
    assert conn.codes == []


def test_dimension_and_topology_generate_valid_code():
    conn, fns = _make_tools()
    res = fns["get_techdraw_view_topology"](
        ctx=None, doc_name="D", page_name="P", view_name="TopView"
    )
    assert "ok" in res[0].text
    res = fns["add_techdraw_dimension"](
        ctx=None,
        doc_name="D",
        page_name="P",
        view_name="TopView",
        dim_type="DistanceX",
        references=["Vertex2", "Vertex7"],
        format_spec="%.2f in",
    )
    assert "ok" in res[0].text
    assert "DrawViewDimension" in conn.codes[-1]

    res = fns["add_techdraw_dimension"](
        ctx=None, doc_name="D", page_name="P", view_name="V",
        dim_type="Chamfer", references=["Edge1"],
    )
    assert "Invalid dim_type" in res[0].text


def test_validate_page_generates_valid_code():
    conn, fns = _make_tools()
    res = fns["validate_techdraw_page"](ctx=None, doc_name="D", page_name="P")
    assert "ok" in res[0].text
    assert "validate_techdraw_page" in conn.codes[-1]


def test_windows_backslash_path_generates_valid_code():
    """C:\\Users\\... embedded in generated code must not raise a unicode-escape
    SyntaxError (path_utils normalizes to forward slashes)."""
    conn, fns = _make_tools()
    res = fns["export_techdraw_page"](
        ctx=None, doc_name="D", page_name="P",
        export_pdf_path="C:\\Users\\hvksh\\Documents\\out.pdf",
    )
    assert conn.codes, "no code generated"
    assert "C:/Users/hvksh/Documents/out.pdf" in conn.codes[-1]


def test_validator_accepts_iso5457_lowercase_drawing_number():
    conn, fns = _make_tools()
    fns["validate_techdraw_page"](ctx=None, doc_name="D", page_name="P")
    code = conn.codes[-1]
    assert "drawing" in code and "lower()" in code


def test_clearance_tools_generate_valid_code():
    from fastmcp import FastMCP
    import freecad_mcp.clearance_tools as ct
    import asyncio
    mcp = FastMCP("t2"); conn = FakeConn()
    ct.register_clearance_tools(mcp, lambda: conn, lambda r, s, i: r)
    result = mcp.list_tools()
    tools = asyncio.run(result) if asyncio.iscoroutine(result) else result
    fns = {t.name: t.fn for t in (tools if not isinstance(tools, dict) else tools.values())}
    for kind, params in [
        ("box", dict(x=0, y=0, z=0, dx=100, dy=100, dz=100)),
        ("cylinder", dict(p1=[0, 0, 0], axis=[1, 0, 0], radius=50, length=100)),
        ("swing_arc", dict(hinge=[100, 200, 0], radius=1219, start_deg=90, sweep_deg=180, z0=0, z1=1600)),
        ("nec_110_26", dict(face_x=100, face_y=200, width=914, facing=[0, -1], condition=2)),
        ("egress", dict(x0=50, y0=200, length=11000, axis=[1, 0])),
    ]:
        res = fns["clearance_declare"](ctx=None, doc_name="D", name=f"t_{kind}", kind=kind, params=params)
        assert "ok" in res[0].text, (kind, res[0].text)
    res = fns["clearance_declare"](ctx=None, doc_name="D", name="bad", kind="sphere", params={})
    assert "Invalid kind" in res[0].text
    res = fns["clearance_declare"](ctx=None, doc_name="D", name="bad2", kind="box", params={"x": 0})
    assert "missing params" in res[0].text
    res = fns["clearance_gate"](ctx=None, doc_name="D", solid_prefixes=["CSW_", "PUMP"],
                                allow_pairs=[["HoseExtract_P1", "PIPE_"]])
    assert "ok" in res[0].text
    assert "CLEARANCE GATE" in conn.codes[-1]
    res = fns["clearance_gate"](ctx=None, doc_name="D", solid_prefixes=[])
    assert "non-empty" in res[0].text
