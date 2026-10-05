"""The MCP surface must not expose privileged raw execution — or a bridge to one."""

from __future__ import annotations

import asyncio

from freecad_mcp.server import mcp


def test_raw_python_is_absent_from_routine_tool_inventory():
    names = {tool.name for tool in asyncio.run(mcp.list_tools())}
    assert "execute_code" not in names
    assert {
        "cad_basis_validate", "cad_edit_preview", "cad_edit_apply",
        "cad_clash_gate", "cad_publish",
    } <= names


def test_deployed_surface_matches_adr_c4_allowlist():
    """Generic GUI editing, CSA and TechDraw are retired; FreeCAD is gone.

    The five FreeCAD tools went with the backend. What they verified is done
    natively and on the issue path: a DimensionSpec carries a typed
    MeasurementExpression re-evaluated against the current basis, so a
    dimension is bound to block placements and route waypoints rather than to
    projected 2D topology, and a sheet whose measurements cannot be resolved
    reports UNEVALUATED rather than passing.
    """
    names = {tool.name for tool in asyncio.run(mcp.list_tools())}
    assert names == {'cad_basis_validate', 'resolution_dispatch', 'cad_fitting_export', 'cad_clash_gate', 'cad_edit_apply', 'cad_asset_ingest', 'cad_recipe_export', 'cad_concept_export', 'cad_asset_bind', 'cad_cutting_dxf_validate', 'cad_edit_preview', 'cad_publish', 'cad_asset_inspect', 'resolution_answer', 'cad_workflow_status', 'resolution_doctor', 'cad_library_catalog', 'cad_workflow_submit', 'clearance_declare', 'cad_fabrication_export', 'cad_pattern_export', 'clearance_gate', 'resolution_apply', 'cad_route_reconcile', 'cad_prototype_materialize', 'cad_workflow_cancel', 'cad_review_export', 'resolution_list', 'cad_route_author', 'cad_capability_fingerprint', 'cad_workflow_preflight', 'cad_concept_preview', 'cad_corridor_probe'}



def test_there_is_no_bridge_left_to_authenticate():
    """The optional FreeCAD RPC bridge is gone, not merely unused.

    It used to be reachable with a token and an XML-RPC transport. Asserting
    its absence rather than its authentication is the point: a bridge that is
    configured-but-off is one environment variable from being on.
    """
    from freecad_mcp import server

    for gone in ("FreeCADConnection", "_AuthenticatedTransport",
                 "get_freecad_connection", "execute_code"):
        assert not hasattr(server, gone), gone


def test_startup_connects_to_nothing_and_still_serves_every_tool():
    from freecad_mcp import server

    async def scenario():
        async with server.server_lifespan(server.mcp):
            assert len(await server.mcp.list_tools()) == 33
    asyncio.run(scenario())
