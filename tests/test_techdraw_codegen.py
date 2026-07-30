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
